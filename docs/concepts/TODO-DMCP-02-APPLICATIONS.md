# TODO-DMCP-02-APPLICATIONS — Applications → MCP tools

> **Status:** **ratified 2026-05-23** (see §15). DMCP-00 and DMCP-01 ratified
> 2026-05-22. This phase reuses their `DerivationRule` plumbing,
> `ToolDescriptor` shape, and per-call permission resolver shape —
> invariants INV-DMCP-1..7 and INV-DMCP01-1..5 are inherited without
> modification unless §10 records a named deviation.

## §0 — Authority policy

DMCP-02 broadens the boundary set beyond Django+MCP introduced by DMCP-00.

| Upstream grammar | Wire revision / version | Crawl boundary |
|------------------|-------------------------|----------------|
| Django | ≥ 4.2, < 6.0 | Inherited from DMCP-00 §0. URLconf / View / URLPattern / URLResolver are now load-bearing in addition to ModelAdmin. |
| Django REST Framework | ≥ 3.14 (optional dependency) | Crawlable when present in `INSTALLED_APPS`. DMCP-02 MUST degrade gracefully when DRF is not installed — `import rest_framework` failure is not a hard error. |
| Model Context Protocol | 2025-03-26 (inherited) | Same as DMCP-00. |

The DRF row introduces a *conditional* authority: the rule that derives MCP
tools from DRF ViewSets MUST be import-guarded so that a project using only
plain Django views still gets the `view.invoke` surface without paying the
DRF import cost.

## §1 — Purpose

Extend the "MCP falls out of registered surface" thesis from the admin to
**every endpoint a Django project already exposes via its root URLconf**.

Concretely, a project that has:

- A function-based view at `path("billing/invoice/<pk>/", invoice_detail)`,
- A class-based `InvoiceDetailView(DetailView)`,
- A DRF `InvoiceViewSet` registered with `router.register("invoices", ...)`,

SHOULD acquire — with no additional configuration beyond what DMCP-01
already required — the following MCP tools:

- `view.invoke:billing.invoice_detail` (FBV)
- `view.invoke:billing.InvoiceDetailView`  (CBV)
- `view.list:billing.InvoiceViewSet`, `view.retrieve:billing.InvoiceViewSet`,
  `view.create:billing.InvoiceViewSet`, `view.update:billing.InvoiceViewSet`,
  `view.delete:billing.InvoiceViewSet`, plus one `view.invoke:` per `@action`.

The unifying property: the MCP-side input schema, output schema, and
permission gate **derive** from the same primitives Django/DRF already use
to render the HTTP surface — the URLconf, the view's method signature, the
serializer, and the permission_classes. No parallel description language.

## §2 — Problem statement

DMCP-01 closed the "admin → MCP" gap; DMCP-02 closes the "rest of the
project → MCP" gap. The failure modes in the prior-art shape
(`/Users/iraabbott/softoboros/backend/mcp/api.py:1-12633`) are identical to
those §2 of DMCP-00 enumerated, with one additional one specific to
non-admin views:

- **URL drift.** A view is moved to a new URL path; the hand-written tool's
  URL handler argument decoding (PK extraction, query-param parsing) is
  rewritten by hand against the new path. The MCP-side schema and the URL's
  path-converter declaration both encode "this argument is an int / a UUID
  / a slug" — and they diverge silently.

Evidence pin:
- The softoboros prior art does not derive any tool from a URL pattern. Every
  view-aliasing tool is hand-encoded against the model, then re-validated
  against the view's parameters by reading the code at authoring time. A
  conformance test pinning "the MCP tool's path-arg schema matches the URL
  pattern's converter" does not exist.

The reframe: Django's `URLResolver` already carries authoritative
path-converter information (`int`, `uuid`, `slug`, `str`, `path`, plus any
registered custom converters). DMCP-02 walks it as the source of truth.

## §3 — Glossary (additions only; inherited from DMCP-00 §3 / DMCP-01 §3)

- **URL pattern** — As defined in `django.urls.URLPattern`; used without
  modification. A pattern's `.callback` attribute carries the resolved view
  (a callable for FBVs, a `View.as_view()` closure for CBVs, a DRF
  `ViewSet.as_view({...})` closure for ViewSets).
