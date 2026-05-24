# TODO-DMCP-03-RESOURCES-PROMPTS — Resources and prompts

> **Status:** **ratified 2026-05-23**, **amended 2026-05-23** (see §15).
> DMCP-00, DMCP-01, and DMCP-02 ratified earlier (2026-05-22, 2026-05-22,
> 2026-05-23 respectively). This phase reuses their `DerivationRule`
> plumbing and per-call permission-resolver shape — invariants
> INV-DMCP-1..7, INV-DMCP01-1..5, and INV-DMCP02-1..8 are inherited
> without modification unless §10 records a named deviation.

## §0 — Authority policy

DMCP-03 introduces no new external grammars beyond what DMCP-00 §0 and
DMCP-02 §0 already named. It adds two new MCP wire-grammar concepts
(resources, prompts) on top of the same MCP 2025-03-26 revision.

| Upstream grammar | Wire revision / version | Crawl boundary |
|------------------|-------------------------|----------------|
| Django | ≥ 4.2, < 6.0 (inherited) | `Model._meta`, `FileField` / `ImageField`, `admin.ModelAdmin.actions` are now load-bearing in addition to what DMCP-01/02 read. |
| Django REST Framework | ≥ 3.14, optional (inherited) | Not used by DMCP-03 directly. |
| Model Context Protocol | 2025-03-26 (inherited) | §"Resources" and §"Prompts" sub-grammars are now load-bearing. |

Two notes pinned at the policy boundary:

- DMCP-03 deliberately consumes the MCP **resource** grammar's two sub-
  shapes — *concrete URIs* and *URI templates* — without paraphrasing
  either. Discovery emits templates wherever a Django registry produces
  parameterised content (e.g. `model/<app>.<Model>/{pk}`); concrete URIs
  are reserved for project-singletons (e.g. `meta/openapi.json` if
  ratified later).
- The **subscribe** sub-grammar of MCP resources (live updates) is out of
  scope for this phase. See §11.

## §1 — Purpose

Extend the "MCP falls out of registered surface" thesis from *tools*
(DMCP-01, DMCP-02) to the other two MCP surface types: *resources*
(URI-addressable read-only content) and *prompts* (pre-defined message
templates the user can attach to a conversation).

Concretely, a project that has:

- A `Post` model registered in `admin.site`,
- A `Post.cover_image` `ImageField`,
- An `@admin.action(description="Publish selected posts.")` named `publish`,

SHOULD acquire — with no additional configuration beyond what DMCP-01 / 02
already required — the following MCP surface beyond the tool layer:

- Resource template `django-mcp://model/blog.Post/{pk}` — read any Post
  instance as JSON.
- Resource template `django-mcp://field/blog.Post/{pk}/cover_image` —
  read the binary contents of one Post's cover image.
- Prompt `prompt.admin.blog.Post.publish` — a template that fills in the
  pks the user wants to publish and routes through the corresponding
  `admin.action:blog.Post.publish` tool.

The unifying property continues: every emitted resource and prompt has a
discoverable derivation rule pointing at a Django registry entry.

## §2 — Problem statement

A hand-written MCP server that exposes resources runs the same drift
failure modes DMCP-01 §2 enumerated for tools, plus one specific to URI-
addressable content:

1. **URI drift.** A model's PK type changes (Auto → UUID), or a model is
   renamed; the hand-written resource URI `mcp://blog/post/123` now points
   at nothing, and the corresponding hand-written `read_handler` either
   returns a 404 or — worse — silently coerces. The HTTP/admin surface
   tracks the schema change; the hand-written MCP resource layer doesn't.

2. **Mime-type drift.** A `FileField` whose `upload_to` callable changed
   its content-type expectations (e.g. `images/` accepts `.webp` now)
   still emits `image/jpeg` from the hand-written resource because nobody
   updated the static dispatch. INV-DMCP03-6 (mime-type honesty) closes
   this.

For prompts, the failure mode is more subtle but real: a hand-authored
prompt that says "invoke the `publish_posts` action on selected items"
keeps using the *old* action name after a rename, and the prompt's body
silently tells the model to call a tool that no longer exists. Prompts
derived from `@admin.action` registrations stay in sync with the
underlying action name by construction (INV-DMCP03-7).

Evidence pin:
- The softoboros prior art at `/Users/iraabbott/softoboros/backend/mcp/`
  exposes resources for a small set of hand-picked models, each defined
  in `api.py` next to its corresponding tools, with no derivation rule.
  Adding a new resource-eligible model requires editing both the tool
  list AND the resource list — the duplication is the cost.

## §3 — Glossary (additions only; rest inherited from DMCP-00 §3 / DMCP-01 §3 / DMCP-02 §3)

- **MCP resource** — As defined in MCP 2025-03-26 §"Resources"; used
  without modification on the wire. Carries `uri`, `name`, `description?`,
  `mimeType?`. Distinguished from **MCP resource template** by the
  presence of `{placeholder}` segments in the URI.
