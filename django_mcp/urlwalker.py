"""DMCP02 §6 — URL-tree walker shared by ViewInvokeRule and DRFViewSetRule.

Walks Django's URLResolver tree (handles ``include()`` recursion via Django's
own resolver), yields a ``WalkedView`` record per concrete URL pattern. Path
converters are projected to JSON Schema per the §4 / INV-DMCP02-2 mapping.

This module owns the boundary between the URLconf surface (Django) and the
rule layer (DMCP-02); rules read ``WalkedView`` records, not raw URLPatterns.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.urls import URLPattern, URLResolver, get_resolver
from django.urls.converters import (
    IntConverter,
    PathConverter,
    SlugConverter,
    StringConverter,
    UUIDConverter,
)

logger = logging.getLogger(__name__)


class ViewKind:
    """Frozen enum-as-class so the import surface stays a string."""

    FBV = "fbv"
    CBV = "cbv"
    DRF_VIEWSET = "drf_viewset"
    DRF_APIVIEW = "drf_apiview"
    UNRESOLVABLE = "unresolvable"


@dataclass(frozen=True, slots=True)
class WalkedView:
    """One row produced by the walker. Rules consume these, not raw patterns."""

    pattern: URLPattern
    callback: Any
    view: Any  # The underlying class (CBV / DRF) or the function itself (FBV)
    kind: str
    dotted_path: str
    url_path: str
    path_args_schema: dict[str, Any]
    namespace: str | None


def _converter_to_schema(converter: Any) -> dict[str, Any]:
    """Map a Django path converter to JSON Schema (INV-DMCP02-2).

    Built-in mappings (Django ``urls.converters``):
    - IntConverter   → {"type": "integer"}
    - UUIDConverter  → {"type": "string", "format": "uuid"}
    - SlugConverter  → {"type": "string", "pattern": "[-a-zA-Z0-9_]+"}
    - StringConverter → {"type": "string"}  (regex [^/]+)
    - PathConverter   → {"type": "string"}  (regex .+ — slash-permitting)

    Anything else: ``{"type": "string"}`` + WARNING naming the converter class.
    """
    if isinstance(converter, IntConverter):
        return {"type": "integer"}
    if isinstance(converter, UUIDConverter):
        return {"type": "string", "format": "uuid"}
    if isinstance(converter, SlugConverter):
        return {"type": "string", "pattern": converter.regex}
    if isinstance(converter, StringConverter | PathConverter):
        return {"type": "string"}
    logger.warning(
        "urlwalker: unknown path converter %s; falling back to string schema",
        converter.__class__.__name__,
    )
    return {"type": "string"}


def _path_args_schema(pattern: URLPattern) -> dict[str, Any]:
    """Build a JSON Schema object describing the named groups in ``pattern``."""
    properties: dict[str, Any] = {}
    required: list[str] = []
    converters = getattr(pattern.pattern, "converters", {}) or {}
    for name, converter in converters.items():
        properties[name] = _converter_to_schema(converter)
        required.append(name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _resolve_view_identity(callback: Any) -> tuple[Any, str, str]:
    """Return (underlying_view, dotted_path, kind) for ``callback``.

    Handles:
    - DRF ``ViewSet.as_view({...})`` closure (carries ``cls`` and ``actions``).
    - Django CBV ``View.as_view()`` closure (carries ``view_class``).
    - DRF ``APIView`` subclasses (also ``view_class``, but the class is a
      subclass of ``rest_framework.views.APIView``).
    - Plain FBV.
    """
    # DRF ViewSet (highest precedence — the ViewSet has ``actions`` AND ``cls``)
    cls = getattr(callback, "cls", None)
    actions = getattr(callback, "actions", None)
    if cls is not None and actions is not None:
        try:
            from rest_framework.viewsets import ViewSetMixin

            if issubclass(cls, ViewSetMixin):
                return cls, _qualified_name(cls), ViewKind.DRF_VIEWSET
        except ImportError:
            pass

    # CBV / DRF APIView (closure with view_class)
    view_class = getattr(callback, "view_class", None) or getattr(callback, "cls", None)
    if view_class is not None and isinstance(view_class, type):
        try:
            from rest_framework.views import APIView

            if issubclass(view_class, APIView):
                return view_class, _qualified_name(view_class), ViewKind.DRF_APIVIEW
        except ImportError:
            pass
        return view_class, _qualified_name(view_class), ViewKind.CBV

    # FBV: a plain callable. Skip lambdas / closures without stable identity.
    qual = getattr(callback, "__qualname__", None)
    module = getattr(callback, "__module__", None)
    if qual is None or module is None or "<locals>" in qual or "<lambda>" in qual:
        return callback, "", ViewKind.UNRESOLVABLE
    return callback, f"{module}.{qual}", ViewKind.FBV


def _qualified_name(cls: type) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


def _flatten(
    resolver: URLResolver,
    *,
    prefix: str = "",
    namespace: str | None = None,
) -> Iterator[tuple[URLPattern, str, str | None]]:
    """Yield (URLPattern, full_url_path, namespace) for every leaf pattern."""
    for entry in resolver.url_patterns:
        if isinstance(entry, URLPattern):
            yield entry, prefix + str(entry.pattern), namespace
        elif isinstance(entry, URLResolver):
            child_prefix = prefix + str(entry.pattern)
            child_ns = entry.namespace if entry.namespace is not None else namespace
            yield from _flatten(entry, prefix=child_prefix, namespace=child_ns)


def walk_urls(
    urlconfs: Iterable[str] | None = None,
    *,
    excluded_paths: Iterable[str] = (),
) -> Iterator[WalkedView]:
    """Yield one WalkedView per concrete URL pattern.

    - ``urlconfs``: list of dotted URLconf module paths. Defaults to
      ``DJANGO_MCP_URLCONFS`` setting, which defaults to
      ``[settings.ROOT_URLCONF]``.
    - ``excluded_paths``: dotted view paths to skip (§10.6 — user-owned).
      An unresolvable view (lambda, unstable closure) is also skipped per §10.5.
    """
    confs = list(urlconfs) if urlconfs is not None else _default_urlconfs()
    excludes = set(excluded_paths)
    seen_dotted: set[str] = set()

    for conf_path in confs:
        # Importing the URLconf module is what populates the resolver. Use
        # Django's get_resolver which caches per-module.
        importlib.import_module(conf_path)
        resolver = get_resolver(conf_path)
        for pattern, url_path, namespace in _flatten(resolver):
            callback = pattern.callback
            view, dotted_path, kind = _resolve_view_identity(callback)

            if kind == ViewKind.UNRESOLVABLE:
                logger.warning(
                    "urlwalker: skipping pattern %r — view identity unresolvable "
                    "(lambda or unstable closure; see DMCP-02 §10.5)",
                    url_path or str(pattern.pattern),
                )
                continue

            if dotted_path in excludes:
                continue

            # De-duplicate when the same DRF ViewSet appears under both its
            # list and detail URL patterns. Rules coalesce per dotted_path,
            # so we only need to surface each pattern once — but DRF expects
            # both patterns visible to read .actions, so we don't dedupe
            # ViewSets here. Plain FBV/CBV duplicates *are* skipped.
            if kind in (ViewKind.FBV, ViewKind.CBV, ViewKind.DRF_APIVIEW):
                if dotted_path in seen_dotted:
                    continue
                seen_dotted.add(dotted_path)

            yield WalkedView(
                pattern=pattern,
                callback=callback,
                view=view,
                kind=kind,
                dotted_path=dotted_path,
                url_path=url_path,
                path_args_schema=_path_args_schema(pattern),
                namespace=namespace,
            )


def _default_urlconfs() -> list[str]:
    configured = getattr(settings, "DJANGO_MCP_URLCONFS", None)
    if configured:
        return list(configured)
    root = getattr(settings, "ROOT_URLCONF", None)
    return [root] if root else []