- **URL resolver** — As defined in `django.urls.URLResolver`. Used to walk
  nested `include()`'d URLconfs. Used without modification.
- **Path converter** — As defined in `django.urls.converters` (built-in:
  `IntConverter`, `StringConverter`, `UUIDConverter`, `SlugConverter`,
  `PathConverter`); plus any project-registered via `register_converter`.
  Used without modification — INV-DMCP02-2 (path-arg parity) reads
  `converter.regex` and `to_python` to derive JSON Schema for path args.
- **View class** — Any subclass of `django.views.View`. The MRO and method
  set determine which verbs are emitted; see §6 / `ViewInvokeRule`.
- **DRF ViewSet** — As defined in `rest_framework.viewsets.ViewSet` /
  `GenericViewSet` and subclasses. Used without modification. A ViewSet's
  *actions* attribute (populated by `as_view({...})`) lists the HTTP verbs
  bound to handler methods (`list`, `create`, `retrieve`, `update`,
  `partial_update`, `destroy`, plus `@action`-decorated methods).
- **DRF serializer** — As defined in `rest_framework.serializers.Serializer`
  / `ModelSerializer`. Used without modification; serves the same role for
  DRF views that `Form` / `ModelForm` serves for admin views in DMCP-01 §7.
- **ViewInvokeRule** — Owned by DMCP-02. Emits one
  `view.invoke:<dotted_view_path>` tool per URL pattern resolving to a
  non-DRF view, OR (for ViewSet patterns) delegates to `DRFViewSetRule`.
- **DRFViewSetRule** — Owned by DMCP-02. Emits one tool per
  ViewSet-bound method (list / retrieve / create / update / delete / each
  @action) following the verb-mapping table in §5.
- **ModelSearchRule** — Owned by DMCP-02. Emits one
  `model.search:<app>.<Model>` tool per model that participates in the rule
  via `DJANGO_MCP_MODEL_SEARCH` (defined in §6). Distinct from
  `admin.list:` because it's not gated on an admin registration — it
  surfaces models that have no admin entry but still want a query surface.
- **View identity (`<dotted_view_path>`)** — Owned by DMCP-02. The dotted
  Python path of the view callable. For FBVs: `module.func_name`. For CBVs:
  `module.ClassName` (not the `as_view()` closure). For DRF ViewSets:
  `module.ViewSetName`. Computed via `view.__module__ + "." + view.__qualname__`
  with normalization of `as_view()` closures back to their underlying class
  (`view.view_class` for CBVs, `view.cls` for DRF).

## §4 — Source-of-truth map (additions only)

