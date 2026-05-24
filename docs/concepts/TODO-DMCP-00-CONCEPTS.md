# TODO-DMCP-00-CONCEPTS — Foundational concepts and vocabulary

> **Status:** **ratified 2026-05-22**, amended 2026-05-22, 2026-05-23 (×2)
> (see §15). All sections of this doc are binding. Per `CLAUDE.md`, this doc
> is the binding artifact for everything below; the `README.md` and
> `docs/README.md` rollups are informative.

## §0 — Authority policy

`django-mcp` integrates two externally-authored grammars. Every concept
declared in this doc that crosses one of those grammar boundaries MUST appear
in the matrix at §4, with an explicit `AuthorityRelationship` value. A
concept that does NOT appear there is internally owned by this package and
falls under spec-before-code §15 amendment rules.

| Upstream grammar | Wire revision / version | Crawl boundary |
|------------------|-------------------------|----------------|
| Django | ≥ 4.2, < 6.0 (tested against 5.2.x) | Crawlable; canonical for `Model`, `View`, `URLconf`, `ModelAdmin`, `Form`, `Permission`, `User`. |
| Model Context Protocol | 2025-03-26 | Treated as a black-box wire spec. Crawl the spec text when amending §5; do not paraphrase it into local glossary. |

The softoboros MCP module at `/Users/iraabbott/softoboros/backend/mcp/` is
**prior art**, not an upstream authority. Consult for shape; do not cite as
canonical.

## §1 — Purpose

Provide a reusable Django application that:

1. Discovers Django's already-registered surface area (admin registrations,
   URL patterns, model registries, app configs).
2. Applies a small set of **derivation rules** to that surface to emit MCP
   tools, resources, and prompts mechanically.
3. Serves the resulting MCP surface over a standard MCP transport, behind
   Django's existing auth and permission machinery.

Said inversely: a Django project that adds `django_mcp` to `INSTALLED_APPS`
and includes one URL include SHOULD acquire an MCP surface that tracks its
existing views and admin registrations with no per-tool boilerplate.

## §2 — Problem statement

Hand-written MCP servers for Django projects (the prior-art shape in
`/Users/iraabbott/softoboros/backend/mcp/api.py:1-12633`) have three failure
modes that compound over time:

1. **Drift between view and tool.** A Django view evolves (new field, new
   permission, renamed action), the MCP tool does not. Six months later the
   tool surfaces a stale schema and no test fails — the schemas were never
   linked.
2. **Permission divergence.** A view enforces permissions through
   `LoginRequiredMixin` / `PermissionRequiredMixin` / DRF permission classes
   / admin `has_*_permission`. The hand-written MCP tool reproduces a subset
   of those checks. The subset rots.
3. **Surface explosion.** Every new model adds N tools (list, retrieve,
   create, update, delete, plus admin actions). Authoring them by hand is
   ~20 lines each that all want to say the same thing.

The reframe: Django already solved (1)–(3) for the HTTP surface via the
admin, the URL resolver, and the permission system. The MCP surface SHOULD
fall out of those primitives by the same mechanism.

Evidence pins for the above failure modes:
- Drift: `softoboros/backend/mcp/api.py` defines `TOOLS` as a literal list,
  edited by hand alongside the views it shadows. Search for `Tool(` to count
  the duplication.
- Permission divergence: `softoboros/backend/mcp/roles.py:1-426` is a
  parallel permission system that exists *because* the MCP surface could not
  reuse the Django one.
- Surface explosion: same `api.py`, ~12.6k lines, is the cost of writing
  each tool by hand.

## §3 — Canonical glossary

Definitions cite their authoritative source. The relationship marker follows
the `CLAUDE.md` discipline.

- **Django Model** — As defined in Django's `django.db.models.Model`; used
  without modification.
- **Django View** — As defined in Django; covers function-based views (FBVs),
  class-based views (CBVs subclassing `django.views.View`), and DRF
  `APIView` / `ViewSet` subclasses when DRF is installed. Used without
  modification.
