"""DMCP01 §7 + DMCP02 §7 — Django/DRF fields and model PKs projected to JSON Schema.

The Django form-field surface is unconditional (Django is a hard dep). The DRF
serializer surface is import-guarded per INV-DMCP02-8: ``rest_framework`` is
imported lazily inside ``drf_field_to_json_schema`` and
``drf_serializer_to_json_schema``; consumers that don't use those functions
don't pay the DRF import cost and the module remains importable when DRF is
not installed.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

try:
    from django import forms
    from django.db import models
except ImportError as exc:  # pragma: no cover - import-time guard
    raise ImportError(
        "django_mcp.schemas requires Django to be importable; install Django>=4.2."
    ) from exc

logger = logging.getLogger(__name__)

# Matches Django's own DecimalValidator grammar at the syntactic level: optional
# sign, integer part, optional fractional part. Per-field max_digits /
# decimal_places narrowing lives in Django's validator chain, not in this regex
# — DMCP01 §7 amendment required to surface those numerically.
_DECIMAL_PATTERN = r"^-?\d+(\.\d+)?$"


def _min_max_from_validators(field: forms.Field) -> tuple[Any, Any]:
    """Pull (minimum, maximum) from a field's validators / explicit bounds.

    Django's IntegerField / FloatField stash bounds on the field as
    `min_value` / `max_value` AND attach Min/MaxValueValidator instances; either
    surface answers the question — prefer the explicit attributes when present.
    """
    from django.core.validators import MaxValueValidator, MinValueValidator

    minimum: Any = getattr(field, "min_value", None)
    maximum: Any = getattr(field, "max_value", None)
    for validator in getattr(field, "validators", ()):
        if isinstance(validator, MinValueValidator) and minimum is None:
            minimum = validator.limit_value
        elif isinstance(validator, MaxValueValidator) and maximum is None:
            maximum = validator.limit_value
    return minimum, maximum


def field_to_json_schema_for_model_pk(model: type[models.Model]) -> dict[str, Any]:
    """JSON Schema for the primary-key field of `model` (DMCP01 §7)."""
    pk = model._meta.pk
    if isinstance(
        pk,
        models.AutoField | models.BigAutoField | models.IntegerField | models.BigIntegerField,
    ):
        return {"type": "integer"}
    if isinstance(pk, models.UUIDField):
        return {"type": "string", "format": "uuid"}
    if isinstance(pk, models.CharField | models.SlugField):
        schema: dict[str, Any] = {"type": "string"}
        if pk.max_length is not None:
            schema["maxLength"] = pk.max_length
        return schema
    # DMCP01 §7 amendment required to handle additional pk types.
    logger.warning(
        "field_to_json_schema_for_model_pk: unhandled pk class %s on %s; falling back to string",
        pk.__class__.__name__,
        model.__name__,
    )
    return {"type": "string"}


def field_to_json_schema(field: forms.Field) -> dict[str, Any]:
    """Project a Django form `Field` to JSON Schema per the DMCP01 §7 table.

    Required-ness is enforced at the enclosing object level by
    `form_to_json_schema`, not on the per-field schema.
    """
    schema = _map_field(field)
    label = getattr(field, "label", None)
    if label:
        schema["title"] = str(label)
    help_text = getattr(field, "help_text", None)
    if help_text:
        schema["description"] = str(help_text)
    return schema


def _map_field(field: forms.Field) -> dict[str, Any]:
    # Order of preference per DMCP01 §7: widget-declared schema first, then the
    # frozen field-class table. No widget exposes a JSON Schema in stock
    # Django, so step 1 is a hook for future widgets — we fall through.

    # ChoiceField is a superclass of ModelChoiceField; check the model variants
    # first so they win the dispatch.
    if isinstance(field, forms.ModelMultipleChoiceField):
        return {
            "type": "array",
            "items": field_to_json_schema_for_model_pk(field.queryset.model),
        }
    if isinstance(field, forms.ModelChoiceField):
        return field_to_json_schema_for_model_pk(field.queryset.model)
    if isinstance(field, forms.EmailField):
        return {"type": "string", "format": "email"}
    if isinstance(field, forms.URLField):
        return {"type": "string", "format": "uri"}
    if isinstance(field, forms.DateTimeField):
        return {"type": "string", "format": "date-time"}
    if isinstance(field, forms.DateField):
        return {"type": "string", "format": "date"}
    if isinstance(field, forms.BooleanField):
        return {"type": "boolean"}
    if isinstance(field, forms.DecimalField):
        return {"type": "string", "pattern": _DECIMAL_PATTERN}
    if isinstance(field, forms.FloatField):
        schema: dict[str, Any] = {"type": "number"}
        minimum, maximum = _min_max_from_validators(field)
        if minimum is not None:
            schema["minimum"] = minimum
        if maximum is not None:
            schema["maximum"] = maximum
        return schema
    if isinstance(field, forms.IntegerField):
        schema = {"type": "integer"}
        minimum, maximum = _min_max_from_validators(field)
        if minimum is not None:
            schema["minimum"] = minimum
        if maximum is not None:
            schema["maximum"] = maximum
        return schema
    if isinstance(field, forms.ChoiceField):
        choices = list(field.choices)
        if any(not isinstance(c[1], str) and hasattr(c[1], "__iter__") for c in choices):
            logger.warning(
                "ChoiceField on %s uses optgroups; emitting generic string fallback "
                "per DMCP01 §7.2 / ERRATA-001",
                field.__class__.__name__,
            )
            return {"type": "string"}
        return {"enum": [value for value, _label in choices]}
    if isinstance(field, forms.CharField):
        schema = {"type": "string"}
        if field.max_length is not None:
            schema["maxLength"] = field.max_length
        return schema

    # DMCP01 §7 amendment required to handle additional form-field classes
    # (e.g. JSONField, TimeField, DurationField, FileField).
    logger.warning(
        "field_to_json_schema: unhandled form-field class %s; falling back to string",
        field.__class__.__name__,
    )
    return {"type": "string"}


def form_to_json_schema(
    form_class_or_instance: type[forms.BaseForm] | forms.BaseForm,
) -> dict[str, Any]:
    """Project a Django Form (class or instance) to a JSON Schema object."""
    if isinstance(form_class_or_instance, forms.BaseForm):
        form = form_class_or_instance
    else:
        form = form_class_or_instance()

    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, field in form.fields.items():
        properties[name] = field_to_json_schema(field)
        if field.required:
            required.append(name)

    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


# Mapping from concrete model-field classes to the §7 form-field analogue used
# for output-schema derivation. Order matters: subclasses come before their
# bases so isinstance dispatch picks the most specific entry.
def _model_field_to_json_schema(field: models.Field) -> dict[str, Any]:
    if isinstance(field, models.AutoField | models.BigAutoField):
        return {"type": "integer"}
    if isinstance(field, models.ForeignKey):
        return field_to_json_schema_for_model_pk(field.related_model)
    if isinstance(field, models.UUIDField):
        return {"type": "string", "format": "uuid"}
    if isinstance(field, models.EmailField):
        return {"type": "string", "format": "email"}
    if isinstance(field, models.URLField):
        return {"type": "string", "format": "uri"}
    if isinstance(field, models.SlugField):
        schema: dict[str, Any] = {"type": "string"}
        if field.max_length is not None:
            schema["maxLength"] = field.max_length
        return schema
    if isinstance(field, models.DateTimeField):
        return {"type": "string", "format": "date-time"}
    if isinstance(field, models.DateField):
        return {"type": "string", "format": "date"}
    if isinstance(field, models.BooleanField):
        return {"type": "boolean"}
    if isinstance(field, models.DecimalField):
        return {"type": "string", "pattern": _DECIMAL_PATTERN}
    if isinstance(field, models.FloatField):
        return {"type": "number"}
    if isinstance(field, models.IntegerField | models.BigIntegerField):
        return {"type": "integer"}
    if isinstance(field, models.CharField | models.TextField):
        schema = {"type": "string"}
        max_length = getattr(field, "max_length", None)
        if max_length is not None:
            schema["maxLength"] = max_length
        return schema

    # DMCP01 §7 amendment required to handle additional model-field classes
    # (e.g. JSONField, BinaryField, DurationField, FileField).
    logger.warning(
        "model_to_output_schema: unhandled model-field class %s; falling back to string",
        field.__class__.__name__,
    )
    return {"type": "string"}


def _annotate_model_field_schema(schema: dict[str, Any], field: models.Field) -> dict[str, Any]:
    # Per DMCP01 §7.1 / §7.3: surface verbose_name as `title` when it diverges
    # from the field name's title-cased form (which is what Django auto-fills),
    # and help_text as `description` when non-empty.
    verbose_name = getattr(field, "verbose_name", None)
    if verbose_name:
        auto = field.name.replace("_", " ")
        if str(verbose_name) != auto:
            schema["title"] = str(verbose_name)
    help_text = getattr(field, "help_text", None)
    if help_text:
        schema["description"] = str(help_text)
    return schema


def model_to_output_schema(
    model: type[models.Model],
    visible_fields: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Build the output schema for a model (DMCP01 §5 — `M`; §7.3 mapping rules).

    Per-user visibility filtering (INV-DMCP01-3) is *not* done here; callers
    pass the already-resolved field-name set via `visible_fields`. `None` means
    "every concrete field" — reverse relations are always skipped, M2M fields
    render as arrays of target pks per §7.3.
    """
    visible_set = set(visible_fields) if visible_fields is not None else None

    properties: dict[str, Any] = {}
    required: list[str] = []
    for field in model._meta.get_fields():
        # Reverse relations (descriptors) are skipped — only forward-declared
        # columns and forward M2M participate per §7.3.
        is_concrete = getattr(field, "concrete", False)
        is_m2m = getattr(field, "many_to_many", False)
        if not is_concrete and not is_m2m:
            continue
        # Forward M2M is "concrete" in Django's terms for the through table but
        # not for the column; treat it as M2M unconditionally.
        name = field.name
        if visible_set is not None and name not in visible_set:
            continue

        if is_m2m:
            field_schema: dict[str, Any] = {
                "type": "array",
                "items": field_to_json_schema_for_model_pk(field.related_model),
            }
        else:
            field_schema = _model_field_to_json_schema(field)
        properties[name] = _annotate_model_field_schema(field_schema, field)

        # §7.3: required iff null=False AND blank=False. Auto-pks are always
        # required (they're populated by the DB but always present in output).
        is_auto_pk = isinstance(field, models.AutoField | models.BigAutoField)
        not_null = not getattr(field, "null", True)
        not_blank = not getattr(field, "blank", True)
        if is_auto_pk or (not_null and not_blank):
            required.append(name)

    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