- **MCP resource template** — As defined in MCP 2025-03-26
  §"Resources/Templates"; used without modification. Listed via
  `resources/templates/list`; client expands placeholders before calling
  `resources/read`.
- **MCP prompt** — As defined in MCP 2025-03-26 §"Prompts"; used without
  modification. Carries `name`, `description?`, `arguments?` (a list of
  named arguments). Returned to the client via `prompts/list`; rendered
  for a specific argument binding via `prompts/get`.
- **ResourceDescriptor** — Owned by DMCP-03; does not exist upstream. The
  in-process record produced by a resource-derivation rule, carrying:
  `uri` (string, possibly a URI template), `name` (short human label),
  `description` (one-line summary), `mime_type` (MIME string), `is_template`
  (bool — true when `uri` contains `{placeholder}` segments), `read_handler`
  (async callable resolving a concrete URI to bytes or a JSON-serialisable
  Python object), `auth_check` (callable resolving Django permissions —
  same shape as `ToolDescriptor.auth_check`), `origin` (which derivation
  rule produced this descriptor).
- **PromptDescriptor** — Owned by DMCP-03; does not exist upstream. The
  in-process record produced by a prompt-derivation rule, carrying:
  `name` (namespaced per §5.3), `description` (one-line summary),
  `arguments` (list of `PromptArgument`), `render_handler` (sync callable
  taking an argument binding dict, returning a list of message dicts per
  the MCP prompt-message wire shape), `auth_check`, `origin`.
- **PromptArgument** — Owned by DMCP-03. A simple `(name, description,
  required)` triple. Argument *types* are out of scope at the prompt
  layer — prompts are template strings, not validated input — but the
  client may render the prompt UI with type hints inferred from the
  underlying tool's input schema when the prompt is derived from a tool.
- **Resource URI scheme** — Owned by DMCP-03; does not exist upstream.
  See §5 for the frozen grammar. Uses the `django-mcp:` scheme to avoid
  colliding with the project's HTTP surface.
- **ResourceRule** — Owned by DMCP-03. Abstract derivation rule whose
  `emit` returns `ResourceDescriptor` instances. Parallel to
  `DerivationRule` in DMCP-00 §3 but specialised for resources.
- **PromptRule** — Owned by DMCP-03. Same shape, but for prompts.

## §4 — Source-of-truth map (additions only)

| Concept | Upstream authority | Local representation | Mutation rights | Divergence policy | Downstream consumers | Conformance test owner |
|---------|--------------------|----------------------|-----------------|-------------------|----------------------|------------------------|
| MCP resource wire payload | MCP 2025-03-26 | `mirror` — emitted verbatim, no local extensions on the wire | None | MCP revision bump lands as a §15 amendment to DMCP-00 §0 first | All MCP clients | DMCP-04 |
| MCP resource template wire payload | MCP 2025-03-26 | `mirror` | None | Same as above | All MCP clients | DMCP-04 |
| MCP prompt wire payload | MCP 2025-03-26 | `mirror` | None | Same as above | All MCP clients | DMCP-04 |
| Django `Model._meta` (for resource emission) | Django | `derive` — walked to produce per-instance resource templates | None | A model whose `_meta.pk` shape mutates yields a different URI template; INV-DMCP03-1 catches drift between discovery passes | DMCP-03 ModelResourceRule | DMCP-03 |
| Django `FileField` / `ImageField` | Django | `derive` — walked to emit field-content resource templates; mime-type read via `mimetypes.guess_type` plus the field's own content-type hints when present | None | A field whose `upload_to` widens content-type acceptance does NOT auto-widen the resource's declared `mime_type`; declared mime is conservative (`application/octet-stream` for unknown) — INV-DMCP03-6 | DMCP-03 FileFieldResourceRule | DMCP-03 |
| `admin.action.short_description` | Django | `derive` — extracted as the prompt body's natural-language description | None | A `short_description` mutation between discovery passes changes the prompt's `description` AND the body's natural-language text | DMCP-03 AdminActionPromptRule | DMCP-03 |
| ResourceDescriptor | (none — locally owned) | `own` | Full, gated by §15 amendments | n/a | All resource phases | DMCP-03 |
| PromptDescriptor | (none — locally owned) | `own` | Full, gated by §15 amendments | n/a | All prompt phases | DMCP-03 |
| Resource URI scheme `django-mcp:` | (none — locally owned) | `own` | Full, gated by §15 amendments | The scheme name itself is frozen; the per-host taxonomy in §5 is Specification Required | All resource phases | DMCP-03 |

## §5 — Frozen output surface

### §5.1 — Resource URI grammar

**Registration policy:** Standards Action for the scheme and the
host-taxonomy values (`model`, `field`, `admin`, `meta`, `static`); the
sub-target structure within a host is Specification Required.

