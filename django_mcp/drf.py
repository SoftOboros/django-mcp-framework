"""DMCP02 §5.3 / §6 / §8.2 / §10.1 — DRF ViewSet/APIView → MCP tool derivation.

Operates on the ``WalkedView`` stream produced by ``django_mcp.urlwalker``.
For each DRF ViewSet, list-pattern and detail-pattern records are coalesced
per ``dotted_path`` and projected onto the §5.3 verb table; PUT and PATCH
collapse to a single ``view.update:`` tool whose input schema marks every
field optional (§10.1, INV-DMCP02 §10.1).

Import-guarded per INV-DMCP02-8: the module imports without ``rest_framework``
installed and ``emit_for_drf_views`` yields nothing in that mode.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from typing import Any

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied

from django_mcp.derivation import (
    DerivationRule,
    PermissionOutcome,
    ToolCallContext,
    ToolDescriptor,
)
from django_mcp.names import RuleFamily, Verb
from django_mcp.names import format as _format_name
from django_mcp.requests import build_admin_request
from django_mcp.schemas import drf_serializer_to_json_schema
from django_mcp.urlwalker import ViewKind, WalkedView

logger = logging.getLogger(__name__)

try:
    from rest_framework.views import APIView
    from rest_framework.viewsets import ViewSetMixin

    DRF_AVAILABLE = True
except ImportError:
    DRF_AVAILABLE = False
    ViewSetMixin = None  # type: ignore[assignment,misc]
    APIView = None  # type: ignore[assignment,misc]


_PERMISSIVE_OBJECT: dict[str, Any] = {"type": "object", "additionalProperties": True}
_PERMISSIVE_ANY: dict[str, Any] = {}

# CRUD handler names per §5.3 — the canonical ViewSet method set.
_CRUD_HANDLERS: frozenset[str] = frozenset(
    {"list", "retrieve", "create", "update", "partial_update", "destroy"}
)

# DRF handler-method name → verb used to synthesise the request (mapped through
# ``build_admin_request``'s ``_VERB_METHOD`` table). ``partial_update`` collapses
# into ``update`` per §10.1.
_HANDLER_VERB: dict[str, str] = {
    "list": "list",
    "retrieve": "retrieve",
    "create": "create",
    "update": "update",
    "partial_update": "update",
    "destroy": "delete",
}


def _split_dotted(dotted_path: str) -> tuple[str, ...]:
    """Split a dotted path into the target tuple expected by the name grammar.

    Per the DMCP-00 §5 2026-05-23 amendment, leading-underscore components
    (e.g. ``__main__``) are now legal; this function only synthesises a
    leading component when the path collapses to a single token (the grammar
    still requires ``dotted_target >= 2`` components).
    """
    parts = tuple(p for p in dotted_path.split(".") if p)
    if len(parts) < 2:
        return ("mod", parts[0] if parts else "unknown")
    return parts


def _pk_schema_for_view(view_class: Any) -> dict[str, Any] | None:
    """Best-effort pk JSON Schema for a ViewSet via its declared queryset."""
    queryset = getattr(view_class, "queryset", None)
    model = getattr(queryset, "model", None) if queryset is not None else None
    if model is None:
        return None
    from django_mcp.schemas import field_to_json_schema_for_model_pk

    try:
        return field_to_json_schema_for_model_pk(model)
    except Exception as exc:
        logger.warning(
            "drf: pk schema derivation failed for %s: %s; falling back to string",
            getattr(view_class, "__qualname__", view_class),
            exc,
        )
        return {"type": "string"}


def _first_doc_line(obj: Any) -> str | None:
    doc = getattr(obj, "__doc__", None)
    if not doc:
        return None
    for line in doc.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def _viewset_description(view_class: Any, verb_label: str, target: tuple[str, ...]) -> str:
    """First doc line if available, else templated per-verb fallback."""
    doc = _first_doc_line(view_class)
    if doc:
        return doc
    return f"{verb_label} {'.'.join(target)}."


def _action_description(action_method: Any, view_class: Any, target: tuple[str, ...]) -> str:
    declared = getattr(action_method, "description", None)
    if declared:
        return str(declared)
    doc = _first_doc_line(action_method)
    if doc:
        return doc
    name = getattr(action_method, "url_path", None) or action_method.__name__
    return f"DRF action '{name}' on {'.'.join(target)}."


def _resolve_serializer_class(view_class: Any) -> Any | None:
    """Return the serializer class declared on a ViewSet, or None.

    Tries the class attribute first; falls back to ``get_serializer_class()``
    on a bare instance only when the attribute is missing. ``get_serializer_class``
    that touches ``self.request`` raises and we log + return None — callers
    degrade to a permissive object schema.
    """
    declared = getattr(view_class, "serializer_class", None)
    if declared is not None:
        return declared

    try:
        instance = view_class()
        return instance.get_serializer_class()
    except Exception as exc:
        logger.warning(
            "drf: get_serializer_class() failed for %s without a request: %s; "
            "falling back to permissive object schema",
            getattr(view_class, "__qualname__", view_class),
            exc,
        )
        return None


def _serializer_input_schema(view_class: Any) -> dict[str, Any]:
    serializer_class = _resolve_serializer_class(view_class)
    if serializer_class is None:
        return dict(_PERMISSIVE_OBJECT)
    try:
        return drf_serializer_to_json_schema(serializer_class)
    except Exception as exc:
        logger.warning(
            "drf: drf_serializer_to_json_schema(%r) failed: %s; falling back to permissive",
            serializer_class,
            exc,
        )
        return dict(_PERMISSIVE_OBJECT)


def _patchify(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``schema`` with ``required`` cleared (§10.1 PATCH semantics)."""
    out = dict(schema)
    if "required" in out:
        out["required"] = []
    return out


