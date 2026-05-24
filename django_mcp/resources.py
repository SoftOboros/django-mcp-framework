"""DMCP-03 §6 — resource derivation rules.

Owns the two resource-emitting rules ratified in DMCP-03 §5.2 / §5.3:

- :class:`ModelResourceRule` (host = ``model``) emits one
  ``django-mcp://model/<app>.<Model>/{pk}`` template per admin-registered model
  OR ``DJANGO_MCP_RESOURCE_MODELS`` entry. The read handler delegates to the
  same ``_serialize_instance`` projection that ``admin.retrieve:`` uses
  (INV-DMCP01-3 visible-field parity, INV-DMCP03-2).
- :class:`FileFieldResourceRule` (host = ``field``) emits one
  ``django-mcp://field/<app>.<Model>/{pk}/<field_name>`` template per
  ``FileField`` (or ``ImageField``, which subclasses it) on a participating
  model. The handler enforces the byte cap from
  ``DJANGO_MCP_FIELD_RESOURCE_MAX_BYTES`` per INV-DMCP03-8 and re-checks the
  field's visibility against the admin's ``get_fields(request, obj)`` set per
  INV-DMCP03-2.

The discovery wiring (DMCP-03 §6 step 4) resolves admin registration; this
module's parser deliberately does NOT touch ``admin.site`` — the
``is_admin_registered`` flag on :class:`ResourceSpec` is set by the caller.
"""

from __future__ import annotations

import asyncio
import mimetypes
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from django.apps import apps
from django.conf import settings
from django.contrib.admin import ModelAdmin
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.db import models

from django_mcp.admin import _serialize_instance
from django_mcp.derivation import (
    PermissionOutcome,
    ResourceDerivationRule,
    ResourceDescriptor,
    ToolCallContext,
)
from django_mcp.names import format_resource_uri
from django_mcp.requests import build_admin_request

_DEFAULT_FIELD_RESOURCE_MAX_BYTES: int = 10 * 1024 * 1024
_ALLOWED_RESOURCE_MODEL_KEYS: frozenset[str] = frozenset({"model", "permission"})


def _default_view_permission(model: type[models.Model]) -> str:
    meta = model._meta
    return f"{meta.app_label}.view_{meta.model_name}"


def _resolve_model(dotted: str) -> type[models.Model]:
    try:
        return apps.get_model(dotted)
    except LookupError as exc:
        raise ImproperlyConfigured(
            f"DJANGO_MCP_RESOURCE_MODELS entry references unknown model {dotted!r}: {exc}"
        ) from exc
    except ValueError as exc:
        raise ImproperlyConfigured(
            f"DJANGO_MCP_RESOURCE_MODELS entry has malformed model path {dotted!r}: {exc}"
        ) from exc


@dataclass(frozen=True, slots=True)
class ResourceSpec:
    """Parsed source for a model's resource emission (DMCP-03 §5.2 / §10.5).

    ``is_admin_registered`` is informative for the rules: when True the read
    handlers honour ``ModelAdmin``'s visibility (INV-DMCP01-3); otherwise they
    fall through to the all-concrete-fields projection. ``admin_source`` is
    the ``ModelAdmin`` instance when the model is admin-registered; ``None``
    for the ``DJANGO_MCP_RESOURCE_MODELS`` opt-in path.
    """

    model: type[models.Model]
    permission: str
    is_admin_registered: bool = False
    admin_source: Any | None = None


def parse_resource_model_entry(entry: str | dict[str, Any]) -> ResourceSpec:
    """Parse one ``DJANGO_MCP_RESOURCE_MODELS`` entry per DMCP-03 §10.5.

    String form is a dotted ``app.Model`` path with defaults; dict form may
    override ``permission``. Unknown top-level keys raise
    ``ImproperlyConfigured`` — same discipline as DMCP-02 §10.2.
    """
    if isinstance(entry, str):
        model = _resolve_model(entry)
        return ResourceSpec(model=model, permission=_default_view_permission(model))

    if not isinstance(entry, dict):
        raise ImproperlyConfigured(
            f"DJANGO_MCP_RESOURCE_MODELS entry must be a dotted string or dict, "
            f"got {type(entry).__name__}"
        )

    unknown = set(entry.keys()) - _ALLOWED_RESOURCE_MODEL_KEYS
    if unknown:
        raise ImproperlyConfigured(
            f"DJANGO_MCP_RESOURCE_MODELS entry has unknown keys: {sorted(unknown)}"
        )

    model_path = entry.get("model")
    if not isinstance(model_path, str) or not model_path:
        raise ImproperlyConfigured(
            "DJANGO_MCP_RESOURCE_MODELS entry is missing required key 'model'"
        )
    model = _resolve_model(model_path)
    permission = entry.get("permission") or _default_view_permission(model)
    return ResourceSpec(model=model, permission=permission)


