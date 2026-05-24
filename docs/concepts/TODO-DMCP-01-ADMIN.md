# TODO-DMCP-01-ADMIN — Admin → MCP tools

> **Status:** **ratified 2026-05-22**, **amended 2026-05-22** (see §15).
> DMCP-00 ratified on the same date.
>
> This phase inherits all invariants and frozen enums from
> [`TODO-DMCP-00-CONCEPTS.md`](TODO-DMCP-00-CONCEPTS.md). Where this doc
> uses a term defined there, it is **used without modification** unless
> §10 says otherwise.

## §0 — Authority policy

This phase introduces no new external grammars. Authority decisions for
`ModelAdmin`, `Form`, `ModelForm`, `Permission`, `User`, and admin actions
are inherited from DMCP-00 §4.

The "admin action" name space is owned upstream by Django
(`admin.actions.action` decorator and `ModelAdmin.actions` list);
relationship is `derive` (we read it, we never mutate it).

## §1 — Purpose

Make every `admin.site.register(Model, MyModelAdmin)` call in a consuming
project mechanically produce a set of MCP tools — CRUD plus custom admin
actions — without further configuration. The default surface MUST match what
the same user could do via the rendered admin pages, no more and no less.

This is the proof-of-concept that the "MCP falls out of registered surface"
thesis is real. If DMCP-01 lands cleanly, DMCP-02 (applications) reuses the
same derivation-rule plumbing against URL patterns.

## §2 — Problem statement

The admin is the highest-density Django surface: a single
`admin.site.register(Model, ModelAdmin)` call yields the list view, the
change/add forms, the delete view, custom actions, permission gating, and
search/filter machinery — all by introspecting the model.

The hand-written prior-art shape in `softoboros/backend/mcp/api.py` does NOT
exploit this. The admin's introspection output (`get_list_display`,
`get_form`, `get_actions`, `get_search_fields`, the `has_*_permission`
methods) is ignored, and each tool is rewritten by hand from the underlying
model. This is the dominant per-tool authoring cost in the prior art.

DMCP-01 closes that gap by walking `admin.site._registry` and applying the
six rules in §6, producing the tools listed in §5.

## §3 — Glossary (additions only; rest inherited from DMCP-00 §3)

- **AdminSite** — As defined in `django.contrib.admin.AdminSite`; used
  without modification. The default site is `django.contrib.admin.site`.
- **Registered admin** — A `(Model, ModelAdmin)` pair in
  `admin.site._registry`. Used without modification.
- **Admin action** — As defined in `django.contrib.admin.options.action`;
  used without modification. Custom callables in `ModelAdmin.actions`
  participate.
- **Inline** — As defined in Django (`InlineModelAdmin`). DMCP-01 does NOT
  surface inlines as separate tools; see §11.
- **ModelAdminEmitter** — Owned by DMCP-01; does not exist upstream. The
  concrete `DerivationRule` implementation in this phase that walks
  registered admins and produces ToolDescriptors.

## §4 — Source-of-truth map (additions only)

| Concept | Upstream authority | Local representation | Mutation rights | Divergence policy | Downstream consumers | Conformance test owner |
|---------|--------------------|----------------------|-----------------|-------------------|----------------------|------------------------|
| `ModelAdmin.has_view_permission` etc. | Django | `derive` — called at MCP-call time with the resolved Django user | None | INV-DMCP-3 | DMCP-01 handlers | DMCP-01 |
| `ModelAdmin.get_search_fields` | Django | `derive` — used to populate the `q` argument schema for `admin.list` | None | A search field that the admin removes also disappears from the MCP tool on next process boot | DMCP-01 list-rule | DMCP-01 |
| `ModelAdmin.actions` | Django | `derive` — each action becomes one `admin.action:` tool | None | A removed action stops being surfaced on next process boot | DMCP-01 action-rule | DMCP-01 |
| `ModelForm` / `Form` returned by `ModelAdmin.get_form` | Django | `derive` — fields become the create/update input schema | None | Custom form widgets do not influence the MCP schema beyond their declared field types | DMCP-01 create / update rules | DMCP-01 |

