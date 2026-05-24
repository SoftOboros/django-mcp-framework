"""DMCP01 §5/§6 — admin → MCP tool derivation rules.

Walks a ``ModelAdmin`` and emits the six default tools per DMCP-01 §5:
``admin.list``, ``admin.retrieve``, ``admin.create``, ``admin.update``,
``admin.delete``, and ``admin.action:<...>.<action_name>`` for every action
registered on (or inherited by) the admin.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterable
from typing import Any

from django.contrib.admin import ModelAdmin
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.forms.models import model_to_dict

from django_mcp.derivation import (
    DerivationRule,
    PermissionOutcome,
    ToolCallContext,
    ToolDescriptor,
)
from django_mcp.names import RuleFamily, Verb
from django_mcp.names import format as _format_name
from django_mcp.requests import build_admin_request
from django_mcp.schemas import (
    field_to_json_schema_for_model_pk,
    form_to_json_schema,
    model_to_output_schema,
)

logger = logging.getLogger(__name__)


_CLASS_PERMISSION_METHOD: dict[Verb, str] = {
    Verb.LIST: "has_view_permission",
    Verb.RETRIEVE: "has_view_permission",
    Verb.CREATE: "has_add_permission",
    Verb.UPDATE: "has_change_permission",
    Verb.DELETE: "has_delete_permission",
}


def _target(model: type[models.Model]) -> tuple[str, str]:
    return (model._meta.app_label, model._meta.object_name)


def _pk_schema(model: type[models.Model]) -> dict[str, Any]:
    return field_to_json_schema_for_model_pk(model)


def _flatten_fields(fields: Iterable[Any]) -> list[str]:
    flat: list[str] = []
    for f in fields:
        if isinstance(f, tuple | list):
            flat.extend(f)
        else:
            flat.append(f)
    return flat


def _serialize_instance(
    source: ModelAdmin,
    request: Any,
    instance: models.Model,
    *,
    visible_fields: Iterable[str] | None = None,
) -> dict[str, Any]:
    if visible_fields is None:
        try:
            visible = _flatten_fields(source.get_fields(request, instance))
        except Exception:
            visible = [f.name for f in instance._meta.concrete_fields]
    else:
        visible = list(visible_fields)

    pk_name = instance._meta.pk.name
    if pk_name not in visible:
        visible.insert(0, pk_name)

    data = model_to_dict(instance, fields=visible)
    # model_to_dict skips non-editable fields (auto-pks) even when listed in
    # `fields`. Backfill the pk so the output schema's required-set is honoured.
    data[pk_name] = instance.pk
    # FieldFile values (FileField / ImageField) are not JSON-serialisable;
    # coerce to their storage name string (or "" for empty fields). The raw
    # bytes belong on the DMCP-03 field-resource path, not in tool payloads.
    for key, value in list(data.items()):
        if hasattr(value, "name") and hasattr(value, "storage"):
            data[key] = value.name or ""
    return json.loads(json.dumps(data, cls=DjangoJSONEncoder))


def _class_auth_check(source: ModelAdmin, verb: Verb):
    """Build the discovery-time auth_check for class-level (no-obj) verbs."""
    method_name = _CLASS_PERMISSION_METHOD[verb]
    verb_str = verb.value

    def check(ctx: ToolCallContext) -> PermissionOutcome:
        user = ctx.user
        if user is None or not getattr(user, "is_authenticated", False):
            return PermissionOutcome.UNAUTHENTICATED
        request = build_admin_request(user, verb=verb_str)
        method = getattr(source, method_name)
        return PermissionOutcome.ALLOW if method(request) else PermissionOutcome.DENY

    return check


class AdminListRule(DerivationRule):
    family = RuleFamily.ADMIN

    @classmethod
    def emit(cls, source: ModelAdmin) -> Iterable[ToolDescriptor]:
        model = source.model
        tool_name = _format_name(RuleFamily.ADMIN, Verb.LIST, _target(model))

        properties: dict[str, Any] = {
            "filters": {"type": "object", "additionalProperties": True},
            "ordering": {"type": "string"},
            "page": {"type": "integer", "minimum": 1, "default": 1},
            "page_size": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 25},
        }
        if getattr(source, "search_fields", None):
            properties["q"] = {"type": "string"}

        input_schema = {
            "type": "object",
            "properties": properties,
            "additionalProperties": False,
        }
        m_schema = model_to_output_schema(model)
        output_schema = {
            "type": "object",
            "properties": {
                "results": {"type": "array", "items": m_schema},
                "count": {"type": "integer"},
                "page": {"type": "integer"},
                "page_size": {"type": "integer"},
            },
            "required": ["results", "count", "page", "page_size"],
            "additionalProperties": False,
        }

        async def handler(ctx: ToolCallContext) -> dict[str, Any]:
            args = ctx.arguments or {}
            page = max(1, int(args.get("page") or 1))
            page_size = max(1, min(1000, int(args.get("page_size") or 25)))
            q = args.get("q") or ""
            ordering = args.get("ordering") or ""
            filters = args.get("filters") or {}

            def _run() -> tuple[list[dict[str, Any]], int]:
                request = build_admin_request(ctx.user, verb="list")
                if not source.has_view_permission(request):
                    raise PermissionDenied("admin.list not permitted")
                qs = source.get_queryset(request)
                if filters:
                    qs = qs.filter(**filters)
                if q and source.get_search_fields(request):
                    qs, _ = source.get_search_results(request, qs, q)
                if ordering:
                    qs = qs.order_by(*[o.strip() for o in ordering.split(",") if o.strip()])
                count = qs.count()
                start = (page - 1) * page_size
                results = [
                    _serialize_instance(source, request, obj)
                    for obj in qs[start : start + page_size]
                ]
                return results, count

            results, count = await asyncio.to_thread(_run)
            return {
                "results": results,
                "count": count,
                "page": page,
                "page_size": page_size,
            }

        yield ToolDescriptor(
            tool_name=tool_name,
            description=f"List {model._meta.verbose_name_plural}.",
            input_schema=input_schema,
            output_schema=output_schema,
            handler=handler,
            auth_check=_class_auth_check(source, Verb.LIST),
            origin=tool_name,
        )


class AdminRetrieveRule(DerivationRule):
    family = RuleFamily.ADMIN

    @classmethod
    def emit(cls, source: ModelAdmin) -> Iterable[ToolDescriptor]:
        model = source.model
        tool_name = _format_name(RuleFamily.ADMIN, Verb.RETRIEVE, _target(model))

        input_schema = {
            "type": "object",
            "properties": {"pk": _pk_schema(model)},
            "required": ["pk"],
            "additionalProperties": False,
        }
        output_schema = {
            "type": "object",
            "properties": {"object": model_to_output_schema(model)},
            "required": ["object"],
            "additionalProperties": False,
        }

        async def handler(ctx: ToolCallContext) -> dict[str, Any]:
            pk = (ctx.arguments or {}).get("pk")

            def _run() -> dict[str, Any]:
                request = build_admin_request(ctx.user, verb="retrieve")
                qs = source.get_queryset(request)
                try:
                    instance = qs.get(pk=pk)
                except model.DoesNotExist as exc:
                    raise LookupError(f"{model.__name__} pk={pk!r} not found") from exc
                if not source.has_view_permission(request, instance):
                    raise PermissionDenied("admin.retrieve not permitted")
                return _serialize_instance(source, request, instance)

            data = await asyncio.to_thread(_run)
            return {"object": data}

        yield ToolDescriptor(
            tool_name=tool_name,
            description=f"Retrieve a {model._meta.verbose_name} by primary key.",
            input_schema=input_schema,
            output_schema=output_schema,
            handler=handler,
            auth_check=_class_auth_check(source, Verb.RETRIEVE),
            origin=tool_name,
        )


def _form_input_schema_at_discovery(
    source: ModelAdmin, *, change: bool, rule_label: str
) -> dict[str, Any]:
    try:
        request = build_admin_request(AnonymousUser(), verb="update" if change else "create")
        form_class = source.get_form(request, obj=None, change=change)
        return form_to_json_schema(form_class)
    except Exception as exc:
        logger.warning(
            "%s: get_form(synthesized, change=%s) failed for %s.%s: %s; "
            "falling back to permissive object schema",
            rule_label,
            change,
            source.model._meta.app_label,
            source.model._meta.object_name,
            exc,
        )
        return {"type": "object", "additionalProperties": True}


class AdminCreateRule(DerivationRule):
    family = RuleFamily.ADMIN

    @classmethod
    def emit(cls, source: ModelAdmin) -> Iterable[ToolDescriptor]:
        model = source.model
        tool_name = _format_name(RuleFamily.ADMIN, Verb.CREATE, _target(model))

        input_schema = _form_input_schema_at_discovery(
            source, change=False, rule_label="AdminCreateRule"
        )
        output_schema = {
            "type": "object",
            "properties": {"object": model_to_output_schema(model)},
            "required": ["object"],
            "additionalProperties": False,
        }

        async def handler(ctx: ToolCallContext) -> dict[str, Any]:
            args = ctx.arguments or {}

            def _run() -> dict[str, Any]:
                request = build_admin_request(ctx.user, verb="create", body=args)
                if not source.has_add_permission(request):
                    raise PermissionDenied("admin.create not permitted")
                form_class = source.get_form(request, obj=None, change=False)
                form = form_class(data=args)
                if not form.is_valid():
                    raise ValueError(f"validation failed: {form.errors.as_json()}")
                instance = form.save(commit=False)
                source.save_model(request, instance, form, change=False)
                form.save_m2m()
                source.save_related(request, form, formsets=[], change=False)
                return _serialize_instance(source, request, instance)

            return {"object": await asyncio.to_thread(_run)}

        yield ToolDescriptor(
            tool_name=tool_name,
            description=f"Create a new {model._meta.verbose_name}.",
            input_schema=input_schema,
            output_schema=output_schema,
            handler=handler,
            auth_check=_class_auth_check(source, Verb.CREATE),
            origin=tool_name,
        )


class AdminUpdateRule(DerivationRule):
    family = RuleFamily.ADMIN

    @classmethod
    def emit(cls, source: ModelAdmin) -> Iterable[ToolDescriptor]:
        model = source.model
        tool_name = _format_name(RuleFamily.ADMIN, Verb.UPDATE, _target(model))

        fields_schema = _form_input_schema_at_discovery(
            source, change=True, rule_label="AdminUpdateRule"
        )
        input_schema = {
            "type": "object",
            "properties": {
                "pk": _pk_schema(model),
                "fields": fields_schema,
            },
            "required": ["pk", "fields"],
            "additionalProperties": False,
        }
        output_schema = {
            "type": "object",
            "properties": {"object": model_to_output_schema(model)},
            "required": ["object"],
            "additionalProperties": False,
        }

        async def handler(ctx: ToolCallContext) -> dict[str, Any]:
            args = ctx.arguments or {}
            pk = args.get("pk")
            fields = args.get("fields") or {}

            def _run() -> dict[str, Any]:
                request = build_admin_request(ctx.user, verb="update", body=fields)
                qs = source.get_queryset(request)
                try:
                    instance = qs.get(pk=pk)
                except model.DoesNotExist as exc:
                    raise LookupError(f"{model.__name__} pk={pk!r} not found") from exc
                if not source.has_change_permission(request, instance):
                    raise PermissionDenied("admin.update not permitted")
                form_class = source.get_form(request, obj=instance, change=True)
                form = form_class(data=fields, instance=instance)
                if not form.is_valid():
                    raise ValueError(f"validation failed: {form.errors.as_json()}")
                instance = form.save(commit=False)
                source.save_model(request, instance, form, change=True)
                form.save_m2m()
                source.save_related(request, form, formsets=[], change=True)
                return _serialize_instance(source, request, instance)

            return {"object": await asyncio.to_thread(_run)}

        yield ToolDescriptor(
            tool_name=tool_name,
            description=f"Update a {model._meta.verbose_name} by primary key.",
            input_schema=input_schema,
            output_schema=output_schema,
            handler=handler,
            auth_check=_class_auth_check(source, Verb.UPDATE),
            origin=tool_name,
        )


class AdminDeleteRule(DerivationRule):
    family = RuleFamily.ADMIN

    @classmethod
    def emit(cls, source: ModelAdmin) -> Iterable[ToolDescriptor]:
        model = source.model
        tool_name = _format_name(RuleFamily.ADMIN, Verb.DELETE, _target(model))

        input_schema = {
            "type": "object",
            "properties": {"pk": _pk_schema(model)},
            "required": ["pk"],
            "additionalProperties": False,
        }
        output_schema = {
            "type": "object",
            "properties": {
                "deleted": {"type": "boolean", "const": True},
                "pk": _pk_schema(model),
            },
            "required": ["deleted", "pk"],
            "additionalProperties": False,
        }

        async def handler(ctx: ToolCallContext) -> dict[str, Any]:
            pk = (ctx.arguments or {}).get("pk")

            def _run() -> Any:
                request = build_admin_request(ctx.user, verb="delete")
                qs = source.get_queryset(request)
                try:
                    instance = qs.get(pk=pk)
                except model.DoesNotExist as exc:
                    raise LookupError(f"{model.__name__} pk={pk!r} not found") from exc
                if not source.has_delete_permission(request, instance):
                    raise PermissionDenied("admin.delete not permitted")
                # §10: route through delete_model so soft-delete overrides participate.
                source.delete_model(request, instance)
                return pk

            deleted_pk = await asyncio.to_thread(_run)
            return {"deleted": True, "pk": deleted_pk}

        yield ToolDescriptor(
            tool_name=tool_name,
            description=f"Delete a {model._meta.verbose_name} by primary key.",
            input_schema=input_schema,
            output_schema=output_schema,
            handler=handler,
            auth_check=_class_auth_check(source, Verb.DELETE),
            origin=tool_name,
        )


class AdminActionRule(DerivationRule):
    family = RuleFamily.ADMIN

    @classmethod
    def emit(cls, source: ModelAdmin) -> Iterable[ToolDescriptor]:
        actions = cls._discover_actions(source)
        for action_name, (func, _name, _description) in actions.items():
            yield cls._build_descriptor(source, action_name, func)

    @staticmethod
    def _discover_actions(source: ModelAdmin) -> dict[str, tuple[Any, str, str]]:
        # `_get_base_actions()` is Django's "after inheritance, before
        # request-permission filtering" entry point. Using `get_actions(request)`
        # would filter out actions whose `allowed_permissions` reject the
        # synthesized discovery user, which would violate INV-DMCP01-4 — the
        # tool list MUST be the superset; per-user filtering happens in the
        # auth_check.
        try:
            base = list(source._get_base_actions())
        except Exception as exc:
            logger.warning(
                "AdminActionRule: _get_base_actions failed for %s.%s: %s; "
                "falling back to class-level actions",
                source.model._meta.app_label,
                source.model._meta.object_name,
                exc,
            )
            return AdminActionRule._fallback_actions(source)

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
    ) -> ToolDescriptor:
        model = source.model
        app_label, object_name = _target(model)
        tool_name = _format_name(
            RuleFamily.ADMIN,
            Verb.ACTION,
            (app_label, object_name, action_name),
        )

        input_schema = {
            "type": "object",
            "properties": {
                "pks": {"type": "array", "items": _pk_schema(model), "minItems": 1},
            },
            "required": ["pks"],
            "additionalProperties": False,
        }
        output_schema = {
            "type": "object",
            "properties": {
                "updated": {"type": "integer", "minimum": 0},
                "message": {"type": "string"},
            },
            "required": ["updated"],
            "additionalProperties": False,
        }

        allowed_permissions = getattr(action_func, "allowed_permissions", None)
        model_name_lc = model._meta.model_name

        def auth_check(ctx: ToolCallContext) -> PermissionOutcome:
            user = ctx.user
            if user is None or not getattr(user, "is_authenticated", False):
                return PermissionOutcome.UNAUTHENTICATED
            request = build_admin_request(user, verb="action")
            # §8: has_view_permission is the floor for every action.
            if not source.has_view_permission(request):
                return PermissionOutcome.DENY
            if allowed_permissions:
                for codename in allowed_permissions:
                    full = f"{app_label}.{codename}_{model_name_lc}"
                    if not user.has_perm(full):
                        return PermissionOutcome.DENY
            return PermissionOutcome.ALLOW

        async def handler(ctx: ToolCallContext) -> dict[str, Any]:
            pks = (ctx.arguments or {}).get("pks") or []

            def _run() -> dict[str, Any]:
                request = build_admin_request(ctx.user, verb="action")
                if not source.has_view_permission(request):
                    raise PermissionDenied("admin.action not permitted")
                if allowed_permissions:
                    for codename in allowed_permissions:
                        full = f"{app_label}.{codename}_{model_name_lc}"
                        if not ctx.user.has_perm(full):
                            raise PermissionDenied(f"admin.action requires permission {full}")
                qs = source.get_queryset(request).filter(pk__in=pks)
                affected = qs.count()
                result = action_func(source, request, qs)
                # Admin actions may return None or an HttpResponse (e.g. the
                # default delete_selected returns a confirmation page).
                message: str | None = None
                if result is None:
                    pass
                elif hasattr(result, "url"):
                    message = f"redirect:{result.url}"
                elif isinstance(result, str):
                    message = result
                else:
                    message = result.__class__.__name__
                payload: dict[str, Any] = {"updated": affected}
                if message is not None:
                    payload["message"] = message
                return payload

            return await asyncio.to_thread(_run)

        raw = getattr(action_func, "short_description", None)
        if raw:
            # Django's built-in delete_selected ships a gettext_lazy string
            # with %(verbose_name_plural)s as a placeholder, meant to be
            # substituted at admin-page-render time. Apply the substitution
            # eagerly so the wire-side description is fully rendered.
            try:
                description = str(raw) % {
                    "verbose_name": model._meta.verbose_name,
                    "verbose_name_plural": model._meta.verbose_name_plural,
                }
            except (KeyError, ValueError, TypeError):
                description = str(raw)
        else:
            description = f"Apply '{action_name}' to selected {model._meta.verbose_name_plural}."

        return ToolDescriptor(
            tool_name=tool_name,
            description=description,
            input_schema=input_schema,
            output_schema=output_schema,
            handler=handler,
            auth_check=auth_check,
            origin=tool_name,
        )


ALL_ADMIN_RULES: tuple[type[DerivationRule], ...] = (
    AdminListRule,
    AdminRetrieveRule,
    AdminCreateRule,
    AdminUpdateRule,
    AdminDeleteRule,
    AdminActionRule,
)


def emit_for_admin(source: ModelAdmin) -> Iterable[ToolDescriptor]:
    """Apply every admin rule in order; yields a flat stream of descriptors."""
    for rule in ALL_ADMIN_RULES:
        yield from rule.emit(source)