def build_admin_resource_spec(model_admin: ModelAdmin) -> ResourceSpec:
    """Convenience for the admin-walking path of discovery (DMCP-03 §6 step 4)."""
    model = model_admin.model
    return ResourceSpec(
        model=model,
        permission=_default_view_permission(model),
        is_admin_registered=True,
        admin_source=model_admin,
    )


def _target_components(model: type[models.Model]) -> tuple[str, str]:
    meta = model._meta
    return (meta.app_label, meta.object_name)


def _is_authenticated(user: Any) -> bool:
    return user is not None and getattr(user, "is_authenticated", False)


def _admin_visible_fields(
    model_admin: ModelAdmin, request: Any, instance: models.Model
) -> list[str]:
    flat: list[str] = []
    for entry in model_admin.get_fields(request, instance):
        if isinstance(entry, tuple | list):
            flat.extend(entry)
        else:
            flat.append(entry)
    return flat


def _field_resource_max_bytes() -> int:
    return int(
        getattr(settings, "DJANGO_MCP_FIELD_RESOURCE_MAX_BYTES", _DEFAULT_FIELD_RESOURCE_MAX_BYTES)
    )


def _build_model_auth_check(spec: ResourceSpec):
    permission = spec.permission
    admin_source = spec.admin_source
    model = spec.model

    def check(ctx: ToolCallContext) -> PermissionOutcome:
        user = ctx.user
        if not _is_authenticated(user):
            return PermissionOutcome.UNAUTHENTICATED
        if admin_source is not None:
            pk = (ctx.arguments or {}).get("pk")
            request = build_admin_request(user, verb="retrieve")
            instance: models.Model | None = None
            if pk is not None:
                try:
                    instance = model._default_manager.get(pk=pk)
                except model.DoesNotExist:
                    instance = None
            allowed = admin_source.has_view_permission(request, instance)
            return PermissionOutcome.ALLOW if allowed else PermissionOutcome.DENY
        return PermissionOutcome.ALLOW if user.has_perm(permission) else PermissionOutcome.DENY

    return check


def _build_model_read_handler(spec: ResourceSpec):
    model = spec.model
    admin_source = spec.admin_source

    async def handler(ctx: ToolCallContext) -> dict[str, Any]:
        pk = (ctx.arguments or {}).get("pk")

        def _run() -> dict[str, Any]:
            try:
                instance = model._default_manager.get(pk=pk)
            except model.DoesNotExist as exc:
                raise LookupError(f"{model.__name__} pk={pk!r} not found") from exc
            if admin_source is not None:
                request = build_admin_request(ctx.user, verb="retrieve")
                if not admin_source.has_view_permission(request, instance):
                    raise PermissionDenied("resource read not permitted")
                return _serialize_instance(admin_source, request, instance)
            # Non-admin path: every concrete field participates (no per-user
            # redaction available — INV-DMCP-4 says don't invent semantics
            # Django doesn't have, so this mirrors model.search's projection).
            visible = [f.name for f in model._meta.concrete_fields]
            return _serialize_instance(admin_source, None, instance, visible_fields=visible)

        return await asyncio.to_thread(_run)

    return handler


def _model_description(spec: ResourceSpec) -> str:
    model_name = spec.model._meta.object_name
    if spec.is_admin_registered:
        return (
            f"JSON projection of one {model_name} instance; field set follows "
            f"the admin's visibility per INV-DMCP01-3."
        )
    return (
        f"JSON projection of one {model_name} instance; all concrete fields "
        f"participate (no admin registration for per-user redaction)."
    )