```
resource_uri    = scheme "://" host "/" target [ "/" sub_target ]
                  [ "?" query ]
scheme          = "django-mcp"
host            = "model" / "field" / "admin" / "meta" / "static"
target          = component *( "." component )
component       = id_start *( id_continue )           ; DMCP-00 §5 grammar
sub_target      = segment *( "/" segment )
segment         = unreserved / pct-encoded / "{" placeholder "}"
placeholder     = 1*( ALPHA / DIGIT / "_" )
query           = ( unreserved / pct-encoded / "&" / "=" )*
```

`id_start` / `id_continue` are reused from DMCP-00 §5 (post-2026-05-23
amendment): leading `_` permitted.

Worked examples (all DMCP-03-emittable):

- `django-mcp://model/auth.User/{pk}` — read one User as JSON.
- `django-mcp://model/blog.Post/{pk}` — read one Post.
- `django-mcp://field/blog.Post/{pk}/cover_image` — read a Post's cover
  image bytes.
- `django-mcp://admin/blog.Post/{pk}` — read a Post's admin-projected
  representation (the change-page's serialised field set, NOT the
  rendered HTML).

Out of scope for DMCP-03 (reserved hosts; emission belongs to later
phases):

- `django-mcp://meta/<...>` — reserved for self-describing introspection
  resources (e.g. `meta/openapi.json`).
- `django-mcp://static/<path>` — reserved for `STATIC_ROOT` access.

### §5.2 — Resources from registered models

For every model `M` whose admin is registered (intersection of
`admin.site._registry.keys()` and `M._meta.concrete_model is not None`),
the **ModelResourceRule** emits:

| Resource | URI template | Mime type | Read behaviour |
|----------|--------------|-----------|----------------|
| Per-instance | `django-mcp://model/<app>.<Model>/{pk}` | `application/json` | Returns the same dict that `admin.retrieve:<app>.<Model>` returns (the visible-field-set projection per INV-DMCP01-3) |

A model that is *not* registered in admin but is named in
`DJANGO_MCP_RESOURCE_MODELS` (a new setting introduced by this phase)
also gets a per-instance resource template, with permission default
`<app>.view_<model_name>` (same default as `model.search:` in DMCP-02
§5.4).

### §5.3 — Resources from FileField / ImageField

For every concrete `FileField` (or subclass — `ImageField` included) on a
model `M` that participates in §5.2's resource emission, the
**FileFieldResourceRule** emits:

| Resource | URI template | Mime type | Read behaviour |
|----------|--------------|-----------|----------------|
| Per-instance per-field | `django-mcp://field/<app>.<Model>/{pk}/<field_name>` | Best-effort via `mimetypes.guess_type(file.name)`; falls back to `application/octet-stream` (INV-DMCP03-6) | Returns the file's binary content; permission check inherits the model's view permission |

A field whose `upload_to` produces files on a remote backend (S3,
Google Cloud Storage) is handled identically — the handler calls
`file.read()` through Django's storage abstraction. Large-file streaming
is out of scope for this phase (see §11).

### §5.4 — Prompts from admin actions

For every `@admin.action`-decorated function (or string-named action
method) reachable from a registered `ModelAdmin`, the
**AdminActionPromptRule** emits:

| Prompt | Name | Description | Arguments | Body shape |
|--------|------|-------------|-----------|------------|
| Per-action | `prompt.admin.<app>.<Model>.<action_name>` | `action.short_description` (verbatim if set; else a templated default `"Invoke <action_name> on selected <Model> instances."`) | `[{name: "pks", description: "Primary keys to act on", required: true}]` | A single `user` message per §7.3, whose body instructs the assistant to invoke `admin.action:<app>.<Model>.<action_name>` with the bound `pks`. Multi-message bodies are reserved for a future §15 amendment to §7.3 (see 2026-05-23 amendment in §15). |

Per INV-DMCP03-7, the prompt's name and the underlying tool name change
together: rename the admin action method, both names change in the next
discovery pass.

### §5.5 — User-registered prompts (`DJANGO_MCP_PROMPTS`)

Each entry in the `DJANGO_MCP_PROMPTS` setting (default: empty list) is
a dict per §10.5 (frozen shape). The **UserPromptRule** parses each entry
into a `PromptDescriptor` and emits it with name `prompt.user.<slug>`
(where `<slug>` is the entry's `name` field, validated against the
DMCP-00 §5 leaf-component grammar).

Project-defined prompts are explicit, not derived from any Django
registry — they're the escape hatch for workflows that span multiple
registered tools. They DO go through the rule machinery (INV-DMCP-2: no
hand-written surface): the rule's `source` is the dict entry itself, not
arbitrary Python code.

## §6 — Derivation rules (implementation contract)

DMCP-03 ships four concrete rules:

1. **`ModelResourceRule`** (host = `model`) — emits one `model/` resource
   per registered model.
