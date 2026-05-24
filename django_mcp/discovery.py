"""DMCP01 / DMCP02 / DMCP03 / INV-DMCP-5 — single-pass discovery into the registry.

The discovery pass walks (in order):

1. Every configured ``AdminSite`` (DMCP-01) — applies the six admin rules
   from ``django_mcp.admin``.
2. Every URL pattern reachable from ``DJANGO_MCP_URLCONFS`` (DMCP-02) —
   applies ``ViewInvokeRule`` for plain views and ``DRFViewSetRule`` for
   DRF surfaces, both filtering by ``DJANGO_MCP_VIEW_EXCLUDE``.
3. Every entry in ``DJANGO_MCP_MODEL_SEARCH`` (DMCP-02) — applies
   ``ModelSearchRule``.
4. Every admin-registered model + ``DJANGO_MCP_RESOURCE_MODELS`` entry
   (DMCP-03) — applies ``ModelResourceRule`` and ``FileFieldResourceRule``.
5. Every admin action from step 1 (DMCP-03) — applies
   ``AdminActionPromptRule``.
6. Every entry in ``DJANGO_MCP_PROMPTS`` (DMCP-03) — applies
   ``UserPromptRule``.

The whole walk runs exactly once per process, behind a single registry
lock. Test fixtures may call ``MCPRegistry.clear`` between passes — see
``registry.py``.

Settings consumed (all optional):

- ``DJANGO_MCP_ADMIN_SITES`` — list of dotted ``AdminSite`` instance paths;
  defaults to ``("django.contrib.admin.site",)``.
- ``DJANGO_MCP_URLCONFS`` — list of URLconf module paths to walk; defaults
  to ``[settings.ROOT_URLCONF]``.
- ``DJANGO_MCP_VIEW_EXCLUDE`` — list of dotted view paths to skip;
  defaults to ``[]``.
- ``DJANGO_MCP_MODEL_SEARCH`` — list of entries per DMCP-02 §10.2;
  defaults to ``[]``.
- ``DJANGO_MCP_REQUIRE_AUTH`` — see DMCP-02 §10.3; defaults to ``True``.
- ``DJANGO_MCP_RESOURCE_MODELS`` — list of entries per DMCP-03 §10.5
  for non-admin model resource emission; defaults to ``[]``.
- ``DJANGO_MCP_RESOURCES_DISABLED`` — DMCP-03 global kill-switch; when
  ``True``, the resource and prompt rules emit nothing. Default: ``False``.
- ``DJANGO_MCP_PROMPTS`` — list of entries per DMCP-03 §10.5;
  defaults to ``[]``.
- ``DJANGO_MCP_FIELD_RESOURCE_MAX_BYTES`` — per-read cap for FileField
  resources (INV-DMCP03-8). Default: 10 MiB.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Iterable
from typing import Any

from django.conf import settings
from django.contrib.admin import AdminSite

from django_mcp.admin import emit_for_admin
from django_mcp.drf import emit_for_drf_views
from django_mcp.prompts import emit_admin_action_prompts, emit_user_prompts
from django_mcp.registry import get_registry
from django_mcp.resources import (
    build_admin_resource_spec,
    emit_for_resource_specs,
    parse_resource_model_entry,
)
from django_mcp.search import emit_for_model_search
from django_mcp.urlwalker import ViewKind, walk_urls
from django_mcp.views import emit_for_walked_views

logger = logging.getLogger(__name__)

DEFAULT_ADMIN_SITES: tuple[str, ...] = ("django.contrib.admin.site",)


def _resolve_admin_sites(paths: Iterable[str]) -> list[AdminSite]:
    sites: list[AdminSite] = []
    seen_ids: set[int] = set()
    for path in paths:
        module_path, _, attr = path.rpartition(".")
        if not module_path or not attr:
            raise ImproperlyConfigured(
                f"DJANGO_MCP_ADMIN_SITES entry {path!r} must be a dotted import path"
            )
        module = importlib.import_module(module_path)
        try:
            site = getattr(module, attr)
        except AttributeError as exc:
            raise ImproperlyConfigured(
                f"DJANGO_MCP_ADMIN_SITES entry {path!r} not found in {module_path!r}"
            ) from exc
        if not isinstance(site, AdminSite):
            raise ImproperlyConfigured(
                f"DJANGO_MCP_ADMIN_SITES entry {path!r} resolves to "
                f"{type(site).__name__}, expected AdminSite"
            )
        if id(site) in seen_ids:
            continue
        seen_ids.add(id(site))
        sites.append(site)
    return sites


def _descriptor_key(descriptor: Any) -> str:
    """Return the per-kind unique key for a descriptor (tool_name / uri / name)."""
    # Avoid eager imports of DMCP-03 descriptor types for the same reason the
    # registry module dispatches lazily.
    if hasattr(descriptor, "tool_name"):
        return descriptor.tool_name
    if hasattr(descriptor, "uri"):
        return descriptor.uri
    if hasattr(descriptor, "name"):
        return descriptor.name
    raise TypeError(f"unknown descriptor kind: {type(descriptor).__name__}")


def _descriptor_already_registered(registry: Any, descriptor: Any) -> bool:
    if hasattr(descriptor, "tool_name"):
        return descriptor.tool_name in registry.tools
    if hasattr(descriptor, "uri"):
        return descriptor.uri in registry.resources
    if hasattr(descriptor, "name"):
        return descriptor.name in registry.prompts
    return False


def _register_unique(registry: Any, descriptor: Any, *, source_label: str) -> bool:
    """Register a descriptor, logging + skipping on duplicate key."""
    if _descriptor_already_registered(registry, descriptor):
        logger.warning(
            "django_mcp discovery: duplicate descriptor key %r from %s — keeping first occurrence",
            _descriptor_key(descriptor),
            source_label,
        )
        return False
    registry.register(descriptor)
    return True


def discover_now() -> int:
    """Run the full discovery pass once. Returns the number of descriptors emitted.

    INV-DMCP-5: holds the registry lock; if the registry is already frozen,
    returns 0 without re-running. Safe to call from multiple threads — the
    first caller wins; the others see a frozen registry and return 0.
    """
    registry = get_registry()
    with registry.lock:
        if registry.is_frozen():
            return 0

        # --- DMCP-01: admin pass ----------------------------------------
        site_paths = getattr(settings, "DJANGO_MCP_ADMIN_SITES", DEFAULT_ADMIN_SITES)
        sites = _resolve_admin_sites(site_paths)
        admin_emitted = 0
        for site in sites:
            label = f"AdminSite {getattr(site, 'name', site)!r}"
            for _model, model_admin in site._registry.items():
                for descriptor in emit_for_admin(model_admin):
                    if _register_unique(registry, descriptor, source_label=label):
                        admin_emitted += 1

        # --- DMCP-02: URL walk ------------------------------------------
        excluded = list(getattr(settings, "DJANGO_MCP_VIEW_EXCLUDE", ()) or ())
        urlconfs = getattr(settings, "DJANGO_MCP_URLCONFS", None)
        walked = list(walk_urls(urlconfs, excluded_paths=excluded))

        plain_views = [w for w in walked if w.kind in (ViewKind.FBV, ViewKind.CBV)]
        drf_views = [w for w in walked if w.kind in (ViewKind.DRF_VIEWSET, ViewKind.DRF_APIVIEW)]

        plain_emitted = 0
        for descriptor in emit_for_walked_views(plain_views):
            if _register_unique(registry, descriptor, source_label="ViewInvokeRule"):
                plain_emitted += 1

        drf_emitted = 0
        for descriptor in emit_for_drf_views(drf_views):
            if _register_unique(registry, descriptor, source_label="DRFViewSetRule"):
                drf_emitted += 1

        # --- DMCP-02: model search --------------------------------------
        search_entries = list(getattr(settings, "DJANGO_MCP_MODEL_SEARCH", ()) or ())
        search_emitted = 0
        for descriptor in emit_for_model_search(search_entries):
            if _register_unique(registry, descriptor, source_label="ModelSearchRule"):
                search_emitted += 1

        # --- DMCP-03: resources -----------------------------------------
        # Admin-registered models (resource permissions inherit admin parity);
        # plus any DJANGO_MCP_RESOURCE_MODELS entries (opt-in non-admin path).
        # The kill-switch is honoured inside emit_for_resource_specs.
        resource_specs = []
        seen_models: set[int] = set()
        for site in sites:
            for model, model_admin in site._registry.items():
                if id(model) in seen_models:
                    continue
                seen_models.add(id(model))
                resource_specs.append(build_admin_resource_spec(model_admin))
        for entry in getattr(settings, "DJANGO_MCP_RESOURCE_MODELS", ()) or ():
            spec = parse_resource_model_entry(entry)
            if id(spec.model) in seen_models:
                logger.warning(
                    "django_mcp discovery: %s.%s appears in both an AdminSite "
                    "registry AND DJANGO_MCP_RESOURCE_MODELS; admin path wins "
                    "(DMCP-03 §10.6)",
                    spec.model._meta.app_label,
                    spec.model._meta.object_name,
                )
                continue
            seen_models.add(id(spec.model))
            resource_specs.append(spec)

        resource_emitted = 0
        for descriptor in emit_for_resource_specs(resource_specs):
            if _register_unique(registry, descriptor, source_label="resource rule"):
                resource_emitted += 1

        # --- DMCP-03: prompts -------------------------------------------
        # Admin-action prompts re-walk the same ModelAdmins that step 1 used;
        # the prompts module shares the _get_base_actions idiom internally.
        resources_disabled = bool(getattr(settings, "DJANGO_MCP_RESOURCES_DISABLED", False))
        admin_prompt_emitted = 0
        if not resources_disabled:
            model_admins = [
                model_admin for site in sites for _model, model_admin in site._registry.items()
            ]
            for descriptor in emit_admin_action_prompts(model_admins):
                if _register_unique(registry, descriptor, source_label="AdminActionPromptRule"):
                    admin_prompt_emitted += 1

        user_prompt_entries = list(getattr(settings, "DJANGO_MCP_PROMPTS", ()) or ())
        user_prompt_emitted = 0
        if not resources_disabled:
            for descriptor in emit_user_prompts(user_prompt_entries):
                if _register_unique(registry, descriptor, source_label="UserPromptRule"):
                    user_prompt_emitted += 1

        total = (
            admin_emitted
            + plain_emitted
            + drf_emitted
            + search_emitted
            + resource_emitted
            + admin_prompt_emitted
            + user_prompt_emitted
        )
        registry.freeze()
        logger.info(
            "django_mcp discovery [DMCP-01+DMCP-02+DMCP-03]: emitted %d descriptors "
            "(admin=%d from %d site(s), view=%d, drf=%d, search=%d from %d entries, "
            "resources=%d, admin_prompts=%d, user_prompts=%d from %d entries)",
            total,
            admin_emitted,
            len(sites),
            plain_emitted,
            drf_emitted,
            search_emitted,
            len(search_entries),
            resource_emitted,
            admin_prompt_emitted,
            user_prompt_emitted,
            len(user_prompt_entries),
        )
        return total


def ensure_discovered() -> None:
    """Lazy entry point for the first MCP request.

    Idempotent; thread-safe via the registry lock acquired inside
    ``discover_now``.
    """
    if not get_registry().is_frozen():
        discover_now()


# Imported late to keep the module importable without Django settings configured
# (CLAUDE.md / DMCP00-b). ImproperlyConfigured is the canonical Django signal
# for misconfigured settings — used inside _resolve_admin_sites above.
from django.core.exceptions import ImproperlyConfigured  # noqa: E402