def _list_output_schema(item_schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "results": {"type": "array", "items": item_schema},
            "count": {"type": "integer"},
            "page": {"type": "integer"},
            "page_size": {"type": "integer"},
        },
        "required": ["results", "count", "page", "page_size"],
        "additionalProperties": False,
    }


def _delete_output_schema(pk_schema: dict[str, Any] | None) -> dict[str, Any]:
    props: dict[str, Any] = {"deleted": {"type": "boolean", "const": True}}
    if pk_schema is not None:
        props["pk"] = pk_schema
    else:
        props["pk"] = {}
    return {
        "type": "object",
        "properties": props,
        "required": ["deleted", "pk"],
        "additionalProperties": False,
    }


def _wrap_with_path(
    body_schema: dict[str, Any], pk_schema: dict[str, Any] | None
) -> dict[str, Any]:
    """Build the canonical {path, body} input shape used by detail-bound tools."""
    path_props: dict[str, Any] = {}
    path_required: list[str] = []
    if pk_schema is not None:
        path_props["pk"] = pk_schema
        path_required.append("pk")
    return {
        "type": "object",
        "properties": {
            "path": {
                "type": "object",
                "properties": path_props,
                "required": path_required,
                "additionalProperties": True,
            },
            "body": body_schema,
        },
        "required": ["path", "body"] if pk_schema is not None else ["body"],
        "additionalProperties": False,
    }


def _build_view_instance(
    view_class: Any, *, request: Any, path_args: dict[str, Any], action: str | None = None
) -> Any:
    """Construct a fresh ViewSet/APIView instance with synthesised state bound."""
    instance = view_class()
    instance.request = request
    instance.kwargs = dict(path_args)
    instance.args = ()
    if hasattr(instance, "format_kwarg"):
        instance.format_kwarg = None
    if action is not None:
        instance.action = action
    return instance


