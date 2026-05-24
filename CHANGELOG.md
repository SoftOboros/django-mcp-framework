# Changelog

All notable changes to `django-mcp-framework` are recorded here. Per-phase
§15 entries in `docs/concepts/TODO-DMCP-NN-*.md` carry the binding history
for each spec amendment; this file is the consolidated release-facing
summary.

## 1.0.0 — 2026-05-23

Production-stable release. No MCP wire-surface or Python API changes
from 0.9.0; this release promotes the package to 1.0 and activates the
post-1.0 backwards-compatibility rule from `CLAUDE.md` — breaking
changes henceforth require migration paths.

### Fixed

- `transport.mcp_endpoint` now preserves coroutine-function identity
  under Django 4.2 and 5.0 (whose `csrf_exempt` decorator wraps the
  view in a sync function, breaking `asyncio.iscoroutinefunction` and
  causing the request handler to dispatch the view as sync and reject
  the returned coroutine). The view sets `mcp_endpoint.csrf_exempt =
  True` directly. INV-DMCP04-4 (CSRF exemption scoped to this view
  only) is unchanged — only the mechanism differs.

### CI

- Test matrix expanded to Python 3.13 against Django 5.2; combinations
  (3.13, 4.2) and (3.13, 5.0) are excluded because Python 3.13 support
  landed in Django 5.1.
- Workflows opt into Node.js 24 for JavaScript actions
  (`FORCE_JAVASCRIPT_ACTIONS_TO_NODE24=true`) ahead of the 2026-06-02
  GitHub Actions runner default flip.
- Empty YAML `exclude:` mapping in `ci.yml` corrected (the placeholder
  block with only comments rejected the workflow at parse time).

### Docs

- README gains a Django REST Framework section: import-guard, verb
  mapping including the PUT/PATCH collapse to one `view.update:` per
  DMCP-02 §10.1, serializer→JSON Schema derivation, the
  `permission_classes`-only auth boundary per §8.2 (two-stage: pre-
  invoke `has_permission` and handler-time `has_object_permission`),
  the `asyncio.to_thread` ORM-boundary citation (INV-DMCP-1), and the
  feature-gap list (no `authentication_classes`, no `pagination_class`,
  no filter backends).
- README installation surface clarified: distributed as
  `django-mcp-framework` on PyPI, imported in Python as `django_mcp`.

### Classifiers

- `Development Status` flipped from `4 - Beta` to `5 - Production/Stable`.
- `Framework :: Django :: 5.2` and `Programming Language :: Python ::
  3.13` added to the trove classifier list.

## 0.9.0 — 2026-05-23

Initial release. Feature-complete pre-1.0 cut.

### Surface

Adds the `django_mcp` Django app. Exposes a project's registered views,
URL patterns, admin actions, models, and configured prompts as MCP tools,
resources, and prompt templates over an HTTP / STDIO transport.

Mount via:

```python
INSTALLED_APPS = [..., "django_mcp"]
urlpatterns = [path("mcp/", include("django_mcp.urls"))]
```

105 tests pass across the full surface. Targets MCP wire revision
**2025-03-26**.

### Added — DMCP-00 (Foundational concepts)

- `ToolDescriptor`, `ResourceDescriptor`, `PromptDescriptor` dataclasses
  as the package's in-process lingua franca for derivation rules.
- `MCPRegistry` (process-singleton) holding three dicts under one
  `threading.Lock`, frozen after a single discovery pass.
- Tool-name grammar (`<family>.<verb>:<dotted_target>`) and resource URI
  grammar (`django-mcp://<host>/<dotted_target>[/<segments>]`) with
  parsers in `django_mcp.names`.
- `PermissionOutcome` enum (`ALLOW` / `DENY` / `UNAUTHENTICATED` /
  `OUT_OF_SCOPE`).
- Single-pass discovery in `django_mcp.discovery.discover_now()`.

### Added — DMCP-01 (Admin → MCP tools)

- Six `DerivationRule` subclasses that walk `admin.site._registry` and
  emit tools per registered `ModelAdmin`:
  `admin.list:`, `admin.retrieve:`, `admin.create:`, `admin.update:`,
  `admin.delete:`, `admin.action:<app>.<Model>.<name>`.
- Permission enforcement delegates to `ModelAdmin.has_*_permission`;
  INV-DMCP-3 parity verified per gate.
- Form / model → JSON Schema derivation in `django_mcp.schemas`.
- Synthesised `HttpRequest` helper in `django_mcp.requests`.

### Added — DMCP-02 (Applications → MCP tools)

- URL-tree walker in `django_mcp.urlwalker` with path-converter →
  JSON Schema mapping (`<int:pk>` → `{"type":"integer"}`, etc.).
- `ViewInvokeRule` for plain FBVs and CBVs, with class-definition
  method narrowing (`get`-only `DetailView` → `view.retrieve:`,
  `get`-only `ListView` → `view.list:`).