# ---------------------------------------------------------------------------
# DMCP-02 §7 — DRF serializer field → JSON Schema (import-guarded).
# ---------------------------------------------------------------------------


def drf_field_to_json_schema(field: Any) -> dict[str, Any]:
    """Project a DRF serializer field to JSON Schema per DMCP-02 §7.

    Raises ``ImportError`` if ``rest_framework`` is not installed — callers
    SHOULD guard with ``django_mcp.schemas.drf_available()`` first.
    """
    from rest_framework import serializers as drf

    schema = _drf_map_field(field, drf)
    label = getattr(field, "label", None)
    if label:
        schema["title"] = str(label)
    help_text = getattr(field, "help_text", None)
    if help_text:
        schema["description"] = str(help_text)
    return schema


def drf_serializer_to_json_schema(
    serializer_class_or_instance: Any,
) -> dict[str, Any]:
    """Project a DRF Serializer to a JSON Schema object."""
    from rest_framework import serializers as drf

    serializer = (
        serializer_class_or_instance
        if isinstance(serializer_class_or_instance, drf.BaseSerializer)
        else serializer_class_or_instance()
    )

    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, field in serializer.fields.items():
        if getattr(field, "write_only", False) is False:
            # Output side is the default projection; required-ness rule below
            # follows the input convention.
            pass
        properties[name] = drf_field_to_json_schema(field)
        if field.required:
            required.append(name)

    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def drf_available() -> bool:
    """True iff ``rest_framework`` can be imported. INV-DMCP02-8 entry point."""
    try:
        import rest_framework  # noqa: F401
    except ImportError:
        return False
    return True