| Concept | Upstream authority | Local representation | Mutation rights | Divergence policy | Downstream consumers | Conformance test owner |
|---------|--------------------|----------------------|-----------------|-------------------|----------------------|------------------------|
| Django `URLPattern` / `URLResolver` | Django | `derive` — walked to discover endpoints | None | A new Django version that changes URLPattern's introspection surface lands as a §15 amendment | DMCP-02 ViewInvokeRule | DMCP-02 |
| Django `View` MRO + method set | Django | `derive` — `View.http_method_names` ∩ defined methods determines emitted verbs | None | A View that overrides `dispatch` but not the per-verb methods emits only `view.invoke:` (see INV-DMCP02-3) | DMCP-02 | DMCP-02 |
| Django path converters | Django | `derive` — `converter.regex` + `converter.to_python` → JSON Schema | None | A custom converter without a JSON-Schema-mappable regex falls back to `{"type":"string"}` with a WARNING (parallel to DMCP-01 §7's generic fallback) | DMCP-02 | DMCP-02 |
| DRF `ViewSet` action map | DRF (when installed) | `derive` — read from `cls.get_extra_actions()` and the canonical CRUD method set | None | DRF version skew → §15 amendment; minimum DRF version pinned at 3.14 | DMCP-02 DRFViewSetRule | DMCP-02 |
| DRF `Serializer` field set | DRF (when installed) | `derive` — re-uses the §7 mapping table extended for DRF serializer fields | None | A serializer field whose `to_internal_value` differs from its declared field class → `{"type":"string"}` fallback + WARNING | DMCP-02 | DMCP-02 |
| DRF `permission_classes` | DRF (when installed) | `derive` — each class's `has_permission` / `has_object_permission` is invoked with the synthesised request | None | INV-DMCP-3 parity applies; per-object checks happen in the handler | DMCP-02 | DMCP-02 |
| Django auth mixins (`LoginRequiredMixin`, `PermissionRequiredMixin`, `UserPassesTestMixin`) | Django | `derive` — the View's MRO is walked at discovery to detect mixin presence; the mixin's `dispatch`-level check is recreated at auth_check time | None | Custom mixins overriding `dispatch` for permission checks SHOULD be detected via their `raise_exception` / `login_url` attribute pattern; undetectable mixins emit a WARNING and the tool's auth_check falls back to `is_authenticated` (INV-DMCP02-4) | DMCP-02 | DMCP-02 |

## §5 — Frozen output surface

### §5.1 — Plain views (FBV / CBV, no DRF)

For every `URLPattern p` reachable from `ROOT_URLCONF`, with `view = resolve_view_class(p.callback)`:

| Tool name | Emitted when | Input | Output |
|-----------|--------------|-------|--------|
| `view.invoke:<dotted_view_path>` | `view` is an FBV, or a CBV whose `http_method_names` includes anything beyond a single CRUD verb | `{ path: object, query: object, body: object }` — `path` carries the URL's named groups (per-converter JSON Schema); `query` is a permissive object; `body` is the form/serializer schema when discoverable, else permissive | `{ status: int, headers: object, body: any }` — coarse-grained because non-DRF views may return rendered HTML, redirects, JSON, etc.; `body` is `any` by default, narrowed when the view declares its content type |

`view.invoke:` is the **broadest** rule — it works on any view. The narrower
verbs in §5.2 / §5.3 take precedence when their conditions match (a CBV
that has only `get` becomes `view.retrieve:`, not `view.invoke:`). This
precedence is INV-DMCP02-5.

### §5.2 — CBV verb-mapping

For a class-based view whose method set (intersection of
`http_method_names` with actually-defined methods) is exactly one of the
canonical verbs:

| Defined methods | Emitted tool | Notes |
|-----------------|--------------|-------|
| `{get}` | `view.retrieve:<dotted_view_path>` | Or `view.list:` when the view inherits from `ListView` |
| `{post}` | `view.create:<dotted_view_path>` | Or `view.invoke:` when the view's form is unresolvable |
| `{put}` | `view.update:<dotted_view_path>` | |
| `{delete}` | `view.delete:<dotted_view_path>` | |
| Multiple methods, or methods outside this set (`head`, `options`, custom) | `view.invoke:<dotted_view_path>` | Fallback per §5.1 |

A CBV's `get`-only/`post`-only narrowing is decided at discovery time from
the **class definition**, not from per-request method dispatch. A class
that conditionally defines `post` (via `@method_decorator` or runtime
patching) is undetectable and emits `view.invoke:` by default.

### §5.3 — DRF ViewSet verb-mapping

For a `URLPattern` resolving to a DRF `ViewSet.as_view({...})` closure, the
emitter reads `view.actions` (the `{http_verb: handler_name}` dict) and
emits per the table below. A ViewSet typically resolves to TWO patterns —
the list pattern (`{"get":"list","post":"create"}`) and the detail pattern
(`{"get":"retrieve","put":"update","patch":"partial_update","delete":"destroy"}`).
The emitter MUST coalesce both patterns into one tool set per
`<dotted_viewset_path>`.

| Handler method | Emitted tool |
|----------------|--------------|
| `list` | `view.list:<dotted_viewset_path>` |
| `retrieve` | `view.retrieve:<dotted_viewset_path>` |
| `create` | `view.create:<dotted_viewset_path>` |
| `update` AND/OR `partial_update` | `view.update:<dotted_viewset_path>` — single tool; semantics are PATCH-style (every field optional). See §10.1 for the PUT/PATCH coalescing decision. |
| `destroy` | `view.delete:<dotted_viewset_path>` |
| `@action`-decorated method `<name>` | `view.invoke:<dotted_viewset_path>.<action_name>` |

Input schemas derive from the ViewSet's `get_serializer_class()` (with a
synthesised request); output schemas derive from the same serializer's
output projection.

### §5.4 — Model search (fallback rule)

