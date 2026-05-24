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

## Django REST Framework

DRF is supported as a first-class derivation target ([`django_mcp/drf.py`](django_mcp/drf.py),
DMCP-02 §5.3 / §10.1). Support is **import-guarded** — the module is always
importable, but emission is a no-op when `rest_framework` isn't installed
(INV-DMCP02-8). No configuration is required; if DRF is on the path and a
router is mounted under `ROOT_URLCONF`, the URL walker finds the ViewSets and
the rule emits tools.

### Verb mapping

| DRF handler | MCP tool |
|---|---|
| `list` | `view.list:<app>.<View>` |
| `retrieve` | `view.retrieve:<app>.<View>` |
| `create` | `view.create:<app>.<View>` |
| `update` + `partial_update` | one `view.update:<app>.<View>` (collapsed per §10.1, body is `partial=True`) |
| `destroy` | `view.delete:<app>.<View>` |
| `@action(detail=…)` | `view.invoke:<app>.<View>.<action>` |
| Bare `APIView` subclass | one `view.invoke:<app>.<View>` (method dispatched via the `method` argument) |

Router list and detail patterns of one ViewSet coalesce to a single tool set;
no duplicate emissions. PUT and PATCH collapse to one `view.update:` tool whose
body schema has every field optional — clients send only the fields they want
to change.

### Schema derivation

Input/output schemas come from `view.serializer_class` (or `get_serializer_class()`
when the attribute is absent and the call is safe without a request). The
walker uses [`drf_serializer_to_json_schema`](django_mcp/schemas.py) to project
the serializer's declared fields into JSON Schema. Detail-bound tools wrap the
body schema in `{path: {pk}, body}`; list output uses a fixed
`{results, count, page, page_size}` envelope.

### Permissions

Only `permission_classes` participate (DMCP-02 §8.2) — `authentication_classes`
are intentionally bypassed because MCP authentication is owned by `MCPAPIKey`,
and `request.user` is the artifact DRF permissions see. The gate runs twice:
once at `auth_check` (pre-invoke, `has_permission` only) and again at
handler time (`has_permission` + `has_object_permission` once the target
object has been fetched). Both stages re-synthesise a fresh view instance via
`build_admin_request`.

### Async / ORM boundary

Every DRF handler wraps its serializer-validation / queryset-evaluation /
`.save()` work in `asyncio.to_thread(_run)` per INV-DMCP-1. Handlers never
block the event loop on ORM I/O.

### Known limitations

- `authentication_classes` are not honored; MCP auth is `MCPAPIKey`-only.
- `pagination_class` is ignored; the list envelope is fixed
  (`{ordering, page, page_size}` in, `{results, count, page, page_size}` out).
- `filter_backends` / `filterset_class` are not applied — only the `ordering`
  argument is honored.
- DRF renderers / parsers / content negotiation are bypassed; bodies arrive
  as already-parsed JSON-RPC argument dicts.

A future phase (DMCP-05 candidate) could let `pagination_class` and
`filter_backends` participate at list time; the shape would be a §15
amendment to DMCP-02 §5.3 plus one new invariant.

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