def _drf_permission_check(view_class: Any, *, verb: str, action: str | None = None):
    """Build a DMCP-00 auth_check honoring §8.2: only ``permission_classes``."""
    permission_classes = list(getattr(view_class, "permission_classes", []) or [])

    def check(ctx: ToolCallContext) -> PermissionOutcome:
        user = ctx.user
        if user is None or not getattr(user, "is_authenticated", False):
            return PermissionOutcome.UNAUTHENTICATED
        if not permission_classes:
            return PermissionOutcome.ALLOW
        request = build_admin_request(user, verb=verb)
        path_args = (ctx.arguments or {}).get("path") or {}
        view_instance = _build_view_instance(
            view_class, request=request, path_args=path_args, action=action
        )
        for permission_cls in permission_classes:
            try:
                if not permission_cls().has_permission(request, view_instance):
                    return PermissionOutcome.DENY
            except Exception as exc:
                logger.warning(
                    "drf: permission %s.has_permission raised: %s; denying",
                    permission_cls.__name__,
                    exc,
                )
                return PermissionOutcome.DENY
        return PermissionOutcome.ALLOW

    return check


def _enforce_permissions(view_instance: Any, request: Any, *, obj: Any = None) -> None:
    """Handler-time permission re-check (per-object included). Raises on DENY."""
    permission_classes = list(getattr(view_instance.__class__, "permission_classes", []) or [])
    for permission_cls in permission_classes:
        permission = permission_cls()
        if not permission.has_permission(request, view_instance):
            raise DjangoPermissionDenied(
                f"DRF permission {permission_cls.__name__}.has_permission denied"
            )
        if obj is not None and not permission.has_object_permission(request, view_instance, obj):
            raise DjangoPermissionDenied(
                f"DRF permission {permission_cls.__name__}.has_object_permission denied"
            )


def _validate(serializer: Any) -> None:
    if not serializer.is_valid():
        raise ValueError(f"validation failed: {serializer.errors!r}")


def _list_handler(view_class: Any):
    async def handler(ctx: ToolCallContext) -> dict[str, Any]:
        args = ctx.arguments or {}
        page = max(1, int(args.get("page") or 1))
        page_size = max(1, min(1000, int(args.get("page_size") or 25)))
        ordering = args.get("ordering") or ""

        def _run() -> dict[str, Any]:
            request = build_admin_request(ctx.user, verb="list")
            instance = _build_view_instance(
                view_class, request=request, path_args={}, action="list"
            )
            _enforce_permissions(instance, request)

            queryset = instance.get_queryset() if hasattr(instance, "get_queryset") else None
            if queryset is None:
                queryset = getattr(view_class, "queryset", None)
            if queryset is None:
                # No queryset declared — surface an empty list rather than crashing.
                return {
                    "results": [],
                    "count": 0,
                    "page": page,
                    "page_size": page_size,
                }
            if ordering:
                queryset = queryset.order_by(*[o.strip() for o in ordering.split(",") if o.strip()])

            count = queryset.count()
            start = (page - 1) * page_size
            page_qs = queryset[start : start + page_size]

            serializer_class = _resolve_serializer_class(view_class)
            if serializer_class is None:
                results: list[Any] = [getattr(obj, "pk", None) for obj in page_qs]
            else:
                results = serializer_class(page_qs, many=True).data
            return {
                "results": list(results),
                "count": count,
                "page": page,
                "page_size": page_size,
            }

        return await asyncio.to_thread(_run)

    return handler


def _retrieve_handler(view_class: Any):
    async def handler(ctx: ToolCallContext) -> dict[str, Any]:
        path_args = (ctx.arguments or {}).get("path") or {}

        def _run() -> dict[str, Any]:
            request = build_admin_request(ctx.user, verb="retrieve")
            instance = _build_view_instance(
                view_class, request=request, path_args=path_args, action="retrieve"
            )
            _enforce_permissions(instance, request)
            obj = instance.get_object() if hasattr(instance, "get_object") else None
            if obj is None:
                raise LookupError(f"retrieve target not found for {view_class.__qualname__}")
            _enforce_permissions(instance, request, obj=obj)
            serializer_class = _resolve_serializer_class(view_class)
            if serializer_class is None:
                return {"object": getattr(obj, "pk", None)}
            return {"object": serializer_class(obj).data}

        return await asyncio.to_thread(_run)

    return handler