For a model `M` named in `DJANGO_MCP_MODEL_SEARCH` (default: empty list —
opt-in, not opt-out, because the surface is broader than admin):

| Tool name | Permission check | Input | Output |
|-----------|------------------|-------|--------|
| `model.search:<app>.<Model>` | Default permission: `<app>.view_<model_name>`; configurable per entry | `{ q?: string, filters?: object, ordering?: string, page?: int, page_size?: int }` | `{ results: M[], count: int, page: int, page_size: int }` |

The output `M` schema is `model_to_output_schema(M)` from
`django_mcp.schemas` (DMCP-01 §7.3 — reused). `search_fields` for the rule
are explicit per-entry, NOT inferred from any admin's `search_fields`.

**Registration policy for §5.1–§5.4:** Standards Action. Adding a new
default-emitted verb (e.g. `view.head:` for OPTIONS-only views) requires a
§15 amendment here AND a §15 amendment to DMCP-00 §5/§6 expanding the
verb / family enum.

## §6 — Derivation rules (implementation contract)

DMCP-02 ships three concrete rules:

1. **`ViewInvokeRule`** — operates on `URLPattern.callback` after `URL` walk.
2. **`DRFViewSetRule`** — operates on the same patterns, takes precedence
   when the callback resolves to a DRF ViewSet `as_view` closure (detected
   via `getattr(callback, "cls", None) is not None and is-subclass-of
   rest_framework.viewsets.ViewSetMixin`).
3. **`ModelSearchRule`** — operates on model classes named in
   `DJANGO_MCP_MODEL_SEARCH`.

Each rule subclasses `django_mcp.derivation.DerivationRule` (DMCP-00) with
`family` set to:

- `view` for `ViewInvokeRule` and `DRFViewSetRule`
- `model` for `ModelSearchRule`

Discovery happens during the same single-pass that DMCP-01 owns
(INV-DMCP-5): `django_mcp.discovery.discover_now` is extended in this
phase to:

1. (existing, from DMCP-01) walk every AdminSite in `DJANGO_MCP_ADMIN_SITES`.
2. (DMCP-02) walk `ROOT_URLCONF`'s URL tree, dispatching each
   `URLPattern` to `DRFViewSetRule` if it's a ViewSet, otherwise
   `ViewInvokeRule`.
3. (DMCP-02) walk `DJANGO_MCP_MODEL_SEARCH` and apply `ModelSearchRule` to
   each entry.

The walk MUST handle `URLResolver` recursively (for `include()`'d
URLconfs) without falling into cycles — Django's resolver guards against
this internally; the DMCP-02 walker re-uses Django's traversal rather than
re-implementing it (INV-DMCP-4: no silent restatement).

Settings introduced by this phase:

- `DJANGO_MCP_URLCONFS: list[str]` — additional URLconfs to walk. Default:
  `[settings.ROOT_URLCONF]`.
- `DJANGO_MCP_VIEW_EXCLUDE: list[str]` — dotted paths of views whose
  patterns are skipped by the walker. Use to opt-out static/media/health
  endpoints. Default: `[]`.
- `DJANGO_MCP_MODEL_SEARCH: list[dict] | list[str]` — entries describing
  `model.search:` tools. Each entry is either a dotted model path (use
  defaults) or a dict carrying `{model, search_fields, permission?}`. See
  §10.2 for the schema's frozen shape.

## §7 — Schema derivation (DRF additions)

DMCP-01 §7 froze the form-field → JSON Schema mapping for Django forms.
DMCP-02 inherits that and **extends** it with a parallel table for DRF
serializer fields (this extension is in scope because it's an additive
mapping, not a redefinition):

