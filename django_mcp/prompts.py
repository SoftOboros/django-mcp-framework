"""DMCP-03 §5.4 / §5.5 — prompt derivation rules.

Two rules:

- :class:`AdminActionPromptRule` walks a ``ModelAdmin`` and emits one
  ``prompt.admin.<app>.<Model>.<action_name>`` per ``@admin.action`` discovered.
  The prompt body is a single user message instructing the assistant to invoke
  the corresponding ``admin.action:<app>.<Model>.<action_name>`` tool with the
  caller-supplied ``pks`` (INV-DMCP03-7 — namespace parity with the tool).
- :class:`UserPromptRule` ingests one ``DJANGO_MCP_PROMPTS`` entry (§10.5) and
  emits a ``prompt.user.<slug>`` PromptDescriptor whose ``render_handler`` does
  safe ``{placeholder}`` substitution against the caller's argument binding.

INV-DMCP03-5 (templates not computation): ``render_handler`` is synchronous,
returns a list of MCP message dicts, NEVER executes any tool, and NEVER raises
on missing arguments — unknown placeholders are left as literal ``{name}``
text via the ``_SafeDict`` substitution adapter.

INV-DMCP03-9 (per-user-stable ``prompts/list``): every PromptDescriptor carries
an ``auth_check`` for ``prompts/get`` gating, but the listing layer (DMCP-04)
MUST NOT use that callable to filter the per-user prompt list. The list is
stable across callers; per-user authorisation is decided at render time.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Final

from django.contrib.admin import ModelAdmin
from django.core.exceptions import ImproperlyConfigured

from django_mcp.derivation import (
    PermissionOutcome,
    PromptArgument,
    PromptDerivationRule,
    PromptDescriptor,
    ToolCallContext,
)

logger = logging.getLogger(__name__)


_LEAF_CHARS: Final = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_")
_USER_PROMPT_ALLOWED_KEYS: Final = frozenset(
    {"name", "description", "arguments", "body", "permission"}
)
_USER_PROMPT_REQUIRED_KEYS: Final = ("name", "description", "arguments", "body")
_DEFAULT_PROMPT_PERMISSION: Final = "authenticated"


@dataclass(frozen=True, slots=True)
class UserPromptEntry:
    """Parsed ``DJANGO_MCP_PROMPTS`` entry (DMCP-03 §10.5).

    ``permission`` is ``None`` when the entry did not declare one — semantically
    equivalent to the spec's ``"authenticated"`` default; :class:`UserPromptRule`
    treats ``None`` as "any authenticated user".
    """

    name: str
    description: str
    arguments: tuple[PromptArgument, ...]
    body: str
    permission: str | None = None


def _validate_slug(value: str) -> None:
    if not value:
        raise ImproperlyConfigured("DJANGO_MCP_PROMPTS entry 'name' is empty")
    if not all(ord(ch) < 128 for ch in value):
        raise ImproperlyConfigured(f"DJANGO_MCP_PROMPTS entry 'name' must be ASCII: {value!r}")
    for offset, ch in enumerate(value):
        if ch not in _LEAF_CHARS:
            raise ImproperlyConfigured(
                f"DJANGO_MCP_PROMPTS entry 'name' contains illegal character "
                f"at offset {offset} (allowed: ALPHA / DIGIT / '_'): {value!r}"
            )


def _require_str(entry: dict[str, Any], key: str) -> str:
    if key not in entry:
        raise ImproperlyConfigured(f"DJANGO_MCP_PROMPTS entry is missing required key {key!r}")
    raw = entry[key]
    if not isinstance(raw, str):
        raise ImproperlyConfigured(
            f"DJANGO_MCP_PROMPTS entry key {key!r} must be a string, got {type(raw).__name__}"
        )
    return raw


def _parse_arguments(raw_arguments: Any) -> tuple[PromptArgument, ...]:
    if not isinstance(raw_arguments, list | tuple):
        raise ImproperlyConfigured(
            "DJANGO_MCP_PROMPTS entry 'arguments' must be a list of dicts, "
            f"got {type(raw_arguments).__name__}"
        )
    parsed: list[PromptArgument] = []
    for index, item in enumerate(raw_arguments):
        if not isinstance(item, dict):
            raise ImproperlyConfigured(
                f"DJANGO_MCP_PROMPTS entry 'arguments[{index}]' must be a dict, "
                f"got {type(item).__name__}"
            )
        unknown_arg_keys = set(item) - {"name", "description", "required"}
        if unknown_arg_keys:
            raise ImproperlyConfigured(
                f"DJANGO_MCP_PROMPTS entry 'arguments[{index}]' has unknown keys: "
                f"{sorted(unknown_arg_keys)}"
            )
        if "name" not in item or not isinstance(item["name"], str) or not item["name"]:
            raise ImproperlyConfigured(
                f"DJANGO_MCP_PROMPTS entry 'arguments[{index}].name' is required and must be a "
                "non-empty string"
            )
        description = item.get("description", "")
        if not isinstance(description, str):
            raise ImproperlyConfigured(
                f"DJANGO_MCP_PROMPTS entry 'arguments[{index}].description' must be a string"
            )
        required = item.get("required", True)
        if not isinstance(required, bool):
            raise ImproperlyConfigured(
                f"DJANGO_MCP_PROMPTS entry 'arguments[{index}].required' must be a bool"
            )
        parsed.append(PromptArgument(name=item["name"], description=description, required=required))
    return tuple(parsed)


def parse_user_prompt_entry(entry: dict[str, Any]) -> UserPromptEntry:
    """Validate one ``DJANGO_MCP_PROMPTS`` entry (DMCP-03 §10.5).

    Dict-only; the spec deliberately rejects string-shorthand to keep the
    surface inspectable. Unknown top-level keys raise ``ImproperlyConfigured``
    in the same shape DMCP-02 §10.2 uses for its analogous setting.
    """
    if not isinstance(entry, dict):
        raise ImproperlyConfigured(
            f"DJANGO_MCP_PROMPTS entries must be dicts, got {type(entry).__name__}"
        )
    unknown = set(entry) - _USER_PROMPT_ALLOWED_KEYS
    if unknown:
        raise ImproperlyConfigured(f"DJANGO_MCP_PROMPTS entry has unknown keys: {sorted(unknown)}")
    for key in _USER_PROMPT_REQUIRED_KEYS:
        if key not in entry:
            raise ImproperlyConfigured(f"DJANGO_MCP_PROMPTS entry is missing required key {key!r}")

    name = _require_str(entry, "name")
    _validate_slug(name)
    description = _require_str(entry, "description")
    body = _require_str(entry, "body")
    arguments = _parse_arguments(entry["arguments"])

    permission = entry.get("permission")
    if permission is not None and not isinstance(permission, str):
        raise ImproperlyConfigured(
            "DJANGO_MCP_PROMPTS entry 'permission' must be a string or omitted"
        )

    return UserPromptEntry(
        name=name,
        description=description,
        arguments=arguments,
        body=body,
        permission=permission,
    )


class _SafeDict(dict):
    """``str.format_map`` adapter that leaves unknown ``{name}`` placeholders intact.

    INV-DMCP03-5 forbids ``render_handler`` from raising on missing arguments —
    a prompt is a template, not a function call. Missing keys yield literal
    ``{key}`` text so the operator can spot the gap when reading the rendered
    body.
    """

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _safe_render(template: str, arguments: dict[str, Any]) -> str:
    return template.format_map(_SafeDict(arguments))


def _single_user_message(text: str) -> list[dict[str, Any]]:
    return [{"role": "user", "content": {"type": "text", "text": text}}]


class AdminActionPromptRule(PromptDerivationRule):
    """Emit one ``prompt.admin.<app>.<Model>.<action_name>`` per admin action.

    The discovery walk mirrors :class:`django_mcp.admin.AdminActionRule`'s
    ``_get_base_actions`` idiom (DMCP-01 §6): we call the same private hook,
    fall back to the ``actions`` class attribute on failure, and emit one
    prompt per discovered action. The two rules deliberately do NOT share
    code so ``prompts.py`` stays independent of ``admin.py``.
    """

    kind = "admin"

    @classmethod
    def emit(cls, source: ModelAdmin) -> Iterable[PromptDescriptor]:
        actions = cls._discover_actions(source)
        for action_name, (func, _name, description) in actions.items():
            yield cls._build_descriptor(source, action_name, func, description)

    @staticmethod
    def _discover_actions(source: ModelAdmin) -> dict[str, tuple[Any, str, str]]:
        try:
            base = list(source._get_base_actions())
        except Exception as exc:
            logger.warning(
                "AdminActionPromptRule: _get_base_actions failed for %s.%s: %s; "
                "falling back to class-level actions",
                source.model._meta.app_label,
                source.model._meta.object_name,
                exc,
            )
            return AdminActionPromptRule._fallback_actions(source)

        actions: dict[str, tuple[Any, str, str]] = {}
        for entry in base:
            if not isinstance(entry, tuple) or len(entry) < 3:
                continue
            func, name, description = entry[0], entry[1], entry[2]
            actions[name] = (func, name, description)
        return actions

    @staticmethod
    def _fallback_actions(source: ModelAdmin) -> dict[str, tuple[Any, str, str]]:
        actions: dict[str, tuple[Any, str, str]] = {}
        for action in getattr(source, "actions", None) or []:
            if callable(action):
                name = action.__name__
                description = getattr(action, "short_description", name)
                actions[name] = (action, name, description)
            elif isinstance(action, str):
                func = getattr(source, action, None)
                if func is not None:
                    description = getattr(func, "short_description", action)
                    actions[action] = (func, action, description)
        return actions

    @classmethod
    def _build_descriptor(
        cls,
        source: ModelAdmin,
        action_name: str,
        action_func: Any,
        description: str,
    ) -> PromptDescriptor:
        model = source.model
        app_label = model._meta.app_label
        object_name = model._meta.object_name
        model_name_lc = model._meta.model_name
        tool_suffix = f"{app_label}.{object_name}.{action_name}"
        prompt_name = f"prompt.admin.{tool_suffix}"
        tool_name = f"admin.action:{tool_suffix}"

        if description:
            human_description = description
        else:
            human_description = f"Invoke {action_name} on selected {object_name} instances."

        arguments: tuple[PromptArgument, ...] = (
            PromptArgument(
                name="pks",
                description="Primary keys to act on",
                required=True,
            ),
        )

        allowed_permissions = getattr(action_func, "allowed_permissions", None)
        view_perm = f"{app_label}.view_{model_name_lc}"

        def render_handler(ctx: ToolCallContext) -> list[dict[str, Any]]:
            # INV-DMCP03-5: build the message list; do NOT execute the tool.
            pks = (ctx.arguments or {}).get("pks", [])
            body = f"Please invoke the {tool_name} tool with pks={pks}. {human_description}"
            return _single_user_message(body)

        def auth_check(ctx: ToolCallContext) -> PermissionOutcome:
            user = ctx.user
            if user is None or not getattr(user, "is_authenticated", False):
                return PermissionOutcome.UNAUTHENTICATED
            if not user.has_perm(view_perm):
                return PermissionOutcome.DENY
            if allowed_permissions:
                for codename in allowed_permissions:
                    full = f"{app_label}.{codename}_{model_name_lc}"
                    if not user.has_perm(full):
                        return PermissionOutcome.DENY
            return PermissionOutcome.ALLOW

        return PromptDescriptor(
            name=prompt_name,
            description=human_description,
            arguments=arguments,
            render_handler=render_handler,
            auth_check=auth_check,
            origin=prompt_name,
        )


class UserPromptRule(PromptDerivationRule):
    """Emit one ``prompt.user.<slug>`` per ``DJANGO_MCP_PROMPTS`` entry."""

    kind = "user"

    @classmethod
    def emit(cls, source: UserPromptEntry) -> Iterable[PromptDescriptor]:
        prompt_name = f"prompt.user.{source.name}"
        body_template = source.body
        permission = source.permission

        def render_handler(ctx: ToolCallContext) -> list[dict[str, Any]]:
            # INV-DMCP03-5: safe substitution; missing keys are left as
            # literal {name} text rather than raising.
            text = _safe_render(body_template, ctx.arguments or {})
            return _single_user_message(text)

        def auth_check(ctx: ToolCallContext) -> PermissionOutcome:
            user = ctx.user
            if user is None or not getattr(user, "is_authenticated", False):
                return PermissionOutcome.UNAUTHENTICATED
            if permission is None:
                return PermissionOutcome.ALLOW
            return PermissionOutcome.ALLOW if user.has_perm(permission) else PermissionOutcome.DENY

        yield PromptDescriptor(
            name=prompt_name,
            description=source.description,
            arguments=source.arguments,
            render_handler=render_handler,
            auth_check=auth_check,
            origin=prompt_name,
        )


def emit_admin_action_prompts(
    model_admins: Iterable[ModelAdmin],
) -> Iterable[PromptDescriptor]:
    """Apply :class:`AdminActionPromptRule` to each registered ``ModelAdmin``."""
    for admin_obj in model_admins:
        yield from AdminActionPromptRule.emit(admin_obj)


def emit_user_prompts(entries: Iterable[dict[str, Any]]) -> Iterable[PromptDescriptor]:
    """Apply :class:`UserPromptRule` to each ``DJANGO_MCP_PROMPTS`` entry."""
    for entry in entries:
        parsed = parse_user_prompt_entry(entry)
        yield from UserPromptRule.emit(parsed)


__all__ = (
    "UserPromptEntry",
    "parse_user_prompt_entry",
    "AdminActionPromptRule",
    "UserPromptRule",
    "emit_admin_action_prompts",
    "emit_user_prompts",
)
