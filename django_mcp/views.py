"""DMCP02 §5–§9 — ViewInvokeRule: derive `view.*` MCP tools from Django views.

This module consumes :class:`~django_mcp.urlwalker.WalkedView` records (one per
URL pattern) and emits :class:`~django_mcp.derivation.ToolDescriptor` objects
for FBVs and CBVs. DRF kinds are skipped — they belong to
``django_mcp.drf.DRFViewSetRule``.

The rule applies INV-DMCP02-3 (class-definition method detection),
INV-DMCP02-4 (require-auth default) and INV-DMCP02-5 (verb-narrowing
precedence) at discovery time. INV-DMCP-1 (async/ORM boundary) is honoured by
wrapping view dispatch in ``asyncio.to_thread`` at handler time.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings
from django.http import HttpRequest, QueryDict

from django_mcp.derivation import (
    DerivationRule,
    PermissionOutcome,
    ToolCallContext,
    ToolDescriptor,
)
from django_mcp.names import RuleFamily, Verb
from django_mcp.names import format as _format_name
from django_mcp.requests import MCP_REQUEST_META_KEY
from django_mcp.urlwalker import ViewKind, WalkedView

logger = logging.getLogger(__name__)


_DEFAULT_HTTP_METHOD_NAMES: frozenset[str] = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
)


# Maps the narrowed verb chosen at discovery time to the HTTP method used when
# synthesising a request. `view.invoke:` can carry any method, so the handler
# inspects the call-time arguments and picks the method itself.
_VERB_TO_METHOD: dict[Verb, str] = {
    Verb.LIST: "GET",
    Verb.RETRIEVE: "GET",
    Verb.CREATE: "POST",
    Verb.UPDATE: "PUT",
    Verb.DELETE: "DELETE",
}


@dataclass(frozen=True, slots=True)
class AuthGate:
    """Structured result of §8.1 auth-gate detection on a view."""

    requires_auth: bool = False
    required_perms: tuple[str, ...] = ()
    has_test_func: bool = False
    detected_via: str = ""
    is_unknown: bool = True
    test_func: Callable[[Any], bool] | None = field(default=None)


def _is_subclass_named(view_class: type, target_name: str) -> bool:
    return any(klass.__name__ == target_name for klass in view_class.__mro__)


def _cbv_method_set(view_class: type) -> set[str]:
    """INV-DMCP02-3: read class-definition methods, NOT runtime dispatch."""
    http_method_names = set(getattr(view_class, "http_method_names", _DEFAULT_HTTP_METHOD_NAMES))
    defined: set[str] = set()
    for klass in view_class.__mro__:
        if klass.__name__ == "View":
            break
        for name in vars(klass):
            if name in http_method_names:
                defined.add(name)
    return defined


def _pick_verb(view_class: type) -> Verb:
    """INV-DMCP02-5: narrower verb wins; precedence is mechanical."""
    methods = _cbv_method_set(view_class)
    if methods == {"get"} and _is_subclass_named(view_class, "ListView"):
        return Verb.LIST
    if methods == {"get"}:
        return Verb.RETRIEVE
    if methods == {"post"}:
        return Verb.CREATE
    if methods == {"put"}:
        return Verb.UPDATE
    if methods == {"delete"}:
        return Verb.DELETE
    return Verb.INVOKE


def _split_dotted(dotted_path: str) -> tuple[str, ...]:
    """Tool-name target wants ASCII identifiers per segment; reuse dotted path."""
    parts = tuple(p for p in dotted_path.split(".") if p)
    if len(parts) < 2:
        return ("view", parts[0] if parts else "anonymous")
    return parts


def _normalize_perm_list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Iterable):
        return tuple(str(v) for v in value)
    return ()


def _detect_cbv_auth_gate(view_class: type) -> AuthGate:
    has_login_required = False
    has_permission_required = False
    has_user_passes_test = False
    perms: tuple[str, ...] = ()
    test_func: Callable[[Any], bool] | None = None
    detected_via_parts: list[str] = []

    for klass in view_class.__mro__:
        name = klass.__name__
        if name == "LoginRequiredMixin":
            has_login_required = True
            detected_via_parts.append("LoginRequiredMixin")
        elif name == "PermissionRequiredMixin":
            has_permission_required = True
            detected_via_parts.append("PermissionRequiredMixin")
        elif name == "UserPassesTestMixin":
            has_user_passes_test = True
            detected_via_parts.append("UserPassesTestMixin")

    if has_permission_required:
        perms = _normalize_perm_list(getattr(view_class, "permission_required", None))

    if has_user_passes_test:
        raw = getattr(view_class, "test_func", None)
        if callable(raw):
            test_func = raw  # type: ignore[assignment]

    if not (has_login_required or has_permission_required or has_user_passes_test):
        return AuthGate(is_unknown=True)

    return AuthGate(
        requires_auth=True,
        required_perms=perms,
        has_test_func=has_user_passes_test,
        detected_via=",".join(detected_via_parts),
        is_unknown=False,
        test_func=test_func,
    )


def _detect_fbv_auth_gate(func: Any) -> AuthGate:
    """Walk Django's decorator chain. Heuristic — see §8.1 caveats in module docs.

    Two signals are checked at each frame in the ``__wrapped__`` chain:

    1. The wrapper's own ``__qualname__``. Bare hand-rolled decorators that do
       NOT use ``functools.wraps`` leave their own qualname intact here, which
       is the cheap match.

    2. The wrapper's ``__closure__`` cells. Django's ``@login_required``
       wraps ``user_passes_test`` and ``functools.wraps`` overwrites the
       outer wrapper's qualname back to the original view's name — so (1)
       misses it. But the decorator injects a ``test_func`` cell whose own
       ``__qualname__`` is ``login_required.<locals>.<lambda>``; similarly
       ``permission_required`` injects a ``has_permissions`` test_func whose
       qualname carries ``permission_required``. The closure walk catches
       these.
    """
    visited: set[int] = set()
    current = func
    has_login = False
    perms: list[str] = []
    detected_via_parts: list[str] = []

    def _add_via(token: str) -> None:
        if token not in detected_via_parts:
            detected_via_parts.append(token)

    def _scan_closure_for_decorator_markers(target: Any) -> None:
        nonlocal has_login
        closure = getattr(target, "__closure__", None) or ()
        freevars = getattr(getattr(target, "__code__", None), "co_freevars", ()) or ()
        for name, cell in zip(freevars, closure, strict=False):
            try:
                contents = cell.cell_contents
            except ValueError:
                continue
            inner_qual = getattr(contents, "__qualname__", "") or ""
            if "login_required" in inner_qual:
                has_login = True
                _add_via("login_required")
            if "permission_required" in inner_qual:
                _add_via("permission_required")
                # ``permission_required`` injects a perm string/sequence into
                # the same closure scope; harvest it.
                for sibling_name, sibling_cell in zip(freevars, closure, strict=False):
                    try:
                        sibling = sibling_cell.cell_contents
                    except ValueError:
                        continue
                    if isinstance(sibling, str) and sibling_name in ("perm", "permission"):
                        perms.append(sibling)
                    elif isinstance(sibling, list | tuple):
                        for item in sibling:
                            if isinstance(item, str):
                                perms.append(item)
            # Recurse one level into nested test_func closures (e.g. when
            # ``user_passes_test`` is the outer signal and ``login_required``
            # is the inner one).
            if name == "test_func" and callable(contents):
                _scan_closure_for_decorator_markers(contents)

    while current is not None and id(current) not in visited:
        visited.add(id(current))
        qual = getattr(current, "__qualname__", "") or ""

        if "login_required" in qual:
            has_login = True
            _add_via("login_required")
        if "permission_required" in qual:
            _add_via("permission_required")
            # Same-frame closure perm harvest (decorator-not-wraps case).
            closure = getattr(current, "__closure__", None) or ()
            for cell in closure:
                try:
                    contents = cell.cell_contents
                except ValueError:
                    continue
                if isinstance(contents, str):
                    perms.append(contents)
                elif isinstance(contents, list | tuple):
                    for item in contents:
                        if isinstance(item, str):
                            perms.append(item)

        # Closure-cell scan catches functools.wraps-masked decorators
        # (Django's @login_required) by examining what was captured.
        _scan_closure_for_decorator_markers(current)

        current = getattr(current, "__wrapped__", None)

    if not (has_login or perms):
        return AuthGate(is_unknown=True)

    return AuthGate(
        requires_auth=True,
        required_perms=tuple(perms),
        detected_via=",".join(detected_via_parts),
        is_unknown=False,
    )


def _detect_auth_gate(view: Any, kind: str) -> AuthGate:
    if kind == ViewKind.CBV and isinstance(view, type):
        return _detect_cbv_auth_gate(view)
    if kind == ViewKind.FBV:
        return _detect_fbv_auth_gate(view)
    return AuthGate(is_unknown=True)


def _build_auth_check(gate: AuthGate) -> Callable[[ToolCallContext], PermissionOutcome]:
    """Map a detected gate to an auth_check per §8.1.

    ``UserPassesTestMixin``'s test_func runs at handler time (§8.1) — the
    class-level auth_check returns ALLOW once the user is authenticated.
    """
    required_perms = gate.required_perms
    requires_auth = gate.requires_auth
    has_test_func = gate.has_test_func

    def check(ctx: ToolCallContext) -> PermissionOutcome:
        if not requires_auth and not required_perms and not has_test_func:
            return PermissionOutcome.ALLOW
        user = ctx.user
        if user is None or not getattr(user, "is_authenticated", False):
            return PermissionOutcome.UNAUTHENTICATED
        for perm in required_perms:
            if not user.has_perm(perm):
                return PermissionOutcome.DENY
        return PermissionOutcome.ALLOW

    return check


def _build_input_schema(path_args_schema: dict[str, Any]) -> dict[str, Any]:
    """Per §5.1 — `{path, query, body}`. `path` is required if non-empty."""
    properties: dict[str, Any] = {
        "path": path_args_schema,
        "query": {"type": "object", "additionalProperties": True},
        "body": {"type": "object", "additionalProperties": True},
    }
    required: list[str] = []
    if path_args_schema.get("properties"):
        required.append("path")
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


def _build_output_schema() -> dict[str, Any]:
    """Per §5.1 — coarse `{status, headers, body}`."""
    return {
        "type": "object",
        "properties": {
            "status": {"type": "integer"},
            "headers": {"type": "object", "additionalProperties": True},
            "body": {},
        },
        "required": ["status"],
        "additionalProperties": False,
    }


def _synthesise_request(
    user: Any,
    *,
    method: str,
    url_path: str,
    query: dict[str, Any] | None,
    body: dict[str, Any] | None,
) -> HttpRequest:
    """View-side counterpart to ``build_admin_request``. Same INV-DMCP-6 marker."""
    request = HttpRequest()
    request.method = method
    request.path = "/" + url_path.lstrip("/")
    request.META[MCP_REQUEST_META_KEY] = "1"
    request.user = user  # type: ignore[assignment]

    get_qd = QueryDict("", mutable=True)
    for key, value in (query or {}).items():
        get_qd[key] = value if isinstance(value, str) else json.dumps(value)
    get_qd._mutable = False  # type: ignore[attr-defined]
    request.GET = get_qd

    if body is not None and method in ("POST", "PUT", "PATCH"):
        post = QueryDict("", mutable=True)
        for key, value in body.items():
            post[key] = value if isinstance(value, str) else json.dumps(value)
        post._mutable = False  # type: ignore[attr-defined]
        request.POST = post
    else:
        request.POST = QueryDict("", mutable=False)

    return request


def _pick_handler_method(verb: Verb, body: dict[str, Any] | None) -> str:
    """Map the discovery-time verb to an HTTP method for the synthesised request."""
    if verb in _VERB_TO_METHOD:
        return _VERB_TO_METHOD[verb]
    return "POST" if body else "GET"


def _decode_response_body(response: Any) -> Any:
    content = getattr(response, "content", b"")
    content_type = ""
    headers = getattr(response, "headers", None)
    if headers is not None:
        content_type = headers.get("Content-Type") or headers.get("content-type") or ""
    if not content_type:
        content_type = getattr(response, "_content_type_for_repr", "") or ""

    if isinstance(content, bytes):
        try:
            decoded = content.decode("utf-8")
        except UnicodeDecodeError:
            return content
    else:
        decoded = content

    if content_type.startswith("application/json"):
        try:
            return json.loads(decoded)
        except (TypeError, ValueError):
            return decoded
    return decoded


def _response_headers(response: Any) -> dict[str, Any]:
    headers = getattr(response, "headers", None)
    if headers is None:
        return {}
    try:
        return {str(k): str(v) for k, v in headers.items()}
    except Exception:
        return {}


def _build_handler(
    view: Any,
    *,
    kind: str,
    url_path: str,
    verb: Verb,
    gate: AuthGate,
) -> Callable[[ToolCallContext], Any]:
    test_func = gate.test_func
    has_test_func = gate.has_test_func

    async def handler(ctx: ToolCallContext) -> dict[str, Any]:
        args = ctx.arguments or {}
        path_args = args.get("path") or {}
        query = args.get("query") or {}
        body = args.get("body")
        method = _pick_handler_method(verb, body if isinstance(body, dict) else None)

        def _run() -> dict[str, Any]:
            request = _synthesise_request(
                ctx.user,
                method=method,
                url_path=url_path,
                query=query if isinstance(query, dict) else {},
                body=body if isinstance(body, dict) else None,
            )
            if kind == ViewKind.CBV and isinstance(view, type):
                instance = view()
                instance.request = request
                instance.args = ()
                instance.kwargs = dict(path_args) if isinstance(path_args, dict) else {}
                if has_test_func and test_func is not None and not test_func(instance):
                    from django.core.exceptions import PermissionDenied

                    raise PermissionDenied("UserPassesTestMixin.test_func returned False")
                response = instance.dispatch(
                    request, **(path_args if isinstance(path_args, dict) else {})
                )
            else:
                response = view(request, **(path_args if isinstance(path_args, dict) else {}))

            status = int(getattr(response, "status_code", 200))
            return {
                "status": status,
                "headers": _response_headers(response),
                "body": _decode_response_body(response),
            }

        return await asyncio.to_thread(_run)

    return handler


class ViewInvokeRule(DerivationRule):
    """Emit `view.*` tools from FBV / CBV ``WalkedView`` records (§5.1, §5.2)."""

    family = RuleFamily.VIEW

    @classmethod
    def emit(cls, source: WalkedView) -> Iterable[ToolDescriptor]:
        if source.kind in (ViewKind.DRF_VIEWSET, ViewKind.DRF_APIVIEW):
            return
        if source.kind not in (ViewKind.FBV, ViewKind.CBV):
            return

        view = source.view
        gate = _detect_auth_gate(view, source.kind)
        require_auth = bool(getattr(settings, "DJANGO_MCP_REQUIRE_AUTH", True))

        if gate.is_unknown:
            if require_auth:
                logger.warning(
                    "view %s has no detectable auth gate; skipped per "
                    "INV-DMCP02-4 (DJANGO_MCP_REQUIRE_AUTH=True)",
                    source.dotted_path,
                )
                return
            logger.warning(
                "view %s has no detectable auth gate; emitted as publicly-callable "
                "(DJANGO_MCP_REQUIRE_AUTH=False)",
                source.dotted_path,
            )

        if source.kind == ViewKind.CBV and isinstance(view, type):
            verb = _pick_verb(view)
        else:
            verb = Verb.INVOKE

        target = _split_dotted(source.dotted_path)
        tool_name = _format_name(RuleFamily.VIEW, verb, target)

        input_schema = _build_input_schema(source.path_args_schema)
        output_schema = _build_output_schema()
        handler = _build_handler(
            view,
            kind=source.kind,
            url_path=source.url_path,
            verb=verb,
            gate=gate,
        )
        auth_check = _build_auth_check(gate)

        yield ToolDescriptor(
            tool_name=tool_name,
            description=_view_description(view, source.dotted_path),
            input_schema=input_schema,
            output_schema=output_schema,
            handler=handler,
            auth_check=auth_check,
            origin=tool_name,
        )


def _view_description(view: Any, dotted_path: str) -> str:
    """First non-blank docstring line if available, else a templated fallback."""
    doc = getattr(view, "__doc__", None)
    if doc:
        for line in doc.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped
    return f"Invoke {dotted_path}."


def emit_for_walked_views(views: Iterable[WalkedView]) -> Iterable[ToolDescriptor]:
    """Apply ``ViewInvokeRule`` to every FBV/CBV record. DRF kinds are skipped."""
    for walked in views:
        if walked.kind in (ViewKind.FBV, ViewKind.CBV):
            yield from ViewInvokeRule.emit(walked)