| DRF serializer field | JSON Schema | Notes |
|----------------------|-------------|-------|
| `CharField`, `RegexField`, `SlugField` | `{"type":"string", "maxLength": <max_length if set>, "pattern": <regex if RegexField/SlugField>}` | |
| `IntegerField` | `{"type":"integer"}` with `minimum`/`maximum` from `min_value`/`max_value` | |
| `FloatField` | `{"type":"number"}` with `minimum`/`maximum` | |
| `DecimalField` | `{"type":"string", "pattern": "^-?\\d+(\\.\\d+)?$"}` | Matches DMCP-01 §7 DecimalField row |
| `BooleanField`, `NullBooleanField` | `{"type":"boolean"}` (NullBooleanField also accepts `null`) | |
| `DateField` | `{"type":"string","format":"date"}` | |
| `DateTimeField` | `{"type":"string","format":"date-time"}` | |
| `TimeField` | `{"type":"string","format":"time"}` | |
| `EmailField` | `{"type":"string","format":"email"}` | |
| `URLField` | `{"type":"string","format":"uri"}` | |
| `UUIDField` | `{"type":"string","format":"uuid"}` | |
| `ChoiceField`, `MultipleChoiceField` | `{"enum":[...]}` (multiple wraps in `{"type":"array","items":{...}}`) | Optgroup behaviour mirrors DMCP-01 §7.2 / ERRATA-001 |
| `PrimaryKeyRelatedField` | The target model's pk schema via `field_to_json_schema_for_model_pk` (DMCP-01 §7.3 reuse) | |
| `SlugRelatedField` | `{"type":"string"}` (slug semantics; the target model's pk is not surfaced) | |
| `HyperlinkedRelatedField` | `{"type":"string","format":"uri"}` | |
| `SerializerMethodField` | `{"type":"string"}` + WARNING — method return types are not declared | INV-DMCP02-6 |
| `ListSerializer` | `{"type":"array","items": <inner serializer's object schema>}` | |
| `Serializer` (nested) | The nested serializer's object schema (recursive) | |
| (anything else) | `{"type":"string"}` + WARNING naming the field class | DMCP-01 §7 parallel |

**Registration policy:** Specification Required (entries can be added by
amending DMCP-02 §7 without amending DMCP-00).

Output-schema derivation for DRF tools follows the serializer's output
representation (call `serializer.to_representation` against a synthesised
instance? — no, that's per-request; rely on the field set declared on the
serializer class, mapped via the table above). The §7.3 output-schema
rules from DMCP-01 (`required` semantics, FK/M2M shape, reverse-relation
exclusion) apply to model-side outputs; serializer-declared outputs use the
serializer's `Meta.fields` (or the explicit `fields` list) as the
authoritative set.

## §8 — Permission enforcement (view/DRF additions)

DMCP-01 §8 froze admin permission parity via `ModelAdmin.has_*_permission`.
DMCP-02 adds:

### §8.1 — Plain Django views (CBV/FBV)

- **`LoginRequiredMixin`** — detected on the MRO; auth_check returns
  `UNAUTHENTICATED` for an unauthenticated user.
- **`PermissionRequiredMixin`** — detected on the MRO; reads
  `permission_required` (string or sequence) and resolves via
  `user.has_perm(...)`. Missing perm → `DENY`.
- **`UserPassesTestMixin`** — detected on the MRO. The `test_func` is
  called at handler time with the synthesised request (its check signature
  is `(self)` reading `self.request.user`; the handler instantiates the
  view with the synthesised request bound). Failure → `DENY`.
- **FBV with `@login_required` decorator** — detected by walking the
  decorator chain (`view.__wrapped__` chain); behaviour as
  `LoginRequiredMixin`.
- **FBV with `@permission_required(perm)`** — same, behaviour as
  `PermissionRequiredMixin`.
- **Plain FBV / CBV without any of the above** — auth_check returns
  `ALLOW` for any user (matching what Django would do: no auth
  enforcement). A WARNING is logged at discovery: "view `<dotted_path>` has
  no detectable auth gate; emitted as publicly-callable". Per
  INV-DMCP02-4, opt-in tightening lives in `DJANGO_MCP_REQUIRE_AUTH` (see
  §10.3).

### §8.2 — DRF ViewSets / APIViews

- `view.permission_classes` is read at discovery; each class is
  instantiated at call time and invoked with the synthesised request.
  `has_permission(request, view)` returns False → `DENY`.
  `has_object_permission(request, view, obj)` returns False → handler
  raises `PermissionDenied` (mirrors the admin per-object pattern from
  DMCP-01 §8).
- DRF's default `IsAuthenticated` / `IsAdminUser` / `AllowAny` are handled
  identically by this mechanism (they're just `permission_classes`).
- The `view.authentication_classes` chain is NOT exercised — MCP
  authentication is upstream (the MCP API key resolves to a Django user;
  per-view authentication classes do not re-run). This is INV-DMCP02-7.

### §8.3 — Permission resolution outcomes

The four DMCP-00 §7 outcomes (`ALLOW`, `DENY`, `UNAUTHENTICATED`,
`OUT_OF_SCOPE`) are reused without addition. No new outcomes are
introduced.

## §9 — Invariants (this phase; inherits INV-DMCP-1..7 and INV-DMCP01-1..5)

- **INV-DMCP02-1 (URL-derived names are stable).** The `<dotted_view_path>`
  produced for a given view is deterministic across runs and stable under
  Django version bumps within the supported `>=4.2,<6.0` range. A test
  asserts this against a representative project (the test suite's
  `tests/testapp`).
- **INV-DMCP02-2 (path-arg parity).** The JSON Schema for a path arg of an
  emitted `view.*` tool MUST match what the URL's path converter accepts.
  Specifically: an `<int:pk>` segment yields `{"type":"integer"}`; a
  `<uuid:id>` segment yields `{"type":"string","format":"uuid"}`; a custom
  converter without a known mapping yields `{"type":"string"}` + WARNING.
- **INV-DMCP02-3 (CBV verb detection looks at class definitions).** The
  set of methods used to decide CBV verb-narrowing per §5.2 is computed
  from class definition (`vars(cls)` keys intersected with
  `http_method_names`), not from runtime dispatch. A CBV that adds methods
  via runtime patching is undetectable and falls back to `view.invoke:`.
- **INV-DMCP02-4 (no silent public surface).** A view with no detectable
  auth gate is logged at WARNING during discovery, and the WARNING names
  the view's dotted path. The `DJANGO_MCP_REQUIRE_AUTH` setting (default
  `True`) controls whether such views are emitted at all: when `True`,
  detection failure → skip emission; when `False`, emit with auth_check =
  `ALLOW`. INV-DMCP02-4 is "no view becomes publicly callable over MCP
  without an explicit opt-in"; the test asserts that a vanilla CBV is
  excluded from the registry when `DJANGO_MCP_REQUIRE_AUTH=True`.
- **INV-DMCP02-5 (verb-narrowing precedence).** When multiple §5 rules
  match (e.g. a CBV with only `get` could plausibly emit either
  `view.invoke:` or `view.retrieve:`), the narrower verb wins. The
  decision is mechanical, not configurable.
- **INV-DMCP02-6 (no inferred SerializerMethodField return type).**
  `SerializerMethodField`'s return type is not inferred from the method's
  source; it always emits `{"type":"string"}` + WARNING. Opting in to a
  declared shape requires the serializer to use `extend_schema_field` (or
  equivalent, ratified in a future amendment) — DMCP-02 does NOT
  introspect annotations because they're optional and unreliable across
  the Python ecosystem.
- **INV-DMCP02-7 (no per-view authentication re-run).** MCP request
  authentication is upstream of view dispatch; `view.authentication_classes`
  is not exercised by DMCP-02 handlers.
- **INV-DMCP02-8 (DRF degradation).** When DRF is not installed,
  `DRFViewSetRule` is a no-op (does not raise, does not import
  `rest_framework`). `ViewInvokeRule` and `ModelSearchRule` continue to
  function.

## §10 — Reconciliation with adjacent primitives

### §10.1 — PUT and PATCH collapse to one tool

A DRF ViewSet that defines both `update` and `partial_update` emits **one**
`view.update:` tool. The tool's input schema marks every field as optional
(PATCH-style). When the caller supplies all fields, the underlying handler
performs an `update`; when partial, a `partial_update`. The DMCP-00 §5
verb enum does not contain `partial_update` and §10.1 records that this is
intentional — adding `partial_update` is a §15 amendment to DMCP-00, which
this phase does NOT request.

Rationale: LLM tool callers reason about "update a thing's fields", not
about PUT vs PATCH; collapsing reduces surface without losing semantics.
A consumer who needs the distinction can opt into emitting both via a
future §15 amendment.

### §10.2 — `DJANGO_MCP_MODEL_SEARCH` entry shape

Each entry is either:

- A dotted model path string (`"catalog.Product"`) — uses defaults:
  `search_fields = []`, `permission = "<app>.view_<model>"`.

OR a dict:

```python
DJANGO_MCP_MODEL_SEARCH = [
    {
        "model": "catalog.Product",
        "search_fields": ["name", "sku"],
        "permission": "catalog.view_product",  # default; can override
        "filter_fields": ["category"],  # whitelisted filter keys
    },
]
```

Unknown keys at the dict's top level are a configuration error (the §10.2
shape is frozen).