def _drf_map_field(field: Any, drf: Any) -> dict[str, Any]:
    # ChoiceField / MultipleChoice handle optgroup-shape per DMCP01 §7.2 idiom.
    if isinstance(field, drf.MultipleChoiceField):
        return {"type": "array", "items": _drf_choices_to_enum(field)}
    if isinstance(field, drf.ChoiceField):
        return _drf_choices_to_enum(field)
    if isinstance(field, drf.PrimaryKeyRelatedField):
        # Prefer the queryset's model when present (concrete relation); fall
        # back to {"type":"string"} when DRF has been initialised without a
        # bound queryset (e.g. read-only declarations).
        qs = getattr(field, "queryset", None)
        if qs is not None and hasattr(qs, "model"):
            return field_to_json_schema_for_model_pk(qs.model)
        return {"type": "string"}
    if isinstance(field, drf.SlugRelatedField):
        return {"type": "string"}
    if isinstance(field, drf.HyperlinkedRelatedField | drf.HyperlinkedIdentityField):
        return {"type": "string", "format": "uri"}
    if isinstance(field, drf.SerializerMethodField):
        logger.warning(
            "drf_field_to_json_schema: SerializerMethodField %r has no declared "
            "return type; emitting string fallback per INV-DMCP02-6",
            field.field_name,
        )
        return {"type": "string"}
    if isinstance(field, drf.ListSerializer):
        child = field.child
        if isinstance(child, drf.BaseSerializer):
            return {"type": "array", "items": drf_serializer_to_json_schema(child)}
        return {"type": "array", "items": drf_field_to_json_schema(child)}
    if isinstance(field, drf.ListField):
        return {"type": "array", "items": drf_field_to_json_schema(field.child)}
    if isinstance(field, drf.Serializer):
        return drf_serializer_to_json_schema(field)
    if isinstance(field, drf.IntegerField):
        schema: dict[str, Any] = {"type": "integer"}
        if field.min_value is not None:
            schema["minimum"] = field.min_value
        if field.max_value is not None:
            schema["maximum"] = field.max_value
        return schema
    if isinstance(field, drf.FloatField):
        schema = {"type": "number"}
        if field.min_value is not None:
            schema["minimum"] = field.min_value
        if field.max_value is not None:
            schema["maximum"] = field.max_value
        return schema
    if isinstance(field, drf.DecimalField):
        return {"type": "string", "pattern": _DECIMAL_PATTERN}
    if isinstance(field, drf.BooleanField):
        schema = {"type": "boolean"}
        if getattr(field, "allow_null", False):
            schema = {"type": ["boolean", "null"]}
        return schema
    if isinstance(field, drf.DateTimeField):
        return {"type": "string", "format": "date-time"}
    if isinstance(field, drf.DateField):
        return {"type": "string", "format": "date"}
    if isinstance(field, drf.TimeField):
        return {"type": "string", "format": "time"}
    if isinstance(field, drf.UUIDField):
        return {"type": "string", "format": "uuid"}
    if isinstance(field, drf.EmailField):
        return {"type": "string", "format": "email"}
    if isinstance(field, drf.URLField):
        return {"type": "string", "format": "uri"}
    if isinstance(field, drf.RegexField):
        schema = {"type": "string"}
        regex = getattr(field, "regex", None)
        if regex is not None:
            schema["pattern"] = regex.pattern if hasattr(regex, "pattern") else str(regex)
        if field.max_length is not None:
            schema["maxLength"] = field.max_length
        return schema
    if isinstance(field, drf.SlugField):
        schema = {"type": "string", "pattern": r"^[-a-zA-Z0-9_]+$"}
        if field.max_length is not None:
            schema["maxLength"] = field.max_length
        return schema
    if isinstance(field, drf.CharField):
        schema = {"type": "string"}
        if field.max_length is not None:
            schema["maxLength"] = field.max_length
        return schema

    # DMCP02 §7 amendment required to handle additional DRF field classes.
    logger.warning(
        "drf_field_to_json_schema: unhandled DRF field class %s; falling back to string",
        field.__class__.__name__,
    )
    return {"type": "string"}


def _drf_choices_to_enum(field: Any) -> dict[str, Any]:
    """Extract choice values; fall back to string on optgroups (DMCP01 §7.2)."""
    choices = (
        list(field.choices.items()) if hasattr(field.choices, "items") else list(field.choices)
    )
    # DRF flattens optgroups into a dict before exposing them on .choices, so
    # the iteration is always (value, label) here. The DMCP01 §7.2 grouped-
    # shape detection still applies for paranoia.
    if any(not isinstance(c[1], str) and hasattr(c[1], "__iter__") for c in choices):
        logger.warning(
            "drf_field_to_json_schema: ChoiceField on %r uses optgroup-shaped "
            "choices; emitting generic string fallback per DMCP01 §7.2 / ERRATA-001",
            field.__class__.__name__,
        )
        return {"type": "string"}
    return {"enum": [value for value, _label in choices]}