- **URL pattern** — As defined in Django's `django.urls.URLPattern` /
  `URLResolver`. Used without modification.
- **ModelAdmin** — As defined in `django.contrib.admin.ModelAdmin`. Used
  without modification.
- **Permission** — As defined in `django.contrib.auth` and DRF's
  permission classes. Used without modification.
- **MCP tool** — As defined in MCP 2025-03-26 §"Tools"; used without
  modification on the wire. The local in-process representation is a
  `ToolDescriptor` (Owned by DMCP-00; does not exist upstream).
- **MCP resource** — As defined in MCP 2025-03-26 §"Resources"; used without
  modification on the wire.
- **MCP prompt** — As defined in MCP 2025-03-26 §"Prompts"; used without
  modification on the wire.
- **ToolDescriptor** — Owned by DMCP-00; does not exist upstream yet. The
  in-process record produced by a derivation rule, carrying: `tool_name`
  (string, namespaced), `description` (short human-readable summary
  supplied by the derivation rule — see the 2026-05-23 §15 amendment
  below; carried verbatim into the MCP `tools/list` wire response per
  DMCP-04 §5.3.1), `input_schema` (JSON Schema derived from a Django
  form / serializer / view signature), `output_schema` (JSON Schema derived
  from the view's response or model), `handler` (async callable), `auth_check`
  (callable resolving Django/DRF permissions), `origin` (which derivation
  rule produced this descriptor, e.g. `admin.list:auth.User`).
- **Derivation rule** — Owned by DMCP-00; does not exist upstream. A
  callable `rule(source) -> Iterable[ToolDescriptor]` where `source` is a
  Django registry entry (an admin registration, a URL pattern, a model class,
  an app config). The package ships a small library of rules; downstream
  projects MAY register more via the entry-point or app-config hook defined
  in DMCP-02.
- **Tool namespace** — Owned by DMCP-00. Tool names follow the shape
  `<rule_family>.<verb>:<dotted_target>`, e.g.
  `admin.list:auth.User`, `admin.action:blog.Post.publish`,
  `view.invoke:billing.InvoiceDetailView`. The grammar is frozen in §5.
- **MCP API key** — Owned by DMCP-00. An auth credential dedicated to MCP
  callers, distinct from Django's session / DRF token machinery, so that
  revoking an MCP key cannot lock a human user out of the admin. Schema
  ratified in DMCP-04 (Transport).
- **Authority boundary** — As defined in `../../CLAUDE.md` §"Standards
  integration"; used without modification.

## §4 — Source-of-truth map (AuthorityRelationship matrix)

| Concept | Upstream authority | Local representation | Mutation rights | Divergence policy | Downstream consumers | Conformance test owner |
|---------|--------------------|----------------------|-----------------|-------------------|----------------------|------------------------|
| MCP tool wire payload | MCP 2025-03-26 | `mirror` — emitted verbatim | None on wire grammar; extension fields confined to `__django_mcp` namespace | A new MCP revision lands as a §15 amendment first | All MCP clients | DMCP-04 |
| MCP resource wire payload | MCP 2025-03-26 | `mirror` | None | Same as above | All MCP clients | DMCP-04 |
| Django ModelAdmin | Django | `derive` — walked to produce ToolDescriptors | None on the admin grammar; package only reads it | A new Django version that changes ModelAdmin's introspection surface lands as a §15 amendment | DMCP-01 derivation rules | DMCP-01 |
| Django URL pattern | Django | `derive` — walked to discover endpoints | None on URL grammar | Same as above | DMCP-02 derivation rules | DMCP-02 |
| Django Permission / DRF Permission | Django + DRF | `derive` — resolved at MCP-call time | None | Permission semantics MUST match the HTTP surface bit-for-bit; INV-DMCP-3 | All phases | DMCP-01 / DMCP-02 |
| ToolDescriptor | (none — locally owned) | `own` | Full, gated by §15 amendments | n/a | All phases | DMCP-00 |
| Tool name grammar | (none — locally owned) | `own` | Full, gated by §15 amendments and frozen-enum §5 policy | n/a | All phases | DMCP-00 |
| MCPAPIKey model | (none — locally owned; informed by softoboros prior art) | `own` | Full | n/a | DMCP-04 | DMCP-04 |

Any concept added in a later phase that crosses an authority boundary MUST
extend this table in a §15 amendment to *this* doc, not buried in the later
phase. Boundary state is global; a later phase doesn't get to redefine it
silently.

## §5 — Frozen enum: tool-name grammar

**Registration policy:** Standards Action. Adding a new `<rule_family>` or
`<verb>` value requires a §15 amendment here and a ratification commit. The
component-character classes below are also frozen-enum-equivalents — loosening
or tightening them requires a §15 amendment (the 2026-05-23 entry is the
reference shape).

```
tool_name        = rule_family "." verb ":" dotted_target
rule_family      = "admin" / "view" / "model" / "action" / "rpc"
verb             = "list" / "retrieve" / "create" / "update" / "delete"
                 / "search" / "invoke" / "action"
dotted_target    = 1*( id_start *( id_continue ) "." ) target_leaf
target_leaf      = 1*( id_continue )
id_start         = ALPHA / "_"
id_continue      = ALPHA / DIGIT / "_"
```

The grammar aligns with Python identifier rules (PEP 3131 minus the
non-ASCII letter set): both prefix components and the leaf may start with
ALPHA or `_`, and continue with ALPHA, DIGIT, or `_`. A leading DIGIT is
rejected in both positions (`1bad.User` is not a valid tool name).

Rationale for accepting `_` as `id_start`: legitimate Python module names
include `__main__` (script entry points), `_internal` (private submodules),
and `__init__` (package init — though this rarely surfaces in `__module__`
of an exposed view). The previous restriction "prefix component must start
with ALPHA" rejected those silently and would have forced consumers to
either rename their modules or run with a lossy sanitisation layer. See
the 2026-05-23 §15 amendment and `ERRATA-002`.

Examples (illustrative; not all are implemented at DMCP-00):

- `admin.list:auth.User`
- `admin.retrieve:auth.User`
- `admin.create:blog.Post`
- `admin.action:blog.Post.publish`
- `view.invoke:billing.InvoiceDetailView`
- `model.search:catalog.Product`
- `rpc.invoke:reports.monthly_revenue`

`rule_family = "admin"` is reserved for DMCP-01 derivations.
`rule_family = "view"` and `"model"` are reserved for DMCP-02.
`rule_family = "rpc"` is reserved for future hand-registered tools that do
NOT derive from a Django registry — they MUST still go through a derivation
rule registered in code, just one whose `source` is a Python callable rather
than a Django primitive.

## §6 — Frozen enum: derivation-rule families

**Registration policy:** Standards Action.

| Family | Source object | Phase | Notes |
|--------|---------------|-------|-------|
| `AdminListRule` | `admin.ModelAdmin` | DMCP-01 | Emits `admin.list:<app>.<Model>` per registered admin |
| `AdminRetrieveRule` | `admin.ModelAdmin` | DMCP-01 | Emits `admin.retrieve:<app>.<Model>` |
| `AdminCreateRule` | `admin.ModelAdmin` | DMCP-01 | Emits `admin.create:<app>.<Model>` |
| `AdminUpdateRule` | `admin.ModelAdmin` | DMCP-01 | Emits `admin.update:<app>.<Model>` |
| `AdminDeleteRule` | `admin.ModelAdmin` | DMCP-01 | Emits `admin.delete:<app>.<Model>` |
| `AdminActionRule` | `admin.ModelAdmin.actions` | DMCP-01 | Emits `admin.action:<app>.<Model>.<action_name>` |
| `ViewInvokeRule` | `URLPattern` resolving to a View | DMCP-02 | Emits `view.invoke:<dotted_view_path>` |
| `DRFViewSetRule` | DRF `ViewSet` | DMCP-02 | Emits one tool per detail/list/extra-action method |
| `ModelSearchRule` | `Model` | DMCP-02 | Emits `model.search:<app>.<Model>` over indexed fields |
| `RPCRule` | Python callable | future | Hand-registered tools whose `source` is a callable |

A derivation rule MUST be implemented as a Python class with a class method
`emit(source) -> Iterable[ToolDescriptor]`. The class is the unit of
"derivation rule" referenced elsewhere.

## §7 — Frozen enum: permission resolution outcomes

**Registration policy:** Standards Action.

When an MCP caller invokes a tool, the permission resolver returns exactly
one of:

- `ALLOW` — caller has all required permissions for the corresponding
  Django/DRF surface.
- `DENY` — caller is authenticated but lacks a required permission.
- `UNAUTHENTICATED` — caller has no valid `MCPAPIKey` (or other credential
  ratified in DMCP-04).
- `OUT_OF_SCOPE` — caller's MCP key is restricted to a subset of tools that
  does not include the requested one (key-level allowlist).

`OUT_OF_SCOPE` is distinct from `DENY` so that audit logs can separate "user
lacks the permission" from "this key was never allowed to ask".

## §8 — Frozen enum: transport modes

**Registration policy:** Specification Required (lives in DMCP-04; recorded
here only to reserve the names).

- `streamable-http` — MCP 2025-03-26 streamable HTTP, mounted under the URL
  include. The DMCP-04 default.
- `stdio` — for a `manage.py mcp_server` command analogous to the
  softoboros `backend/mcp_server.py:1-212` prior art. Optional.

## §9 — Invariants

These are the load-bearing rules. Every later phase's §9 inherits them. A
behaviour commit that touches an invariant MUST cite it by id and explain how
it is preserved (per `CLAUDE.md` execution discipline). Mutating an
invariant requires a §15 amendment here, before any behaviour commit.

- **INV-DMCP-1 (async/ORM boundary).** Every MCP tool handler is an
  `async def`. Every blocking Django ORM call inside a handler MUST be
  wrapped with `asyncio.to_thread()` or `asgiref.sync.sync_to_async`. A
  handler that performs a synchronous ORM call from the async context is a
  defect; tests covering this invariant assert on the wrapping discipline.
- **INV-DMCP-2 (no hand-written surface).** No `ToolDescriptor` exists in
  the running server that was not produced by a registered derivation rule.
  The package's hand-written-tool escape hatch is the `RPCRule` family
  (DMCP-future), which still routes through a rule — it does not bypass the
  registry.
- **INV-DMCP-3 (permission parity).** For every emitted ToolDescriptor whose
  source is a Django view or admin registration, the permission check
  enforced by the MCP handler MUST be equivalent to the check the HTTP
  surface enforces for the same operation against the same user. "Equivalent"
  is asserted by a parity test: invoke the operation via Django test client
  and via the MCP handler with the same user; the auth outcomes match.
- **INV-DMCP-4 (no silent restatement).** A concept defined upstream (Django
  or MCP) appears in `django_mcp/` only as either (a) an import / direct
  reference, or (b) a derivation that cites the upstream source in its
  docstring. Re-defining a Django primitive locally is a defect.
- **INV-DMCP-5 (single derivation pass).** Tool discovery happens once per
  process boot, after Django's `ready()` signal fires. Tools are not
  rediscovered per-request. A change to admin or URL registration that
  happens after boot is not visible to MCP until the process restarts. This
  matches Django's own URLconf semantics.
- **INV-DMCP-6 (namespaced extensions).** Any field this package adds to a
  payload that crosses the MCP wire MUST live under the
  `__django_mcp` object key. Top-level wire fields belong to MCP.
- **INV-DMCP-7 (audit trail).** Every tool invocation produces a log line at
  `INFO` containing: tool name, MCP key id, resolved Django user id,
  permission outcome (§7), wall-clock duration, and a stable correlation id
  passed through to any Django log lines emitted during the handler. The
  log shape is owned by DMCP-04; this invariant only locks the *fact* of the
  audit, not its serialisation.

## §10 — Reconciliation with adjacent Django / MCP primitives

This section records decisions where `django-mcp` chose one of several
reasonable couplings to Django or MCP. Each decision is binding under §15.

- **Auth credential separation.** MCP callers authenticate with a dedicated
  `MCPAPIKey` (DMCP-04), not by reusing Django session cookies or DRF tokens.
  Rationale: an MCP key is a machine credential whose blast radius is the
  tools it is allowed to call; revoking it MUST NOT log the human user out
  of the admin or invalidate their session. This decision is informed by
  the softoboros prior art at
  `/Users/iraabbott/softoboros/backend/mcp/models.py`.
- **Permission semantics live in Django.** `django-mcp` does NOT introduce a
  role system. It resolves permissions by delegating to Django's
  `User.has_perm` / `has_module_perms` and to ModelAdmin's
  `has_{add,change,delete,view}_permission` / DRF permission classes for
  views. This is the explicit non-replication of
  `softoboros/backend/mcp/roles.py`.
- **Schema source.** Tool input schemas derive from, in order of preference:
  (1) a DRF serializer when the source view is a DRF view; (2) a Django
  Form / ModelForm when the source is an admin add/change view; (3) the
  underlying Model's fields when no form is available. DMCP-01 and DMCP-02
  refine this; DMCP-00 only freezes the precedence.
- **Discovery scope.** Tools are derived from `admin.site` (the default
  AdminSite) and from URL patterns reachable from `ROOT_URLCONF`. A project
  with multiple AdminSite instances may extend discovery via a setting
  defined in DMCP-01; the default is the canonical site.
- **No background work at import time.** App `ready()` registers signal
  handlers and primes the registry; the actual derivation pass runs lazily
  on the first MCP request, gated by a per-process lock. This avoids
  ordering hazards with apps that register admin entries after `django_mcp`
  loads.

## §11 — Non-goals

- **Not** a generic OpenAPI-to-MCP bridge. Discovery walks Django registries,
  not OpenAPI documents.
- **Not** a GraphQL-style mass introspection endpoint. Tools are
  individually addressable; there is no `query { everything }` surface.
- **Not** a runtime permission editor. Permissions are read from Django;
  this package does not let an MCP caller mutate the Django permission model
  unless the caller has the admin permissions to do so via the normal admin
  surface (in which case it is just `admin.update:auth.Permission`).
- **Not** an attempt to fork the softoboros MCP module. The two will coexist
  until softoboros migrates onto `django-mcp`; the migration is out of scope
  for this repository.

## §12 — Acceptance checklist

A conforming DMCP-00 ratification MUST satisfy:

- **DMCP00-a.** `CLAUDE.md` and this doc are present, cross-link each other,
  and agree on the discipline.
- **DMCP00-b.** The package imports cleanly under Python ≥ 3.11 and Django
  ≥ 4.2 with no Django configuration (i.e. `import django_mcp` does not
  require `DJANGO_SETTINGS_MODULE`).
- **DMCP00-c.** `django_mcp.apps.DjangoMcpConfig` is the AppConfig and is
  discoverable via `INSTALLED_APPS = ["django_mcp"]` with no further
  configuration.
- **DMCP00-d.** Invariants INV-DMCP-1..7 are written and each one names the
  test (or test family) that will enforce it once code lands.
- **DMCP00-e.** The §4 AuthorityRelationship matrix names a conformance test
  owner for every row; no row is "TBD".
- **DMCP00-f.** The §5 tool-name grammar parses every example in §5 and
  rejects names that violate the grammar. (Test target: a parser added in
  DMCP-01 alongside the first emitter.)
- **DMCP00-g.** This doc's §15 carries a dated ratification entry.

## §13 — Files cited

- `../../CLAUDE.md` — spec-before-code discipline, AuthorityRelationship
  matrix definition, RFC 2119 keywords.
- `../../django_mcp/__init__.py` — package entry point.
- `../../django_mcp/apps.py` — AppConfig.
- `/Users/iraabbott/softoboros/backend/mcp/api.py` — prior art for hand-
  written tool surface (cited in §2 as the failure mode being addressed).
- `/Users/iraabbott/softoboros/backend/mcp/models.py` — prior art for
  `MCPAPIKey` shape (cited in §10).
- `/Users/iraabbott/softoboros/backend/mcp/roles.py` — prior art for the
  parallel-permission failure mode (cited in §2 and §10).
- `/Users/iraabbott/softoboros/backend/mcp/http_transport.py` — prior art
  for streamable-HTTP transport (cited in §8).
- `/Users/iraabbott/softoboros/backend/mcp_server.py` — prior art for STDIO
  transport (cited in §8).

## §14 — Unblocks

Once §15 has a dated ratification entry, the following work is unblocked:

- **DMCP-01 (Admin → MCP tools).** Has draft form already; DMCP-00's §3, §5,
  §6, §7, §9, §10 are the vocabulary and invariants DMCP-01 will reference.
- **DMCP-04 (Transport).** Can start authoring once DMCP-00 freezes §8 and
  the MCPAPIKey row in §4.

## §15 — Change log

- **2026-05-22** — Initial ratification. All sections (§0–§14) frozen. Signed
  off by: repository owner (`abbott.ira.r@gmail.com`). Ratified jointly with
  the project's `CLAUDE.md` discipline and DMCP-01. Unblocks DMCP-01
  implementation and DMCP-04 (Transport) authoring. Commit: pending (this
  doc and CLAUDE.md ratify together; ratification commit will cite
  `DMCP00:` and carry the canonical SHA).

- **2026-05-23** — Amendment to §5 (tool-name grammar). **Standards Action.**
  Signed off by: repository owner (`abbott.ira.r@gmail.com`).

  **Change.** Loosen the prefix-component rule so it matches Python's own
  identifier rules. The old grammar `dotted_target = 1*( ALPHA *( ALPHA /
  DIGIT / "_" ) "." ) target_leaf` is replaced by `dotted_target = 1*(
  id_start *( id_continue ) "." ) target_leaf` with `id_start = ALPHA /
  "_"` and `id_continue = ALPHA / DIGIT / "_"`. The leaf rule continues to
  use `id_continue` (which is unchanged from the prior `1*( ALPHA / DIGIT
  / "_" )`).

  **What this admits.** Module paths whose components begin with `_`:
  `__main__.hello`, `myproj._internal.views.View`, `pkg.__init__.X` (the
  last one rarely appears in `__module__` in practice, but the grammar no
  longer rejects it).

  **What this still rejects.** A leading DIGIT (`1bad.User`), non-ASCII
  characters, empty components, whitespace, and components containing
  characters outside `id_continue`. The DMCP01-i / DMCP02-i conformance
  tests retain their negative-case fixtures.

  **No §9 invariant changes.** INV-DMCP-1..7 are preserved. INV-DMCP-2 (no
  hand-written surface) and INV-DMCP-4 (no silent restatement) are
  reinforced: the grammar now matches Python identifier rules, removing
  the divergence between "what a Python module can be named" and "what
  this package admits as a tool-name component".

  **Downstream code changes (sequenced separately, post-amendment).**
  `django_mcp/names.py` `_validate_prefix_component` accepts leading `_`
  in addition to ALPHA. `django_mcp/drf.py`'s `_sanitize_component`
  workaround is removed (silent normalisation is the correctness risk
  this amendment exists to eliminate). `tests/test_names.py` adds a
  positive case for `view.invoke:__main__.hello` and updates the
  `1bad.User` rejection-reason fragment from `"ALPHA"` to `"ALPHA or
  '_'"`.

  **Errata link.** Resolves `ERRATA-002` (which carried `EOQ-001-ERRATA-
  002`); the errata transitions to 🟢 once the code changes land.

  Commit: pending (carries `DMCP00:` subject prefix).

- **2026-05-23** — Amendment to §3 (ToolDescriptor field set). **Standards
  Action.** Signed off by: repository owner (`abbott.ira.r@gmail.com`).

  **Change.** Add a sixth field `description: str` to the `ToolDescriptor`
  dataclass. Previously the field set was `(tool_name, input_schema,
  output_schema, handler, auth_check, origin)`; it is now `(tool_name,
  description, input_schema, output_schema, handler, auth_check, origin)`.
  `description` is a short human-readable summary carried verbatim into
  the MCP `tools/list` wire response per DMCP-04 §5.3.1.

  **Motivation.** DMCP-04 §5.3.1 left the wire `description` field on
  `tools/list` derived from `origin` as a fallback (`"Derived MCP tool
  from {origin}"`). LLM MCP callers materially benefit from richer text:
  Django's `Model._meta.verbose_name` / `verbose_name_plural`,
  view docstrings, and `@admin.action(description=...)` are all readily
  available at derivation time. The fallback stays as the
  fallback-of-the-fallback (when a rule has nothing to say); rules now
  ALSO have a place to put their own text.

  **Description-source map (informative; rules pin their own choices
  in their §3 glossary entries):**

  | Rule | Description source |
  |------|--------------------|
  | `AdminListRule` | `f"List {Model._meta.verbose_name_plural}."` |
  | `AdminRetrieveRule` | `f"Retrieve a {Model._meta.verbose_name} by primary key."` |
  | `AdminCreateRule` | `f"Create a new {Model._meta.verbose_name}."` |
  | `AdminUpdateRule` | `f"Update a {Model._meta.verbose_name} by primary key."` |
  | `AdminDeleteRule` | `f"Delete a {Model._meta.verbose_name} by primary key."` |
  | `AdminActionRule` | `action.short_description` if set, else `f"Apply '{action_name}' to selected {verbose_name_plural}."` |
  | `ViewInvokeRule` (FBV) | First line of `view.__doc__` if set, else `f"Invoke {dotted_view_path}."` |
  | `ViewInvokeRule` (CBV, narrowed verb) | Same FBV rule applied to the class. |
  | `DRFViewSetRule` (CRUD verbs) | First line of ViewSet `__doc__` if set, else templated per verb. |
  | `DRFViewSetRule` (`@action`) | The decorated method's docstring / `description` kwarg, else `f"DRF action '{action_name}' on {dotted_viewset_path}."` |
  | `ModelSearchRule` | `f"Search {Model._meta.verbose_name_plural} by indexed fields."` |

  Operator-supplied descriptions for `ModelSearchRule` (via a future
  `description` key on the `DJANGO_MCP_MODEL_SEARCH` entry shape per
  DMCP-02 §10.2) are a SEPARATE future amendment to DMCP-02; this
  amendment touches DMCP-00 only.

  **No §9 invariant changes.** INV-DMCP-2 (no hand-written surface),
  INV-DMCP-4 (no silent restatement), and INV-DMCP-7 (audit trail) are
  preserved: descriptions derive from Django primitives the model
  already owns, and the audit log records the derived `description`
  via the standard tool-invocation trail.

  **Wire compatibility.** DMCP-04 §5.3.1 falls back to the old derived
  text when a rule supplies an empty `description` (defensive — rules
  on the implementation side fill the field, but the wire layer keeps
  the fallback to honour the previously-ratified §5.3.1 contract).
  No existing MCP client breaks.

  **Downstream code changes (sequenced together post-amendment).**
  `django_mcp/derivation.py` `ToolDescriptor` gains the
  `description: str` field. Each rule constructor in `admin.py`,
  `views.py`, `drf.py`, `search.py` is updated to pass the derived
  text per the table above. `django_mcp/dispatch.py` `_tools_list`
  emits `descriptor.description` (falling back to the §5.3.1 derived-
  from-origin text when empty).

  Commit: pending (carries `DMCP00:` subject prefix; lands the spec
  amendment first, then the code changes per `CLAUDE.md` discipline).