def _create_handler(view_class: Any):
    async def handler(ctx: ToolCallContext) -> dict[str, Any]:
        args = ctx.arguments or {}
        body = args.get("body") if "body" in args else args

        def _run() -> dict[str, Any]:
            request = build_admin_request(ctx.user, verb="create", body=body or {})
            instance = _build_view_instance(
                view_class, request=request, path_args={}, action="create"
            )
            _enforce_permissions(instance, request)
            serializer_class = _resolve_serializer_class(view_class)
            if serializer_class is None:
                return {"object": body}
            serializer = serializer_class(data=body or {})
            _validate(serializer)
            serializer.save()
            return {"object": serializer.data}

        return await asyncio.to_thread(_run)

    return handler


def _update_handler(view_class: Any):
    async def handler(ctx: ToolCallContext) -> dict[str, Any]:
        args = ctx.arguments or {}
        path_args = args.get("path") or {}
        body = args.get("body") or {}

        def _run() -> dict[str, Any]:
            request = build_admin_request(ctx.user, verb="update", body=body)
            instance = _build_view_instance(
                view_class,
                request=request,
                path_args=path_args,
                action="partial_update",
            )
            _enforce_permissions(instance, request)
            obj = instance.get_object() if hasattr(instance, "get_object") else None
            if obj is None:
                raise LookupError(f"update target not found for {view_class.__qualname__}")
            _enforce_permissions(instance, request, obj=obj)
            serializer_class = _resolve_serializer_class(view_class)
            if serializer_class is None:
                return {"object": body}
            # §10.1 — partial=True so PUT and PATCH share one tool.
            serializer = serializer_class(obj, data=body, partial=True)
            _validate(serializer)
            serializer.save()
            return {"object": serializer.data}

        return await asyncio.to_thread(_run)

    return handler


def _delete_handler(view_class: Any):
    async def handler(ctx: ToolCallContext) -> dict[str, Any]:
        path_args = (ctx.arguments or {}).get("path") or {}

        def _run() -> dict[str, Any]:
            request = build_admin_request(ctx.user, verb="delete")
            instance = _build_view_instance(
                view_class, request=request, path_args=path_args, action="destroy"
            )
            _enforce_permissions(instance, request)
            obj = instance.get_object() if hasattr(instance, "get_object") else None
            if obj is None:
                raise LookupError(f"delete target not found for {view_class.__qualname__}")
            _enforce_permissions(instance, request, obj=obj)
            pk = getattr(obj, "pk", None)
            instance.perform_destroy(obj) if hasattr(instance, "perform_destroy") else obj.delete()
            return {"deleted": True, "pk": pk}

        return await asyncio.to_thread(_run)

    return handler


def _action_handler(view_class: Any, method_name: str, *, detail: bool):
    async def handler(ctx: ToolCallContext) -> dict[str, Any]:
        args = ctx.arguments or {}
        path_args = args.get("path") or {}
        body = args.get("body") if "body" in args else args

        def _run() -> Any:
            request = build_admin_request(ctx.user, verb="action", body=body or {})
            instance = _build_view_instance(
                view_class,
                request=request,
                path_args=path_args,
                action=method_name,
            )
            _enforce_permissions(instance, request)
            if detail and hasattr(instance, "get_object"):
                obj = instance.get_object()
                _enforce_permissions(instance, request, obj=obj)

            result = getattr(instance, method_name)(request, **path_args)
            # DRF actions typically return a ``Response`` instance whose ``.data``
            # carries the payload. Surface that when present; otherwise return
            # the raw value.
            data = getattr(result, "data", result)
            return data if isinstance(data, dict | list) else {"result": data}

        return await asyncio.to_thread(_run)

    return handler