- FBV auth-gate detection via decorator-chain walk + closure-cell scan
  (covers `@login_required`, `@permission_required` even with
  `functools.wraps`-masked qualnames).
- `DRFViewSetRule` (import-guarded — degrades to no-op when DRF
  isn't installed). PUT and PATCH collapse to one `view.update:` tool
  per §10.1.
- `ModelSearchRule` driven by `DJANGO_MCP_MODEL_SEARCH` setting.
- `DJANGO_MCP_REQUIRE_AUTH` (default `True`) — culls views with no
  detectable auth gate at discovery time per INV-DMCP02-4.

### Added — DMCP-03 (Resources and prompts)

- `ModelResourceRule` — one `django-mcp://model/<app>.<Model>/{pk}`
  resource template per admin-registered model (and per
  `DJANGO_MCP_RESOURCE_MODELS` entry).
- `FileFieldResourceRule` — one `django-mcp://field/<...>/{pk}/<field>`
  template per `FileField` / `ImageField` on a participating model.
  Read cap enforced by `DJANGO_MCP_FIELD_RESOURCE_MAX_BYTES` (10 MiB
  default; no silent truncation).
- `AdminActionPromptRule` — every `@admin.action` produces a parallel
  `prompt.admin.<app>.<Model>.<name>` whose body routes back to the
  corresponding `admin.action:` tool (INV-DMCP03-7).
- `UserPromptRule` driven by `DJANGO_MCP_PROMPTS` setting; safe
  `format_map`-style substitution so missing arguments don't raise
  (INV-DMCP03-5).
- `DJANGO_MCP_RESOURCES_DISABLED` global kill-switch.

### Added — DMCP-04 (Transport, MCPAPIKey, audit)

- `MCPAPIKey` model with manager (`objects.create_key(...)`) and
  lifecycle helpers (`verify_secret`, `revoke`, `rotate`,
  `touch_last_used`). Wire credential format `<key_id>.<secret>`,
  PBKDF2-hashed at rest.
- `manage.py mcp_key` command — subcommands `create` / `list` /
  `revoke` / `rotate` / `inspect`. Plaintext secret printed once at
  create / rotate.
- `manage.py mcp_server` — STDIO transport per MCP 2025-03-26 §STDIO.
  Reads `DJANGO_MCP_KEY` env var; refuses to start without it.
- `django_mcp.transport.mcp_endpoint` — async streamable-HTTP view
  (`@csrf_exempt`, scoped per INV-DMCP04-4). Mounted at the URL
  include's root.
- `django_mcp.dispatch.dispatch` — JSON-RPC 2.0 method router shared
  by both transports. Routes `initialize`, `tools/list`, `tools/call`,
  `resources/list`, `resources/templates/list`, `resources/read`,
  `prompts/list`, `prompts/get`.
- PermissionOutcome → JSON-RPC error mapping
  (UNAUTHENTICATED → `-32001`, DENY → `-32002`,
  OUT_OF_SCOPE → `-32003`); auth/transport split (HTTP `401`/`403`
  for pre-envelope failures, in-envelope errors for everything else).
- `initialize` reachable without bearer per INV-DMCP04-9; the
  capability declaration honestly omits surfaces whose registry is
  empty per INV-DMCP04-2.
- Audit emitter in `django_mcp.audit` — one structured JSONL record
  per call on the `django_mcp.audit` logger (INV-DMCP-7 /
  INV-DMCP04-5). Always emits, including pre-auth failures.

### Operations

- [`docs/ops/oauth.md`](docs/ops/oauth.md) — OAuth-mints-MCPAPIKey
  integration pattern. Works with any Django OAuth library
  (django-allauth, social-auth, django-oauth-toolkit) without
  changes to django-mcp-framework.

### Spec discipline

- All four phases ratified under the spec-before-code discipline
  documented in `CLAUDE.md`. Five amendments landed during
  implementation (DMCP-00 §7 mapping, DMCP-00 §5 grammar loosening
  for leading-underscore module names, DMCP-00 §3 ToolDescriptor
  `description` field, DMCP-01 §7 form-field annotations, DMCP-03 §5/§8
  body-shape clarifications) — each recorded as a dated §15 entry on
  the owning phase doc.
- Two errata entries (`ERRATA-001` optgroup `ChoiceField` rendering,
  `ERRATA-002` tool-name grammar over-restriction) both resolved 🟢
  in-session.

### Known limitations (future-phase candidates)

- No SSE / streaming responses; every MCP response is a single JSON
  body.
- No `resources/subscribe`; deferred pending Django Channels
  integration.
- No OAuth-token-as-bearer; the OAuth-mints-MCPAPIKey pattern works
  today via `docs/ops/oauth.md`. The bearer-resolver refactor that
  would enable raw OAuth tokens is a future DMCP-05 candidate.
- `MCPAPIKey.allowed_tools` is exact-match; no wildcards / scopes.
- No rate limiting in-package; operators use a reverse proxy.