2. **`FileFieldResourceRule`** (host = `field`) — emits one `field/`
   resource per FileField on a participating model.
3. **`AdminActionPromptRule`** (host = N/A; emits prompts) — one prompt
   per `@admin.action`.
4. **`UserPromptRule`** (host = N/A; emits prompts) — one prompt per
   `DJANGO_MCP_PROMPTS` entry.

Each `ResourceRule` / `PromptRule` lives in a new module-pair under
`django_mcp/`: `django_mcp/resources.py` and `django_mcp/prompts.py`.
The rule classes parallel `DerivationRule` from DMCP-00 §3 — they may
share the same ABC if the implementation chooses, with separate
`emit_resource(source)` / `emit_prompt(source)` methods, OR introduce
two ABCs `ResourceDerivationRule` and `PromptDerivationRule`. The
choice is recorded in §10.1.

Discovery (per `discover_now` in DMCP-02 §6 extension):

1. (existing — DMCP-01) walk `AdminSite._registry`, apply admin rules.
2. (existing — DMCP-02) walk URL tree, apply view/DRF rules.
3. (existing — DMCP-02) walk `DJANGO_MCP_MODEL_SEARCH`, apply
   `ModelSearchRule`.
4. (**new — DMCP-03**) walk registered models (`AdminSite._registry`'s
   keys ∪ `DJANGO_MCP_RESOURCE_MODELS`), apply `ModelResourceRule` then
   `FileFieldResourceRule`.
5. (**new — DMCP-03**) re-walk admin actions discovered in step 1,
   apply `AdminActionPromptRule`. (The walk re-uses the same dispatch as
   `AdminActionRule` from DMCP-01 §6, sharing the
   `_get_base_actions(source)` call.)
6. (**new — DMCP-03**) walk `DJANGO_MCP_PROMPTS`, apply
   `UserPromptRule`.

The whole walk still runs once per process under the registry lock
(INV-DMCP-5).

Settings introduced by this phase:

- `DJANGO_MCP_RESOURCE_MODELS: list[str | dict]` — entries identifying
  non-admin models that should still produce `django-mcp://model/...`
  resources. Default: `[]`. Entry shape mirrors §10.2 of DMCP-02 (string
  dotted path for defaults, dict for per-entry permission overrides).
- `DJANGO_MCP_RESOURCES_DISABLED: bool` — global kill-switch. Default:
  `False`. When `True`, the rules in §6 emit nothing — useful for
  deployments that want only the tool surface.
- `DJANGO_MCP_PROMPTS: list[dict]` — entries per §10.5. Default: `[]`.
- `DJANGO_MCP_FIELD_RESOURCE_MAX_BYTES: int` — declared upper bound for
  field-content reads. Default: `10 * 1024 * 1024` (10 MiB). Reads that
  exceed this raise `ValueError`; the handler does NOT silently
  truncate. INV-DMCP03-8 anchors this.

## §7 — Representation / format derivation

### §7.1 — Model resource representation

The body of `django-mcp://model/<app>.<Model>/{pk}` is the JSON dict
produced by `_serialize_instance(admin_or_none, request, instance)` from
`django_mcp/admin.py`. When the model has an admin registration, the
admin's `get_fields(request, instance)` provides the visible-field set
(INV-DMCP01-3); when there's no admin entry (the
`DJANGO_MCP_RESOURCE_MODELS` opt-in path), every concrete field
participates with no per-user redaction.

Mime type: `application/json` (frozen).

### §7.2 — Field resource representation

A `FileField`'s underlying file is read via the field's storage
(`instance.<field_name>.open("rb")` + `.read()`). The declared mime type
follows:

| Source | Mime type used |
|--------|----------------|
| `mimetypes.guess_type(file.name)` returns a known type | Use that type |
| Field is an `ImageField` AND file extension matches `mimetypes` known image type | Use that type |
| Otherwise | `application/octet-stream` |

The handler returns the bytes (not base64-encoded; the MCP wire layer
in DMCP-04 owns base64 encoding per the spec).

### §7.3 — Prompt body shape

A `PromptDescriptor.render_handler` takes a dict of argument bindings
and returns a list of message dicts. The minimum-viable shape for the
2026-05-23 DMCP-03 implementation:

```python
[
    {
        "role": "user",
        "content": {
            "type": "text",
            "text": "<rendered prompt body with {placeholders} substituted>",
        },
    },
]
```

Multi-message prompts (system + user, or chained turns) are allowed by
the wire spec but the DMCP-03-emitted prompts always produce
single-message lists. Promotion to multi-message is a future §15
amendment.

## §8 — Permission enforcement (additions only)

### §8.1 — Resource permissions

A resource's `auth_check` delegates to the same primitives DMCP-01 / 02
use:

- For `django-mcp://model/<app>.<Model>/{pk}` resources: the underlying
  `ModelAdmin.has_view_permission(request, obj)` when the model has an
  admin; otherwise `<app>.view_<model>` permission.
