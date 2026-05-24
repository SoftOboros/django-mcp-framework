# django-mcp

Expose a Django project's registered views and URL patterns as Model Context
Protocol (MCP) tools and resources — the same way Django's admin and CRUD
machinery falls out of registered models.

## Status

**Pre-1.0, feature-complete.** All four phases ratified and implemented; 105
tests pass across the surface. Per [`CLAUDE.md`](CLAUDE.md), the
"no backwards-compat shims" rule remains in effect until a v1.0 cut.

| Phase | Scope | Concepts doc | Ratified? |
|-------|-------|--------------|-----------|
| DMCP-00 | Foundational concepts, vocabulary, invariants | [`docs/concepts/TODO-DMCP-00-CONCEPTS.md`](docs/concepts/TODO-DMCP-00-CONCEPTS.md) | **2026-05-22** |
| DMCP-01 | Admin → MCP tools (ModelAdmin, admin actions) | [`docs/concepts/TODO-DMCP-01-ADMIN.md`](docs/concepts/TODO-DMCP-01-ADMIN.md) | **2026-05-22** |
| DMCP-02 | Applications → MCP tools (user views, DRF, FBV/CBV) | [`docs/concepts/TODO-DMCP-02-APPLICATIONS.md`](docs/concepts/TODO-DMCP-02-APPLICATIONS.md) | **2026-05-23** |
| DMCP-03 | Resources and prompts (URI-addressable model bodies, admin-action prompts) | [`docs/concepts/TODO-DMCP-03-RESOURCES-PROMPTS.md`](docs/concepts/TODO-DMCP-03-RESOURCES-PROMPTS.md) | **2026-05-23** |
| DMCP-04 | Transport (streamable HTTP + STDIO + MCPAPIKey + audit) | [`docs/concepts/TODO-DMCP-04-TRANSPORT.md`](docs/concepts/TODO-DMCP-04-TRANSPORT.md) | **2026-05-23** |

A conforming `django-mcp` deployment satisfies the acceptance gates listed in
each ratified phase's §12. Optional phases yield a second conformance level.

## Quick start

```python
# settings.py
INSTALLED_APPS = [
    ...,
    "django.contrib.admin",
    "django_mcp",
]

# urls.py
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("mcp/", include("django_mcp.urls")),  # POST /mcp/ — JSON-RPC 2.0
]
```

```sh
python manage.py migrate
python manage.py mcp_key create alice --name "alice's laptop"
# wire credential printed once: <key_id>.<secret>
```

That's the surface area. Tools and resources fall out of what's already
registered with `admin.site` and the project's URLconf. An MCP client
(Claude Desktop, MCP Inspector, custom) connects with the wire
credential in `Authorization: Bearer <credential>`, calls `initialize`,
then `tools/list` / `resources/templates/list` / `prompts/list`, and
invokes whatever it finds.

For STDIO clients (Claude Desktop-style), use the same key in an env
var: `DJANGO_MCP_KEY=<credential> python manage.py mcp_server`.

## What gets surfaced

| Source | Wire surface | Phase |
|--------|--------------|-------|
| Every `admin.site.register(Model, ModelAdmin)` | 6 tools per model — list/retrieve/create/update/delete + one per `@admin.action` | DMCP-01 |
| Every FBV / CBV in `ROOT_URLCONF` | `view.invoke:` (with CBV verb-narrowing to `view.list:` etc.) | DMCP-02 |
| Every DRF `ViewSet` | `view.list:`/`retrieve:`/`create:`/`update:`/`delete:` plus one `view.invoke:` per `@action` | DMCP-02 |
| Models in `DJANGO_MCP_MODEL_SEARCH` | `model.search:` with q/filters/ordering/pagination | DMCP-02 |
| Every admin-registered model | `django-mcp://model/<app>.<Model>/{pk}` resource template | DMCP-03 |
| Every `FileField` / `ImageField` on a participating model | `django-mcp://field/<app>.<Model>/{pk}/<field>` resource template | DMCP-03 |
| Every `@admin.action` | `prompt.admin.<app>.<Model>.<action>` parallel to its tool | DMCP-03 |
| Entries in `DJANGO_MCP_PROMPTS` | `prompt.user.<slug>` | DMCP-03 |

Permission enforcement reuses Django's existing `User.has_perm` /
`ModelAdmin.has_*_permission` / DRF `permission_classes` (INV-DMCP-3,
INV-DMCP01-2, INV-DMCP02-7). MCP authentication uses dedicated
`MCPAPIKey` credentials (DMCP-04 §6), separate from Django session
cookies / DRF tokens.

## Integration recipes

- **OAuth** — see [`docs/ops/oauth.md`](docs/ops/oauth.md) for the
  OAuth-mints-MCPAPIKey pattern. Works today with zero package changes.

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pip install -e .
.venv/bin/pytest
```

## Spec-Before-Code

This project follows a standards-body-style planning cycle. See
[`CLAUDE.md`](CLAUDE.md) for the full discipline, and
[`docs/README.md`](docs/README.md) for the initiative index.