### §10.3 — `DJANGO_MCP_REQUIRE_AUTH`

- Default: `True` (conservative).
- When `True`: a view with no detectable auth gate is skipped at
  discovery; a WARNING is logged with the dotted path. INV-DMCP02-4
  enforces this.
- When `False`: such views emit with `auth_check → ALLOW`. Suitable for
  internal-only deployments behind a network-level gate. Documented as an
  opt-out, not the default.

### §10.4 — `URLResolver` with namespaced includes

A `path("billing/", include("billing.urls", namespace="billing"))` walk
emits tools whose `<dotted_view_path>` is the view's actual import path,
NOT the URL namespace. The URL namespace is informative for routing, not
load-bearing for the tool's identity. (Decision rationale: URL namespaces
are routing-layer; tool identity is code-layer.)

### §10.5 — Anonymous views (lambdas, class-method dispatch)

A URL pattern bound to a lambda or to a closure without a stable
`__module__` + `__qualname__` cannot produce a deterministic dotted path.
Such patterns are skipped at discovery with a WARNING. They are out of
scope for §5; if a project relies on them and wants MCP surface, they
should refactor to a named callable.

### §10.6 — Static / media / health endpoints

The walker does NOT pre-filter URL patterns by URL prefix (`/static/`,
`/media/`, `/healthz`). Filtering is the user's job via
`DJANGO_MCP_VIEW_EXCLUDE`. Rationale: this package does not own those
conventions; auto-filtering would be a hidden DMCP-side opinion.