## §5 — Frozen output surface

For every `(Model M, ModelAdmin A)` in `admin.site._registry`, the
`ModelAdminEmitter` MUST produce exactly the following ToolDescriptors,
subject to the per-tool permission check shown:

| Tool name | Permission check | Input | Output |
|-----------|------------------|-------|--------|
| `admin.list:<app>.<Model>` | `A.has_view_permission(request)` | `{ q?: string, filters?: object, ordering?: string, page?: int, page_size?: int }` (`q` only when `A.get_search_fields(request)` is non-empty) | `{ results: M[], count: int, page: int, page_size: int }` |
| `admin.retrieve:<app>.<Model>` | `A.has_view_permission(request, obj)` | `{ pk: <pk type> }` | `{ object: M }` |
| `admin.create:<app>.<Model>` | `A.has_add_permission(request)` | JSON Schema from `A.get_form(request)` field set | `{ object: M }` |
| `admin.update:<app>.<Model>` | `A.has_change_permission(request, obj)` | `{ pk: <pk type>, fields: <schema from A.get_form(request, obj)> }` | `{ object: M }` |
| `admin.delete:<app>.<Model>` | `A.has_delete_permission(request, obj)` | `{ pk: <pk type> }` | `{ deleted: true, pk: <pk type> }` |
| `admin.action:<app>.<Model>.<action_name>` | (1) `A.has_view_permission(request)` AND (2) any per-action permission declared via `action.allowed_permissions` | `{ pks: <pk type>[] }` | `{ updated: int, message?: string }` |

`<pk type>` is the JSON-Schema form of the model's primary key field type.
`<app>` is the model's `_meta.app_label`; `<Model>` is `_meta.object_name`.

The output `M` is the JSON serialisation of model fields restricted to those
visible per `A.has_view_permission` for the requesting user — same field set
the admin's change view would render. Computation of "visible field set" is
INV-DMCP01-3 below.

**Registration policy for this enum:** Standards Action. Adding a new
default-emitted tool (e.g. `admin.history:<app>.<Model>` reading the admin
log) requires a §15 amendment here AND a §15 amendment to DMCP-00 §5/§6
adding the verb / family.

## §6 — Derivation rules (the implementation contract)

DMCP-01 ships six concrete derivation rules, one per row in §5:

1. `AdminListRule`
2. `AdminRetrieveRule`
3. `AdminCreateRule`
4. `AdminUpdateRule`
5. `AdminDeleteRule`
6. `AdminActionRule`

Each rule MUST:

- Subclass a base `DerivationRule` (introduced in DMCP-00 implementation;
  pure interface today).
- Implement a class method `emit(cls, source: ModelAdmin) ->
  Iterable[ToolDescriptor]` that yields zero or more descriptors. (Zero is
  legal — e.g. `AdminCreateRule.emit` yields nothing for a `ModelAdmin`
  whose `has_add_permission` returns `False` for the anonymous AnonymousUser
  AND for every authenticated user the project knows about — but in practice
  the descriptor is always emitted and the *handler* enforces per-user
  permission at call time. This avoids per-user tool-list mutation. See
  INV-DMCP01-4.)
- Produce a `ToolDescriptor.origin` of the form
  `admin.<verb>:<app>.<Model>[:<action_name>]` so debugging can locate the
  rule that emitted any given tool.

Discovery walks `django.contrib.admin.site._registry` exactly once during
the first MCP request, behind a per-process lock (INV-DMCP-5). A setting
`DJANGO_MCP_ADMIN_SITES` MAY name additional AdminSite instances; default
is `["django.contrib.admin.site"]`.

## §7 — Schema derivation