def _apiview_handler(view_class: Any):
    async def handler(ctx: ToolCallContext) -> dict[str, Any]:
        args = ctx.arguments or {}
        path_args = args.get("path") or {}
        body = args.get("body") if "body" in args else args
        method = (args.get("method") or "get").lower()

        def _run() -> Any:
            request = build_admin_request(
                ctx.user,
                verb="action" if method in ("post", "put", "patch", "delete") else "list",
                body=body or {} if method in ("post", "put") else None,
            )
            instance = _build_view_instance(view_class, request=request, path_args=path_args)
            _enforce_permissions(instance, request)
            handler_fn = getattr(instance, method, None)
            if handler_fn is None:
                raise LookupError(f"APIView {view_class.__qualname__} has no {method!r} method")
            result = handler_fn(request, **path_args)
            data = getattr(result, "data", result)
            return data if isinstance(data, dict | list) else {"result": data}

        return await asyncio.to_thread(_run)

    return handler


def _collect_actions(view_class: Any) -> list[Any]:
    """Return the @action-decorated method objects on ``view_class``."""
    getter = getattr(view_class, "get_extra_actions", None)
    if getter is None:
        return []
    try:
        return list(getter())
    except Exception as exc:
        logger.warning(
            "drf: %s.get_extra_actions() raised: %s; skipping @action discovery",
            getattr(view_class, "__qualname__", view_class),
            exc,
        )
        return []


def _action_input_schema(detail: bool, pk_schema: dict[str, Any] | None) -> dict[str, Any]:
    path_props: dict[str, Any] = {}
    path_required: list[str] = []
    if detail and pk_schema is not None:
        path_props["pk"] = pk_schema
        path_required.append("pk")
    return {
        "type": "object",
        "properties": {
            "path": {
                "type": "object",
                "properties": path_props,
                "required": path_required,
                "additionalProperties": True,
            },
            "body": dict(_PERMISSIVE_OBJECT),
        },
        "required": ["path", "body"] if detail else ["body"],
        "additionalProperties": False,
    }