## §11 — Non-goals

- **GraphQL endpoints.** Out of scope. A `graphql_view` URL pattern is
  emitted as `view.invoke:` with a permissive body schema; GraphQL-aware
  introspection would be a separate phase.
- **WebSocket / channels consumers.** Out of scope. Channels routing is
  not part of `ROOT_URLCONF`'s URL tree.
- **Inferred response shapes from view source code.** A bare FBV
  returning `JsonResponse(...)` does not get its output schema inferred
  via AST or type analysis; output schema falls back to `{}` with a note.
- **DRF's browsable API affordances.** DMCP-02 derives from
  serializer/permission classes only; the rendered HTML browsable API is
  unrelated.
- **Per-request method-narrowing.** A CBV that conditionally accepts POST
  based on a request-time flag still emits as if its class-defined method
  set were the truth.

## §12 — Acceptance checklist

A conforming DMCP-02 deployment MUST satisfy:

- **DMCP02-a.** All three rules in §6 are implemented and registered;
  `discover_now` extends to walk URLs and `DJANGO_MCP_MODEL_SEARCH` in
  the same single pass that DMCP-01 owns (INV-DMCP-5 holds).
- **DMCP02-b.** For a project whose `ROOT_URLCONF` contains zero
  non-admin URL patterns, DMCP-02 contributes zero tools.
- **DMCP02-c.** For `tests/testapp` (or equivalent fixture) declaring one
  FBV, one CBV (`DetailView`-style with only `get`), and one DRF ViewSet
  (when DRF is installed), discovery produces exactly the §5-pinned set
  of tools. With DRF NOT installed, `DRFViewSetRule` emits zero, and
  `ViewInvokeRule` covers the other two views.
- **DMCP02-d.** INV-DMCP02-2 passes: an `<int:pk>` segment yields
  `{"type":"integer"}` in the emitted `view.retrieve:` tool's
  `properties.path.properties.pk` schema; an `<uuid:id>` segment yields
  the `uuid` format; an unknown custom converter falls back to
  `{"type":"string"}` + WARNING.
- **DMCP02-e.** INV-DMCP02-4 passes: a `View` subclass without
  `LoginRequiredMixin` / `PermissionRequiredMixin` / a `@login_required`
  decorator AND with `DJANGO_MCP_REQUIRE_AUTH=True` is NOT emitted;
  the same View with `DJANGO_MCP_REQUIRE_AUTH=False` IS emitted with
  `auth_check → ALLOW`.