`admin.create` / `admin.update` input schemas derive from the form returned
by `ModelAdmin.get_form(request, obj=None)`. The order of preference for
producing JSON Schema from a Django form field is:

1. If the field declares a `widget` with a known JSON Schema (e.g. a
   built-in date/time widget), use it.
2. Otherwise, map the underlying form-field class to JSON Schema using a
   frozen table:

| Django Form field | JSON Schema |
|-------------------|-------------|
| `CharField` | `{ "type": "string", "maxLength": <max_length if set> }` |
| `IntegerField` | `{ "type": "integer" }` (with `minimum`/`maximum` from validators) |
| `FloatField` | `{ "type": "number" }` |
| `DecimalField` | `{ "type": "string", "pattern": "<decimal regex>" }` |
| `BooleanField` | `{ "type": "boolean" }` |
| `DateField` | `{ "type": "string", "format": "date" }` |
| `DateTimeField` | `{ "type": "string", "format": "date-time" }` |
| `EmailField` | `{ "type": "string", "format": "email" }` |
| `URLField` | `{ "type": "string", "format": "uri" }` |
| `ChoiceField` | `{ "enum": [<choice values>] }` |
| `ModelChoiceField` | `{ "type": <pk type schema> }` |
| `ModelMultipleChoiceField` | `{ "type": "array", "items": <pk type schema> }` |
| (anything else) | `{ "type": "string" }` + a warning logged with the field's class name |

**Registration policy for this enum:** Specification Required (this list
lives in DMCP-01; new entries can be added by amending DMCP-01 §7 without
amending DMCP-00).

### §7.1 — Per-field annotations (2026-05-22 amendment)

In addition to the type-shape produced by the table above, every per-field
JSON Schema object MAY carry the following annotations, derived from the
form field's metadata:

| Annotation | Source | Included when |
|------------|--------|---------------|
| `title` | `field.label` | `field.label` is set (truthy) |
| `description` | `field.help_text` | `field.help_text` is set (truthy) |

These annotations are advisory for MCP callers (LLM clients use them as
prompting context); they do NOT participate in JSON Schema *validation*.
They are emitted **only** at the per-field level. Object-level (`form_to_
json_schema`) `title` / `description` are out of scope for this amendment.

### §7.2 — `ChoiceField` choices shape

The base table specifies `ChoiceField → { "enum": [<choice values>] }`.
Django allows two shapes for `ChoiceField.choices`:

1. Flat: `[(value, label), ...]`. The emitter MUST unpack `value` from
   each pair and place it in the `enum` array.
2. Grouped (optgroups): `[(group_label, [(value, label), ...]), ...]`.
   Until ERRATA-001 lands the supported handling, the emitter MUST
   detect the grouped shape and emit `{"type": "string"}` (the generic
   fallback) with a `WARNING` log line naming the field. This is a
   conservative choice: rejecting at runtime would prevent the admin
   from booting at all; silently flattening would lose information that
   an MCP caller might need.

Status of full optgroup support: see
[`ERRATA.md`](ERRATA.md) entry **ERRATA-001**.

### §7.3 — Output schema (`M`) derivation (2026-05-22 amendment)

The output `M` schema derives from the model's `_meta.get_fields()`,
restricted to concrete fields the admin's change view would render. The
mapping mirrors §7's input-side table with form-field → model-field
analogues, plus the following decisions, frozen here:

| Decision | Rule | Registration policy |
|----------|------|---------------------|
| `required` set | A field is `required` iff `field.null is False AND field.blank is False`. Auto-generated pk fields are always `required`. | Specification Required |
| `ForeignKey` shape | Render as the schema produced by `field_to_json_schema_for_model_pk(field.related_model)` — i.e. the *target's pk type*, not the target model nested. This mirrors the input-side `ModelChoiceField` row. | Specification Required |
| `OneToOneField` shape | Same as `ForeignKey`. | Specification Required |
| `ManyToManyField` shape | `{ "type": "array", "items": <target pk schema> }` — mirrors input-side `ModelMultipleChoiceField`. | Specification Required |
| Reverse relations | Excluded. Reverse FKs and reverse M2Ms do not appear in `M`'s output schema. | Specification Required |
| `additionalProperties` | `False` at the object level. | Specification Required |