class DRFViewSetRule(DerivationRule):
    """Emit MCP tools for DRF ViewSets per §5.3 / §10.1 (PUT+PATCH collapse)."""

    family = RuleFamily.VIEW

    @classmethod
    def emit(cls, source: Iterable[WalkedView]) -> Iterable[ToolDescriptor]:
        if not DRF_AVAILABLE:
            return

        grouped: dict[str, list[WalkedView]] = {}
        for walked in source:
            if walked.kind != ViewKind.DRF_VIEWSET:
                continue
            grouped.setdefault(walked.dotted_path, []).append(walked)

        for dotted_path, records in grouped.items():
            yield from cls._emit_for_viewset(dotted_path, records)

    @classmethod
    def _emit_for_viewset(
        cls, dotted_path: str, records: list[WalkedView]
    ) -> Iterable[ToolDescriptor]:
        view_class = records[0].view
        target = _split_dotted(dotted_path)

        handlers: set[str] = set()
        for walked in records:
            actions = getattr(walked.callback, "actions", None) or {}
            for handler_name in actions.values():
                handlers.add(handler_name)

        pk_schema = _pk_schema_for_view(view_class)
        item_schema = _serializer_input_schema(view_class)

        if "list" in handlers:
            yield cls._emit_list(view_class, target, item_schema)
        if "retrieve" in handlers:
            yield cls._emit_retrieve(view_class, target, item_schema, pk_schema)
        if "create" in handlers:
            yield cls._emit_create(view_class, target, item_schema)
        if "update" in handlers or "partial_update" in handlers:
            yield cls._emit_update(view_class, target, item_schema, pk_schema)
        if "destroy" in handlers:
            yield cls._emit_delete(view_class, target, pk_schema)

        # @action-decorated methods. Coalescing here means we discover them
        # once per ViewSet class, not per URL pattern — multiple list/detail
        # patterns would otherwise emit duplicate ``view.invoke:`` tools.
        for action_method in _collect_actions(view_class):
            yield cls._emit_action(view_class, target, action_method)

    @staticmethod
    def _emit_list(
        view_class: Any, target: tuple[str, ...], item_schema: dict[str, Any]
    ) -> ToolDescriptor:
        tool_name = _format_name(RuleFamily.VIEW, Verb.LIST, target)
        input_schema = {
            "type": "object",
            "properties": {
                "ordering": {"type": "string"},
                "page": {"type": "integer", "minimum": 1, "default": 1},
                "page_size": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1000,
                    "default": 25,
                },
            },
            "additionalProperties": False,
        }
        return ToolDescriptor(
            tool_name=tool_name,
            description=_viewset_description(view_class, "List", target),
            input_schema=input_schema,
            output_schema=_list_output_schema(item_schema),
            handler=_list_handler(view_class),
            auth_check=_drf_permission_check(view_class, verb="list", action="list"),
            origin=tool_name,
        )

    @staticmethod
    def _emit_retrieve(
        view_class: Any,
        target: tuple[str, ...],
        item_schema: dict[str, Any],
        pk_schema: dict[str, Any] | None,
    ) -> ToolDescriptor:
        tool_name = _format_name(RuleFamily.VIEW, Verb.RETRIEVE, target)
        path_props: dict[str, Any] = {}
        path_required: list[str] = []
        if pk_schema is not None:
            path_props["pk"] = pk_schema
            path_required.append("pk")
        input_schema = {
            "type": "object",
            "properties": {
                "path": {
                    "type": "object",
                    "properties": path_props,
                    "required": path_required,
                    "additionalProperties": True,
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        }
        output_schema = {
            "type": "object",
            "properties": {"object": item_schema},
            "required": ["object"],
            "additionalProperties": False,
        }
        return ToolDescriptor(
            tool_name=tool_name,
            description=_viewset_description(view_class, "Retrieve a single object via", target),
            input_schema=input_schema,
            output_schema=output_schema,
            handler=_retrieve_handler(view_class),
            auth_check=_drf_permission_check(view_class, verb="retrieve", action="retrieve"),
            origin=tool_name,
        )

    @staticmethod
    def _emit_create(
        view_class: Any, target: tuple[str, ...], item_schema: dict[str, Any]
    ) -> ToolDescriptor:
        tool_name = _format_name(RuleFamily.VIEW, Verb.CREATE, target)
        input_schema = {
            "type": "object",
            "properties": {"body": item_schema},
            "required": ["body"],
            "additionalProperties": False,
        }
        output_schema = {
            "type": "object",
            "properties": {"object": item_schema},
            "required": ["object"],
            "additionalProperties": False,
        }
        return ToolDescriptor(
            tool_name=tool_name,
            description=_viewset_description(view_class, "Create via", target),
            input_schema=input_schema,
            output_schema=output_schema,
            handler=_create_handler(view_class),
            auth_check=_drf_permission_check(view_class, verb="create", action="create"),
            origin=tool_name,
        )

    @staticmethod
    def _emit_update(
        view_class: Any,
        target: tuple[str, ...],
        item_schema: dict[str, Any],
        pk_schema: dict[str, Any] | None,
    ) -> ToolDescriptor:
        tool_name = _format_name(RuleFamily.VIEW, Verb.UPDATE, target)
        patchy = _patchify(item_schema)
        input_schema = _wrap_with_path(patchy, pk_schema)
        output_schema = {
            "type": "object",
            "properties": {"object": item_schema},
            "required": ["object"],
            "additionalProperties": False,
        }
        return ToolDescriptor(
            tool_name=tool_name,
            description=_viewset_description(view_class, "Update (PATCH-style) via", target),
            input_schema=input_schema,
            output_schema=output_schema,
            handler=_update_handler(view_class),
            auth_check=_drf_permission_check(view_class, verb="update", action="partial_update"),
            origin=tool_name,
        )

    @staticmethod
    def _emit_delete(
        view_class: Any, target: tuple[str, ...], pk_schema: dict[str, Any] | None
    ) -> ToolDescriptor:
        tool_name = _format_name(RuleFamily.VIEW, Verb.DELETE, target)
        path_props: dict[str, Any] = {}
        path_required: list[str] = []
        if pk_schema is not None:
            path_props["pk"] = pk_schema
            path_required.append("pk")
        input_schema = {
            "type": "object",
            "properties": {
                "path": {
                    "type": "object",
                    "properties": path_props,
                    "required": path_required,
                    "additionalProperties": True,
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        }
        return ToolDescriptor(
            tool_name=tool_name,
            description=_viewset_description(view_class, "Delete via", target),
            input_schema=input_schema,
            output_schema=_delete_output_schema(pk_schema),
            handler=_delete_handler(view_class),
            auth_check=_drf_permission_check(view_class, verb="delete", action="destroy"),
            origin=tool_name,
        )

    @staticmethod
    def _emit_action(
        view_class: Any, target: tuple[str, ...], action_method: Any
    ) -> ToolDescriptor:
        action_name = getattr(action_method, "url_path", None) or action_method.__name__
        detail = bool(getattr(action_method, "detail", False))
        pk_schema = _pk_schema_for_view(view_class) if detail else None
        tool_name = _format_name(RuleFamily.VIEW, Verb.INVOKE, (*target, action_name))
        return ToolDescriptor(
            tool_name=tool_name,
            description=_action_description(action_method, view_class, target),
            input_schema=_action_input_schema(detail, pk_schema),
            output_schema=dict(_PERMISSIVE_ANY),
            handler=_action_handler(view_class, action_method.__name__, detail=detail),
            auth_check=_drf_permission_check(
                view_class, verb="action", action=action_method.__name__
            ),
            origin=tool_name,
        )


def _emit_apiview(walked: WalkedView) -> ToolDescriptor:
    view_class = walked.view
    target = _split_dotted(walked.dotted_path)
    tool_name = _format_name(RuleFamily.VIEW, Verb.INVOKE, target)
    input_schema = {
        "type": "object",
        "properties": {
            "method": {
                "type": "string",
                "enum": ["get", "post", "put", "patch", "delete", "head", "options"],
                "default": "get",
            },
            "path": dict(_PERMISSIVE_OBJECT),
            "body": dict(_PERMISSIVE_OBJECT),
        },
        "additionalProperties": False,
    }
    apiview_target = _split_dotted(walked.dotted_path)
    return ToolDescriptor(
        tool_name=tool_name,
        description=_viewset_description(view_class, "Invoke APIView", apiview_target),
        input_schema=input_schema,
        output_schema=dict(_PERMISSIVE_ANY),
        handler=_apiview_handler(view_class),
        auth_check=_drf_permission_check(view_class, verb="action"),
        origin=tool_name,
    )


def emit_for_drf_views(views: Iterable[WalkedView]) -> Iterable[ToolDescriptor]:
    """Apply DMCP-02 DRF rules across the walked-view stream.

    Coalesces DRF_VIEWSET records by ``dotted_path`` (so list+detail patterns
    of one ViewSet emit one tool set) and yields one ``view.invoke:`` per
    DRF_APIVIEW. No-op when DRF is not importable (INV-DMCP02-8).
    """
    if not DRF_AVAILABLE:
        return

    materialised = list(views)
    yield from DRFViewSetRule.emit(materialised)

    seen_apiviews: set[str] = set()
    for walked in materialised:
        if walked.kind != ViewKind.DRF_APIVIEW:
            continue
        if walked.dotted_path in seen_apiviews:
            continue
        seen_apiviews.add(walked.dotted_path)
        yield _emit_apiview(walked)


__all__ = (
    "DRF_AVAILABLE",
    "DRFViewSetRule",
    "emit_for_drf_views",
)
