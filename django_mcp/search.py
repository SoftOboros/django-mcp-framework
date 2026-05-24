"""DMCP02 §5.4 / §6 — `ModelSearchRule` derivation.

Emits one ``model.search:<app>.<Model>`` tool per entry in
``DJANGO_MCP_MODEL_SEARCH``. Entry shape is frozen per §10.2 — string form
(dotted model path with defaults) or dict form with keys
``{model, search_fields?, permission?, filter_fields?}``. Unknown keys raise
``ImproperlyConfigured`` per §10.2.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import reduce
from operator import or_
from typing import Any

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Model, Q
from django.forms.models import model_to_dict

from django_mcp.derivation import (
    DerivationRule,
    PermissionOutcome,
    ToolCallContext,
    ToolDescriptor,
)
from django_mcp.names import RuleFamily, Verb
from django_mcp.names import format as _format_name
from django_mcp.schemas import model_to_output_schema

_ALLOWED_KEYS: frozenset[str] = frozenset({"model", "search_fields", "permission", "filter_fields"})


@dataclass(frozen=True, slots=True)
class SearchSpec:
    """Parsed ``DJANGO_MCP_MODEL_SEARCH`` entry (DMCP-02 §10.2)."""

    model: type[Model]
    search_fields: list[str] = field(default_factory=list)
    permission: str = ""
    filter_fields: list[str] = field(default_factory=list)


def _default_permission(model: type[Model]) -> str:
    meta = model._meta
    return f"{meta.app_label}.view_{meta.model_name}"


def _resolve_model(dotted: str) -> type[Model]:
    try:
        return apps.get_model(dotted)
    except LookupError as exc:
        raise ImproperlyConfigured(
            f"DJANGO_MCP_MODEL_SEARCH entry references unknown model {dotted!r}: {exc}"
        ) from exc
    except ValueError as exc:
        raise ImproperlyConfigured(
            f"DJANGO_MCP_MODEL_SEARCH entry has malformed model path {dotted!r}: {exc}"
        ) from exc


def parse_model_search_entry(entry: str | dict[str, Any]) -> SearchSpec:
    """Parse a single ``DJANGO_MCP_MODEL_SEARCH`` entry per §10.2."""
    if isinstance(entry, str):
        model = _resolve_model(entry)
        return SearchSpec(model=model, permission=_default_permission(model))

    if not isinstance(entry, dict):
        raise ImproperlyConfigured(
            f"DJANGO_MCP_MODEL_SEARCH entry must be a dotted string or dict, "
            f"got {type(entry).__name__}"
        )

    unknown = set(entry.keys()) - _ALLOWED_KEYS
    if unknown:
        raise ImproperlyConfigured(
            f"DJANGO_MCP_MODEL_SEARCH entry has unknown keys: {sorted(unknown)}"
        )

    model_path = entry.get("model")
    if not isinstance(model_path, str) or not model_path:
        raise ImproperlyConfigured("DJANGO_MCP_MODEL_SEARCH entry is missing required key 'model'")
    model = _resolve_model(model_path)

    search_fields = list(entry.get("search_fields") or [])
    filter_fields = list(entry.get("filter_fields") or [])
    permission = entry.get("permission") or _default_permission(model)

    return SearchSpec(
        model=model,
        search_fields=search_fields,
        permission=permission,
        filter_fields=filter_fields,
    )


def _serialize_search_instance(instance: Model) -> dict[str, Any]:
    pk_name = instance._meta.pk.name
    data = model_to_dict(instance)
    # model_to_dict drops non-editable fields like auto pks; backfill so the
    # output schema's required-set matches the admin serialiser idiom.
    data[pk_name] = instance.pk
    # FieldFile values aren't JSON-serialisable; coerce to storage name string
    # to mirror django_mcp.admin._serialize_instance.
    for key, value in list(data.items()):
        if hasattr(value, "name") and hasattr(value, "storage"):
            data[key] = value.name or ""
    return json.loads(json.dumps(data, cls=DjangoJSONEncoder))


def _build_input_schema(spec: SearchSpec) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "ordering": {"type": "string"},
        "page": {"type": "integer", "minimum": 1, "default": 1},
        "page_size": {
            "type": "integer",
            "minimum": 1,
            "maximum": 1000,
            "default": 25,
        },
    }
    if spec.search_fields:
        properties["q"] = {"type": "string"}
    if spec.filter_fields:
        properties["filters"] = {
            "type": "object",
            "properties": {f: {} for f in spec.filter_fields},
            "additionalProperties": False,
        }
    return {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }


def _build_output_schema(spec: SearchSpec) -> dict[str, Any]:
    m_schema = model_to_output_schema(spec.model)
    return {
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


def _auth_check_for(spec: SearchSpec):
    permission = spec.permission

    def check(ctx: ToolCallContext) -> PermissionOutcome:
        user = ctx.user
        if user is None or not getattr(user, "is_authenticated", False):
            return PermissionOutcome.UNAUTHENTICATED
        return PermissionOutcome.ALLOW if user.has_perm(permission) else PermissionOutcome.DENY

    return check


class ModelSearchRule(DerivationRule):
    """DMCP-02 §6 — emits ``model.search:<app>.<Model>`` per `SearchSpec`."""

    family = RuleFamily.MODEL

    @classmethod
    def emit(cls, source: SearchSpec) -> Iterable[ToolDescriptor]:
        model = source.model
        meta = model._meta
        tool_name = _format_name(
            RuleFamily.MODEL,
            Verb.SEARCH,
            (meta.app_label, meta.object_name),
        )

        input_schema = _build_input_schema(source)
        output_schema = _build_output_schema(source)
        auth_check = _auth_check_for(source)

        async def handler(ctx: ToolCallContext) -> dict[str, Any]:
            args = ctx.arguments or {}
            page = max(1, int(args.get("page") or 1))
            page_size = max(1, min(1000, int(args.get("page_size") or 25)))
            q = (args.get("q") or "").strip()
            ordering = args.get("ordering") or ""
            filters = args.get("filters") or {}

            def _run() -> tuple[list[dict[str, Any]], int]:
                user = ctx.user
                if user is None or not getattr(user, "is_authenticated", False):
                    raise PermissionDenied("model.search not permitted")
                if not user.has_perm(source.permission):
                    raise PermissionDenied(f"model.search requires permission {source.permission}")

                qs = model._default_manager.all()

                if q and source.search_fields:
                    # INV-DMCP-4: compose Django's Q primitive rather than
                    # reinventing icontains-OR fan-out.
                    q_objects = [Q(**{f"{f}__icontains": q}) for f in source.search_fields]
                    qs = qs.filter(reduce(or_, q_objects))

                if filters:
                    for key in filters:
                        if key not in source.filter_fields:
                            raise ValueError(
                                f"filter key {key!r} not in whitelist {source.filter_fields!r}"
                            )
                    qs = qs.filter(**filters)

                if ordering:
                    qs = qs.order_by(*[o.strip() for o in ordering.split(",") if o.strip()])

                count = qs.count()
                start = (page - 1) * page_size
                results = [_serialize_search_instance(obj) for obj in qs[start : start + page_size]]
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
            description=f"Search {source.model._meta.verbose_name_plural} by indexed fields.",
            input_schema=input_schema,
            output_schema=output_schema,
            handler=handler,
            auth_check=auth_check,
            origin=tool_name,
        )


def emit_for_model_search(
    entries: Iterable[str | dict[str, Any]],
) -> Iterable[ToolDescriptor]:
    """Parse each entry to a `SearchSpec` and apply `ModelSearchRule.emit`."""
    for entry in entries:
        spec = parse_model_search_entry(entry)
        yield from ModelSearchRule.emit(spec)