- For `django-mcp://field/<app>.<Model>/{pk}/<field_name>` resources:
  the model's view permission, AND a per-field readability check that
  matches what the admin's `get_fields(request, obj)` would return —
  fields excluded from the admin's visible set MUST NOT be readable as
  resources (INV-DMCP03-2).
- For `django-mcp://admin/<app>.<Model>/{pk}` (reserved future host):
  identical to the model resource for now.

### §8.2 — Prompt permissions

Prompts carry an `auth_check` whose semantics approximate the underlying
tool's:

- A prompt derived from `admin.action:<app>.<Model>.<name>` checks, in
  order: anonymous → `UNAUTHENTICATED`; missing `<app>.view_<model>` →
  `DENY`; any per-action `allowed_permissions` codename missing on the
  user → `DENY`; else `ALLOW`. For stock `ModelAdmin` subclasses this
  resolves identically to `ModelAdmin.has_view_permission(synthesised_
  request) + allowed_permissions` (the path the tool's auth_check
  takes). For `ModelAdmin` subclasses that **override**
  `has_view_permission` with logic beyond the standard model-perm
  check, the prompt's auth_check diverges from the tool's; see the
  2026-05-23 amendment in §15 for the rationale and scope.
- A user-registered prompt declares its permission in the entry shape
  (§10.5); the default is "authenticated" (anon → `UNAUTHENTICATED`;
  any authenticated user → `ALLOW`). When the entry sets
  `permission = "<app>.<codename>"`, that perm is checked via
  `user.has_perm(...)`.

`prompts/list` returns the **same set of names for all callers**, parallel
to the per-tool stability of `tools/list` (INV-DMCP01-4). Per-user
authorisation is decided when the prompt is invoked: `prompts/get`
re-runs the `auth_check` before rendering, and a `DENY` /
`UNAUTHENTICATED` outcome surfaces an MCP error to the caller rather
than a rendered body. Rationale: a `prompts/list` whose entries depend
on the calling user invalidates client-side caches per-user and creates
a cache-key explosion identical to the one INV-DMCP01-4 exists to
prevent. INV-DMCP03-9 anchors this.

### §8.3 — Permission outcomes

Reuses DMCP-00 §7 (`ALLOW`, `DENY`, `UNAUTHENTICATED`, `OUT_OF_SCOPE`).
No new outcomes.

## §9 — Invariants (this phase; inherits INV-DMCP-1..7, INV-DMCP01-1..5, INV-DMCP02-1..8)

- **INV-DMCP03-1 (resource URI stability).** A given Model's
  per-instance resource URI template is deterministic across discovery
  passes within the same process. A test pins this against a fixture
  Model; mutating the Model's `_meta.pk` shape between two passes MUST
  change the emitted URI's `{pk}` placeholder schema (per §7.1's auth-
  parity helper) but MUST NOT silently drop the resource.
- **INV-DMCP03-2 (resource auth parity).** Reading a model resource
  for a given user produces the same field set the `admin.retrieve:`
  tool would return for the same user. Field-content resources are
  blocked when the field is hidden from the admin's visible set.
- **INV-DMCP03-3 (no resource without auth gate).** A resource whose
  derivation rule cannot resolve a permission (e.g. model has no admin
  registration AND is not listed in `DJANGO_MCP_RESOURCE_MODELS` with
  an explicit permission) MUST be skipped at discovery with a WARNING.
  This is the §10.3-equivalent of INV-DMCP02-4 for resources.
- **INV-DMCP03-4 (collection-resource pagination).** A collection-style
  resource (`django-mcp://model/<...>/` without a `{pk}`) is not
  emitted by DMCP-03 — collections live in the tool layer
  (`admin.list:` / `model.search:`). Resources are individually
  addressable; an unbounded "list all instances as one resource read"
  is forbidden. (Out of scope; will be revisited in a future amendment
  if a paginated resource shape gets proposed.)
- **INV-DMCP03-5 (prompts are templates, not computation).** A prompt
  body MAY reference a tool by name and MAY interpolate
  argument-bound values into the body. It MUST NOT execute tools at
  prompt-render time. `prompts/get` returns text; tool invocation is a
  separate client decision.
- **INV-DMCP03-6 (mime-type honesty).** A `ResourceDescriptor`'s
  declared `mime_type` MUST match the body `read_handler` actually
  returns, OR fall back to `application/octet-stream` when the
  derivation can't determine the type. Silently emitting `image/png`
  for bytes that are actually `image/webp` is a defect.
- **INV-DMCP03-7 (admin-prompt namespace parity).** A prompt derived
  from an admin action carries the same name suffix as the tool: the
  action `publish` on `blog.Post` produces tool
  `admin.action:blog.Post.publish` AND prompt
  `prompt.admin.blog.Post.publish`. Renames track together.