- **DMCP02-f.** INV-DMCP02-5 passes: a CBV with only `get` emits
  `view.retrieve:` (or `view.list:` for `ListView` subclasses), not
  `view.invoke:`. The same CBV with `get` AND `post` defined emits
  `view.invoke:`.
- **DMCP02-g.** INV-DMCP02-8 passes: with DRF uninstalled,
  `import django_mcp.discovery; discover_now()` completes without
  raising; the `model.search:` and `view.invoke:` tools still appear.
- **DMCP02-h.** A DRF ViewSet with `update` AND `partial_update`
  produces exactly one `view.update:` tool whose input schema marks every
  field as optional (PATCH semantics) — §10.1.
- **DMCP02-i.** Parity test: invoking a DRF `view.create:` tool with a
  given user produces the same `(status, body-shape)` as POSTing to the
  ViewSet's URL with the same user via Django's `RequestFactory` +
  `as_view()`. The parity bar is the JSON body's top-level keys; exact
  byte-for-byte equality is out of scope.
- **DMCP02-j.** This doc's §15 carries a dated ratification entry; the
  discovery log line cites `[DMCP-02]` once URLs/models have been walked.

## §13 — Files cited

- [`TODO-DMCP-00-CONCEPTS.md`](TODO-DMCP-00-CONCEPTS.md) §3, §5, §6, §7,
  §9 — invariants and frozen enums inherited.
- [`TODO-DMCP-01-ADMIN.md`](TODO-DMCP-01-ADMIN.md) §7.1–§7.3, §8 —
  schema-mapping table and synthesised-request shape reused.
- `../../django_mcp/admin.py` — `emit_for_admin` shape that
  `ViewInvokeRule` / `DRFViewSetRule` / `ModelSearchRule` mirror.
- `../../django_mcp/discovery.py` — `discover_now()` is the extension
  point per DMCP02-a.
- `../../django_mcp/schemas.py` — DMCP-01 §7 / §7.3 mapping reused;
  serializer-field extension per §7 lands here.
- Django source: `django/urls/resolvers.py` (URLPattern / URLResolver),
  `django/urls/converters.py` (converter regex / to_python).
- DRF source (when installed): `rest_framework/viewsets.py`
  (ViewSet.actions), `rest_framework/serializers.py` (field set),
  `rest_framework/permissions.py` (permission classes).
- `/Users/iraabbott/softoboros/backend/mcp/api.py` — prior art for the
  hand-rolled view-aliasing failure mode (§2).

## §14 — Unblocks

- **DMCP-03 (Resources and prompts).** Once `ViewInvokeRule` ratifies and
  lands, the resource side has a derivation pattern to mirror (URL →
  Resource URI is the same walk, just with a different output shape).
- **DMCP-04 (Transport / `MCPAPIKey`).** Independent of this phase but
  needed for end-to-end DMCP02-i parity tests.

## §15 — Change log

- **2026-05-23** — Initial ratification. All sections (§0–§14) frozen.
  Signed off by: repository owner (`abbott.ira.r@gmail.com`). Ratified on
  the day after DMCP-00 / DMCP-01 / `CLAUDE.md` (which ratified 2026-05-22).
  Inherits INV-DMCP-1..7 and INV-DMCP01-1..5 without modification.
  Introduces phase-local INV-DMCP02-1..8 and the three rules in §6
  (`ViewInvokeRule`, `DRFViewSetRule`, `ModelSearchRule`).

  Unblocks implementation of:
  - The URL-tree walker shared by `ViewInvokeRule` and `DRFViewSetRule`.
  - The DRF-conditional import in `django_mcp.drf` (or equivalent module).
  - Extension of `django_mcp.discovery.discover_now` to walk URLs +
    `DJANGO_MCP_MODEL_SEARCH` in the same single discovery pass that
    DMCP-01 owns (INV-DMCP-5 preserved by gating on the registry lock).
  - DMCP-03 (Resources and prompts) once `ViewInvokeRule` lands —
    resources mirror the same URL walk with a different output shape.

  Commit: pending (carries `DMCP02:` subject prefix).