class ModelResourceRule(ResourceDerivationRule):
    """DMCP-03 §5.2 — one ``model/<app>.<Model>/{pk}`` template per spec."""

    host = "model"

    @classmethod
    def emit(cls, source: ResourceSpec) -> Iterable[ResourceDescriptor]:
        app_label, object_name = _target_components(source.model)
        uri = format_resource_uri("model", (app_label, object_name), ("{pk}",))
        descriptor = ResourceDescriptor(
            uri=uri,
            name=f"{app_label}.{object_name}",
            description=_model_description(source),
            mime_type="application/json",
            is_template=True,
            read_handler=_build_model_read_handler(source),
            auth_check=_build_model_auth_check(source),
            origin=uri,
        )
        yield descriptor


def _iter_file_fields(model: type[models.Model]) -> Iterable[models.FileField]:
    for field in model._meta.get_fields():
        if isinstance(field, models.FileField):
            yield field


def _build_field_read_handler(spec: ResourceSpec, field: models.FileField):
    model = spec.model
    admin_source = spec.admin_source
    field_name = field.name

    async def handler(ctx: ToolCallContext) -> bytes:
        pk = (ctx.arguments or {}).get("pk")

        def _run() -> bytes:
            try:
                instance = model._default_manager.get(pk=pk)
            except model.DoesNotExist as exc:
                raise LookupError(f"{model.__name__} pk={pk!r} not found") from exc
            if admin_source is not None:
                request = build_admin_request(ctx.user, verb="retrieve")
                if not admin_source.has_view_permission(request, instance):
                    raise PermissionDenied("resource read not permitted")
                # INV-DMCP03-2: the field MUST be in the admin's visible set.
                visible = _admin_visible_fields(admin_source, request, instance)
                if field_name not in visible:
                    raise PermissionDenied(f"field {field_name!r} not in admin visible set")
            file_obj = getattr(instance, field_name)
            if not file_obj:
                raise LookupError(f"{model.__name__} pk={pk!r} has no file on field {field_name!r}")
            cap = _field_resource_max_bytes()
            size = file_obj.size
            # INV-DMCP03-8: hard cap, never silent truncation.
            if size > cap:
                raise ValueError(f"file exceeds DJANGO_MCP_FIELD_RESOURCE_MAX_BYTES={cap}")
            fh = file_obj.open("rb")
            try:
                return fh.read()
            finally:
                fh.close()

        return await asyncio.to_thread(_run)

    return handler


class FileFieldResourceRule(ResourceDerivationRule):
    """DMCP-03 §5.3 — one ``field/<app>.<Model>/{pk}/<name>`` template per FileField.

    Declared ``mime_type`` is the conservative ``application/octet-stream`` per
    INV-DMCP03-6 — the per-file mime resolution from
    ``mimetypes.guess_type(file.name)`` is a per-read concern owned by the
    DMCP-04 wire layer; the descriptor cannot honestly declare a single mime
    across all instances of a field.
    """

    host = "field"

    @classmethod
    def emit(cls, source: ResourceSpec) -> Iterable[ResourceDescriptor]:
        app_label, object_name = _target_components(source.model)
        for field in _iter_file_fields(source.model):
            uri = format_resource_uri("field", (app_label, object_name), ("{pk}", field.name))
            # Best-effort static mime hint for documentation only; the declared
            # value remains octet-stream per INV-DMCP03-6 honesty.
            _, _declared_hint = mimetypes.guess_type(f"placeholder.{field.name}")
            yield ResourceDescriptor(
                uri=uri,
                name=f"{app_label}.{object_name}.{field.name}",
                description=(
                    f"Binary content of {object_name}.{field.name}; mime "
                    f"resolved per-read via mimetypes.guess_type (INV-DMCP03-6)."
                ),
                mime_type="application/octet-stream",
                is_template=True,
                read_handler=_build_field_read_handler(source, field),
                auth_check=_build_model_auth_check(source),
                origin=uri,
            )


ALL_RESOURCE_RULES: tuple[type[ResourceDerivationRule], ...] = (
    ModelResourceRule,
    FileFieldResourceRule,
)


def emit_for_resource_specs(
    specs: Iterable[ResourceSpec],
) -> Iterable[ResourceDescriptor]:
    """Apply every resource rule to every spec; honours the global kill switch.

    Per DMCP-03 §6, ``DJANGO_MCP_RESOURCES_DISABLED`` is the operator-level
    opt-out for the whole resource surface.
    """
    if getattr(settings, "DJANGO_MCP_RESOURCES_DISABLED", False):
        return
    for spec in specs:
        for rule in ALL_RESOURCE_RULES:
            yield from rule.emit(spec)