- **INV-DMCP03-8 (field-resource byte cap).** A field-content resource
  read whose underlying file exceeds
  `DJANGO_MCP_FIELD_RESOURCE_MAX_BYTES` MUST raise `ValueError(f"file
  exceeds DJANGO_MCP_FIELD_RESOURCE_MAX_BYTES={cap}")` — NO silent
  truncation. The cap is operator-controlled.
- **INV-DMCP03-9 (prompts/list stability).** `prompts/list` returns the
  same set of names for all callers regardless of per-user permissions —
  parallel to INV-DMCP01-4 for tools. Per-user authorisation happens at
  `prompts/get` time via the prompt's `auth_check`, which surfaces
  `DENY` / `UNAUTHENTICATED` as MCP errors. This preserves client-side
  cache stability of the prompt list.
- **INV-DMCP03-10 (no subscribe).** DMCP-03 does NOT implement
  `resources/subscribe`. Wire-level implementations MAY return an
  empty subscription set for completeness; live-updates land in a
  future phase that integrates Django Channels.

## §10 — Reconciliation with adjacent primitives

### §10.1 — Registry shape changes

The `ToolRegistry` introduced in DMCP-00 §3 holds only
`ToolDescriptor`s. DMCP-03 needs to also hold `ResourceDescriptor`s and
`PromptDescriptor`s. Two options were considered:

| Option | Shape | Trade-off |
|--------|-------|-----------|
| (A) extend existing `ToolRegistry` | Add `resources: dict[str, ResourceDescriptor]` and `prompts: dict[str, PromptDescriptor]` alongside the existing `descriptors` dict | Cheapest; rename `ToolRegistry → MCPRegistry`; existing tool callers see no API change |
| (B) introduce parallel `ResourceRegistry` / `PromptRegistry` | Three singletons | Cleaner separation; but `discover_now` now needs to acquire three locks (INV-DMCP-5 single-pass becomes harder to reason about) |

**Decision:** Option (A). `ToolRegistry` is renamed to `MCPRegistry` in
DMCP-03 (with a deprecation-free rename — pre-1.0 per `CLAUDE.md`).
`MCPRegistry` exposes `tools`, `resources`, `prompts` as three dicts,
each frozen together by the existing `freeze()` call. The single lock
continues to guard all three. Existing imports of `ToolRegistry` from
DMCP-01 / 02 are updated in the same DMCP-03 implementation pass.

### §10.2 — Resource URI scheme: `django-mcp://`

The scheme name is owned by this package. Alternatives considered:

- **HTTP URIs pointing at Django's HTTP surface.** Rejected — would tie
  the MCP resource layer to a running HTTP server even when the project
  doesn't expose one (CLI deployments, internal-only setups).
- **Custom scheme per project.** Rejected — every project would have to
  decide; a built-in default is more ergonomic.
- **`mcp://` (no `django-` prefix).** Rejected — would collide with
  other tools using the bare `mcp://` scheme.

The scheme is frozen at DMCP-03 ratification; changing it is a §15
amendment to DMCP-03 §5.1.

### §10.3 — Subscribe semantics

`resources/subscribe` per the MCP wire grammar lets a client subscribe
to live updates of a resource. DMCP-03 explicitly does NOT implement
this. The wire-side handler (DMCP-04) MAY return an empty subscription
result for resources DMCP-03 emits; a future phase building on Django
Channels would land the live-update path.

### §10.4 — Pagination shape

DMCP-03's resources are individually addressable; pagination is NOT
applicable to a resource (it returns one body for one URI). Collection
listing remains in the tool layer (`admin.list:` / `model.search:`).
INV-DMCP03-4 makes this explicit.

### §10.5 — `DJANGO_MCP_PROMPTS` entry shape

Each entry is a dict (no string-shorthand form):

```python
DJANGO_MCP_PROMPTS = [
    {
        "name": "monthly_revenue",         # leaf component per DMCP-00 §5 grammar
        "description": "Render a prompt that asks the model to compute monthly revenue.",
        "arguments": [
            {"name": "year", "description": "Calendar year (YYYY)", "required": True},
            {"name": "month", "description": "Month (1-12)", "required": True},
        ],
        "body": "Compute revenue for {year}-{month}. Use the rpc.invoke:reports.monthly_revenue tool.",
        "permission": "reports.view_revenue",  # optional; defaults to "authenticated"
    },
]
```

Unknown top-level keys → `ImproperlyConfigured`. Same rejection
discipline as DMCP-02 §10.2.

### §10.6 — Admin-emitted resource permission fall-through