Per-field `title` / `description` from §7.1 also apply on the output side
when the underlying model field declares `verbose_name` / `help_text`:
`title ← verbose_name` (when distinct from the field name's title-cased
form), `description ← help_text` (when non-empty).

A field type not covered by the table or this section follows the
"anything else" fallback: `{ "type": "string" }` plus a warning. Adding
a new model-field mapping is **Specification Required** — a new row in
this section.

## §8 — Permission enforcement contract

Per **INV-DMCP-3 (permission parity)**, a DMCP-01 handler MUST invoke the
corresponding `ModelAdmin.has_*_permission` method with a synthesised
`HttpRequest` whose `.user` is the resolved Django user (from the MCP
caller's `MCPAPIKey.user` link). The synthesised request:

- Has `request.method` set to the HTTP verb equivalent: `GET` for list /
  retrieve, `POST` for create / action, `PUT` for update, `DELETE` for
  delete. This matches what `has_*_permission` implementations in third-
  party admins typically inspect.
- Has `request.META["HTTP_X_DJANGO_MCP"] = "1"` so that downstream code can
  branch on "this call came in over MCP" if it cares. This is the only
  field django-mcp sets that crosses into the Django request surface, and
  it is namespaced per INV-DMCP-6.
- Has no session. Code that requires a session to compute permissions is
  out of scope for parity; if a consuming project does this, parity fails
  and is recorded as an errata.

For admin actions, the per-action permission check is:

1. If the action is decorated with `@admin.action(permissions=[...])`, the
   listed permission codenames MUST be satisfied by `user.has_perm` on the
   model's app/codename pair, AND `has_view_permission` MUST return `True`.
2. Otherwise, `has_view_permission` is the only check (matching Django's
   own action dispatch).

## §9 — Invariants (this phase)

DMCP-01 inherits INV-DMCP-1..7. Phase-local invariants:

- **INV-DMCP01-1 (admin schemas track the live admin).** A test MUST verify
  that mutating a ModelAdmin's `get_form` / `get_search_fields` / `actions`
  return values changes the emitted ToolDescriptor schema on the next
  discovery pass. (This is what catches the prior-art drift failure mode.)
- **INV-DMCP01-2 (action permission parity).** For an `admin.action:` tool,
  a user authorised to invoke the action via the admin POST handler MUST
  also be authorised via MCP; a user NOT so authorised MUST be rejected
  with `DENY`. Tested by a parity test that hits both surfaces with the
  same user.
- **INV-DMCP01-3 (visible-field parity).** The set of fields appearing in
  an `admin.retrieve` / `admin.list` result MUST equal the set the admin's
  change-view template would render for the same user — no more (no
  leaking of fields hidden by `get_fields` / `get_readonly_fields` /
  permissions) and no less.
- **INV-DMCP01-4 (per-user tool list stability).** The set of emitted
  ToolDescriptors does NOT depend on which user is calling. Per-user
  authorisation is decided in the handler. (Some MCP clients cache the
  tools list aggressively; making it user-specific creates a cache-key
  explosion. The DENY/OUT_OF_SCOPE distinction in DMCP-00 §7 exists
  precisely so the per-call decision can be expressed without mutating the
  tool list.)
- **INV-DMCP01-5 (no inline surfacing).** DMCP-01 does NOT emit any
  `admin.list:<inline target model>` tool driven by an inline. Inline
  relationships are surfaced (if at all) in DMCP-02 via the related view's
  URL pattern.

## §10 — Reconciliation with adjacent primitives

- **Custom AdminSite.** A project with a custom AdminSite MUST list it in
  `DJANGO_MCP_ADMIN_SITES`. There is no auto-discovery of custom sites —
  Django doesn't keep a registry of all AdminSite instances, and inventing
  one is `own` mutation across a Django boundary.
- **`ModelAdmin.get_queryset`.** All list / retrieve handlers MUST go
  through `ModelAdmin.get_queryset(request)` to honour per-admin
  filtering (e.g. multi-tenant scoping). A consumer's queryset
  customisation is the *only* place tenant scoping lives; the MCP handler
  does not re-implement it.
- **Soft-delete admins.** Some admins override `delete_model` to soft-
  delete. The `admin.delete` handler MUST call `ModelAdmin.delete_model`,
  not `instance.delete()`. (Calling `instance.delete()` would bypass the
  override and is INV-DMCP-3 violation.)
- **Bulk actions.** `ModelAdmin.actions` includes the default
  `delete_selected` unless the admin removed it. The default action IS
  surfaced as `admin.action:<app>.<Model>.delete_selected`; project that
  want different behaviour should remove the default in the ModelAdmin.

## §11 — Non-goals (this phase)

- **Inlines as nested tools.** Out of scope. A `BookInline` on `AuthorAdmin`
  does not become `admin.list:books.Book` automatically — `Book` has to be
  separately registered in the admin (which is the usual pattern anyway).
- **Admin form widgets that require JS to validate.** The MCP schema sees
  the underlying form-field class. A widget that only narrows input via
  client JS does not narrow the MCP schema; INV-DMCP01-1 is about field
  derivation, not widget round-trip.
- **Admin log entries.** No `admin.history:` tool in DMCP-01. Add via
  amendment if requested.

## §12 — Acceptance checklist

A conforming DMCP-01 deployment MUST satisfy:

- **DMCP01-a.** All six rules in §6 are implemented and registered.
- **DMCP01-b.** For a Django project with no admin registrations,
  discovery produces zero tools and the MCP `tools/list` call returns an
  empty list.
- **DMCP01-c.** For Django's built-in `django.contrib.auth` admin
  registrations (User, Group), discovery produces 6 × 2 = 12 tools (the
  Cartesian product of §5 rows and registered models), and the tool names
  match the §5 / DMCP-00 §5 grammars exactly.
- **DMCP01-d.** INV-DMCP01-1 has a passing test: mutating a ModelAdmin's
  `get_search_fields` between two discovery passes changes the
  `admin.list:` tool's input schema.
- **DMCP01-e.** INV-DMCP01-2 has a passing parity test using a custom
  admin action with `@admin.action(permissions=["change"])`.
- **DMCP01-f.** INV-DMCP01-3 has a passing parity test: a field hidden by
  `get_readonly_fields` is still returned (matches the admin); a field
  hidden by per-user `get_fields` filtering is NOT returned.
- **DMCP01-g.** INV-DMCP01-4 has a passing test: `tools/list` returns the
  same list of names for users with disjoint permissions; only the
  per-tool invoke outcomes differ.
- **DMCP01-h.** INV-DMCP01-5 has a passing test: an `AuthorAdmin` with a
  `BookInline` does NOT cause a `Book`-targeted tool to appear unless
  `Book` is independently registered.
- **DMCP01-i.** A tool-name parser (introduced in this phase, satisfying
  DMCP00-f) accepts every name emitted by DMCP-01 rules and rejects the
  representative negative cases listed in the parser's test fixture.
- **DMCP01-j.** This doc's §15 carries a dated ratification entry, and the
  AppConfig's `ready()` cites the phase id in its tool-discovery log line.

## §13 — Files cited

- [`TODO-DMCP-00-CONCEPTS.md`](TODO-DMCP-00-CONCEPTS.md) — parent doc;
  invariants and frozen enums inherited from §3, §5, §6, §7, §9.
- `../../django_mcp/apps.py` — `ready()` hook will trigger first-call
  discovery once implementation lands.
- Django source: `django/contrib/admin/sites.py` (for `_registry`),
  `django/contrib/admin/options.py` (for `ModelAdmin.has_*_permission`,
  `get_form`, `get_search_fields`, `get_queryset`, `actions`).
- `/Users/iraabbott/softoboros/backend/mcp/api.py` — prior art for
  hand-rolled CRUD tools, the failure mode this phase replaces.

## §14 — Unblocks

- **DMCP-02 (Applications → MCP tools)** can begin once this phase ratifies
  the `DerivationRule` base class and the AppConfig discovery pass.
- **DMCP-03 (Resources and prompts)** can begin once this phase establishes
  the per-call permission resolver — resources reuse it.

## §15 — Change log

- **2026-05-22** — Initial ratification. All sections (§0–§14) frozen.
  Signed off by: repository owner (`abbott.ira.r@gmail.com`). Ratified
  jointly with DMCP-00 and `CLAUDE.md`. Unblocks DMCP-01 implementation
  (six derivation rules per §6; the six default tools per §5 for every
  registered ModelAdmin; the tool-name parser satisfying DMCP00-f) and
  DMCP-03 (Resources and prompts) once the per-call permission resolver
  is in place. Commit: pending (carries `DMCP01:` subject prefix).

- **2026-05-22** — Amendment to §7 (Schema derivation). Three changes,
  bundled because they all surfaced during the first implementation pass
  of `django_mcp/schemas.py` (parallel-worker dispatch on the same day):

  1. **New §7.1 — Per-field annotations.** Authorises emitters to attach
     `title` (from `field.label`) and `description` (from `field.help_text`)
     to each per-field schema. Registration policy: Specification Required.
     Motivation: LLM MCP callers materially benefit from the labels and
     help text Django already authors; not surfacing them was an
     oversight in the initial §7 table, not a deliberate omission.

  2. **New §7.2 — `ChoiceField` choices shape.** Pins behaviour for
     grouped (optgroup) choices: emit the generic fallback
     `{"type": "string"}` with a warning. Full optgroup support is
     tracked by **ERRATA-001** (see `ERRATA.md`); the §7.2 rule holds
     until ERRATA-001 resolves with a §15 amendment promoting the
     handling to first-class.

  3. **New §7.3 — Output schema (`M`) derivation.** Fills the placeholder
     line in the previous §7 ("the §15 of DMCP-01 owns the exact mapping
     when the first implementation lands"). Frozen rules:
     `required` iff `null=False AND blank=False`; `ForeignKey` /
     `OneToOneField` render as the target's pk schema (mirroring the
     input-side `ModelChoiceField` row); `ManyToManyField` renders as
     `{"type":"array","items": <target pk schema>}`; reverse relations
     excluded; `additionalProperties: false`. Per-field `title` /
     `description` carry from §7.1 to the output side via
     `verbose_name` / `help_text`. Registration policy: Specification
     Required.

  No §9 invariant is amended. INV-DMCP-3 (permission parity), INV-DMCP-4
  (no silent restatement), and INV-DMCP-6 (namespaced extensions) are
  preserved: the annotations are valid JSON Schema keywords, the
  ChoiceField fallback is an explicit Django-side decision rather than a
  paraphrase, and no new cross-boundary field names are introduced.

  Existing `django_mcp/schemas.py` (landed earlier the same day under
  task #10) conforms to this amendment as-written *except* for the
  optgroup case, which is `🟡` per ERRATA-001 — the conservative
  fallback specified in §7.2 has not yet been implemented; the current
  code crashes on grouped choices. Tracking: task #14 in this session.
  Commit: pending (carries `DMCP01:` subject prefix; the §15 amendment
  lands separately from any behaviour change per `CLAUDE.md` discipline).