When a model has both an admin registration AND a
`DJANGO_MCP_RESOURCE_MODELS` entry, the admin's permission semantics
win (per the spirit of INV-DMCP01-3 — the admin is the source of truth
for what's visible per-user). The `DJANGO_MCP_RESOURCE_MODELS` entry's
`permission` field is informative-only in that case; a WARNING is
logged at discovery noting the override.

## §11 — Non-goals

- **`resources/subscribe`.** Out of scope; see §10.3.
- **Large-file streaming.** A field-resource read materialises the
  whole file into memory (capped at
  `DJANGO_MCP_FIELD_RESOURCE_MAX_BYTES`). Streaming reads land in a
  future amendment.
- **HTML rendering.** Resources serve JSON, raw bytes, or text. The
  admin's rendered change-page HTML is NOT a resource.
- **Multi-message prompts.** Single `user` message per prompt; see §7.3.
- **Reverse-relation resources.** `User.posts` (a reverse FK) does not
  get a `django-mcp://relation/auth.User/{pk}/posts` resource. The
  `model.search:blog.Post` tool covers the same data with proper
  filtering.
- **Cross-instance batch reads.** A client wanting to read N instances
  calls `resources/read` N times. No batch endpoint.

## §12 — Acceptance checklist

A conforming DMCP-03 deployment MUST satisfy:

- **DMCP03-a.** `ResourceDescriptor` and `PromptDescriptor` dataclasses
  exist with the §3 fields. `MCPRegistry` (renamed from `ToolRegistry`)
  holds `tools`, `resources`, `prompts` dicts under one lock.
- **DMCP03-b.** A project with no models registered in admin AND no
  `DJANGO_MCP_RESOURCE_MODELS` entries AND no `DJANGO_MCP_PROMPTS`
  entries emits zero resources and zero prompts.
- **DMCP03-c.** For `tests/testapp/Post` (admin-registered, has an
  `ImageField`-style cover field if added) plus its `publish` admin
  action, discovery emits: one `django-mcp://model/testapp.Post/{pk}`
  resource template, one `django-mcp://field/testapp.Post/{pk}/<field>`
  template per FileField, and one `prompt.admin.testapp.Post.publish`
  prompt.
- **DMCP03-d.** INV-DMCP03-2 passes: a user with view permission can
  read `django-mcp://model/testapp.Post/{pk}` and gets the same field
  set `admin.retrieve:` returns; a user without view permission gets
  `DENY`.
- **DMCP03-e.** INV-DMCP03-6 passes: a known image (`.png`) yields
  `image/png`; an unknown file extension yields
  `application/octet-stream`; the declared mime_type matches what the
  handler returns.
- **DMCP03-f.** INV-DMCP03-7 passes: the prompt name for an admin
  action `publish` on `testapp.Post` is
  `prompt.admin.testapp.Post.publish`, parallel to the tool name
  `admin.action:testapp.Post.publish`.
- **DMCP03-g.** INV-DMCP03-8 passes: a file larger than the configured
  cap raises `ValueError`; no truncation.
- **DMCP03-h.** INV-DMCP03-9 passes: `prompts/list` (the in-process
  equivalent — iterating `MCPRegistry.prompts`) returns the same set of
  prompt names for two users with disjoint permissions; only the
  per-prompt `prompts/get` outcome differs between them
  (parity check against INV-DMCP01-4's analogous tool test).
- **DMCP03-i.** A `DJANGO_MCP_PROMPTS` entry with unknown top-level
  keys raises `ImproperlyConfigured` (parallel to DMCP-02 §10.2's
  rejection of unknown keys).
- **DMCP03-j.** This doc's §15 carries a dated ratification entry;
  the discovery log line cites `[DMCP-03]` once resources or prompts
  have been emitted.

## §13 — Files cited

- [`TODO-DMCP-00-CONCEPTS.md`](TODO-DMCP-00-CONCEPTS.md) §3, §5, §7, §9
  — vocabulary and invariants inherited; §5 grammar reused for
  resource-URI component validation.
- [`TODO-DMCP-01-ADMIN.md`](TODO-DMCP-01-ADMIN.md) §5, §6, §7.3, §8 —
  ModelAdmin walk, schema/serialisation idioms reused; `_get_base_actions`
  pattern reused for prompt derivation.
- [`TODO-DMCP-02-APPLICATIONS.md`](TODO-DMCP-02-APPLICATIONS.md) §6,
  §10.2 — `discover_now` extension shape; settings-entry rejection
  discipline.
- `../../django_mcp/admin.py` — `_serialize_instance` is the shared
  serialisation entry point; `_get_base_actions` discovery for prompt
  emission.
- `../../django_mcp/registry.py` — rename to `MCPRegistry` lands here.
- `../../django_mcp/schemas.py` — `field_to_json_schema_for_model_pk`
  reused to build the `{pk}` schema attached to resource templates.
- `/Users/iraabbott/softoboros/backend/mcp/api.py` — prior art for
  hand-written resource/prompt drift (§2).

## §14 — Unblocks

- **DMCP-04 (Transport / `MCPAPIKey`).** Independent of this phase but
  needed for end-to-end DMCP03-d / DMCP03-h parity tests (the
  permission resolver needs a real authenticated MCP caller).
- **Future "live updates" phase.** `resources/subscribe` is the
  natural seam once Django Channels integration is in scope.
- **Future "GraphQL/openapi resources" phase.** `django-mcp://meta/...`
  host is reserved here for that.

## §15 — Change log

- **2026-05-23** — Initial ratification. All sections (§0–§14) frozen.
  Signed off by: repository owner (`abbott.ira.r@gmail.com`). Ratified
  on the same day as DMCP-02 and the DMCP-00 §5 grammar amendment.

  **Pre-ratification spec adjustments (this session):**

  - **§8.2 / INV-DMCP03-9 — `prompts/list` stability flipped to per-user-
    stable** (matching INV-DMCP01-4 for tools). The earlier draft had
    `prompts/list` filter by caller `auth_check`; per the ratifying
    decision, the list is now stable across callers and per-user
    authorisation happens at `prompts/get` time. DMCP03-h acceptance gate
    rewritten to assert parity-of-name-set between two users with
    disjoint permissions, mirroring `test_inv_dmcp01_4`.

  Inherits INV-DMCP-1..7, INV-DMCP01-1..5, INV-DMCP02-1..8 without
  modification. Introduces phase-local INV-DMCP03-1..10 and the four
  rules in §6 (`ModelResourceRule`, `FileFieldResourceRule`,
  `AdminActionPromptRule`, `UserPromptRule`).

  Unblocks implementation of:
  - `django_mcp/resources.py` and `django_mcp/prompts.py`.
  - The `ToolRegistry → MCPRegistry` rename in `django_mcp/registry.py`
    (with `tools`, `resources`, `prompts` dicts under one lock).
  - Extension of `django_mcp.discovery.discover_now` to walk resource
    and prompt sources in the same single discovery pass that DMCP-01 /
    DMCP-02 own (INV-DMCP-5 preserved).
  - The four new settings (`DJANGO_MCP_RESOURCE_MODELS`,
    `DJANGO_MCP_RESOURCES_DISABLED`, `DJANGO_MCP_PROMPTS`,
    `DJANGO_MCP_FIELD_RESOURCE_MAX_BYTES`).
  - DMCP-04 (Transport) for resources/prompts wire-level routing once
    drafted.

  Commit: pending (carries `DMCP03:` subject prefix).

- **2026-05-23** — Amendment to §5.4 and §8.2 (clarifications surfaced
  by Worker I during the prompt-rule implementation). **Specification
  Required.** Signed off by: repository owner
  (`abbott.ira.r@gmail.com`).

  **Two changes, bundled:**

  1. **§5.4 body-shape wording aligned with §7.3.** The original §5.4
     table said admin-action prompts emit "A 2-message list: a `user`
     message ... and an `assistant` message acknowledging the requested
     action". §7.3 pinned the minimum-viable shape at a single `user`
     message and is the load-bearing implementation contract. §5.4 has
     been rewritten to say "A single `user` message per §7.3"; the
     "assistant acknowledgement" half is removed. Multi-message prompt
     bodies remain reserved for a future §15 amendment to §7.3.

     No invariant change. No behaviour change in `django_mcp/prompts.py`
     — Worker I correctly followed §7.3 in the implementation, which
     was the load-bearing pin.

  2. **§8.2 auth_check semantics for admin-derived prompts.** The
     original §8.2 said an admin-action-derived prompt "carries the
     same `auth_check` as that action's `ToolDescriptor`". The actual
     implementation (Worker I) uses `user.has_perm("<app>.view_<model>")
     + per-action allowed_permissions` directly, NOT a synthesised
     request through `ModelAdmin.has_view_permission`. §8.2 has been
     rewritten to record this explicitly and name the divergence
     scope:

     - For stock `ModelAdmin` subclasses, the two resolve identically.
     - For `ModelAdmin` subclasses that **override**
       `has_view_permission` with non-standard logic (e.g. multi-tenant
       row-level scoping that returns False for a tenant the user
       doesn't belong to), the prompt's auth_check is more permissive
       than the corresponding tool's. This is acceptable because per
       INV-DMCP03-5 the prompt is a template — rendering does not
       execute the tool. A user who renders the prompt and then
       attempts to invoke the underlying tool will hit the tool's own
       (stricter) auth_check at that call.

     **No INV-DMCP-3 violation:** the tool's permission check is the
     load-bearing surface for "can this user actually run this
     action?". The prompt's auth_check is defense-in-depth at the
     render step.

     **Promotion path (future):** if a deployment needs strict prompt-
     auth parity with the tool, a §15 amendment can replace the §8.2
     check with the build_admin_request + has_view_permission route.
     The current looser-but-stable check is the pragmatic default; the
     §15 amendment exists to make the looseness explicit and auditable.

  Commit: pending (carries `DMCP03:` subject prefix; lands separately
  from any behaviour change per `CLAUDE.md` discipline).
