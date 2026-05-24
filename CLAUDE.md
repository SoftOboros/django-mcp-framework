# CLAUDE.md

## Repository Purpose

`django-mcp` is a reusable Django application that exposes a Django project's
registered views and URL patterns as Model Context Protocol (MCP) tools and
resources. The design principle is one sentence:

> **MCP tools derive from registered views and URLs the same way Django's
> admin/CRUD/forms machinery derives from models.**

A consumer adds `django_mcp` to `INSTALLED_APPS`, includes one URL include, and
the project's existing surface area — admin first, then user-registered
applications — is reflected over MCP without per-tool boilerplate.

Layout (rlvgl-style top level):

- `django_mcp/` — the importable Python package
- `docs/` — concept docs (`docs/concepts/`), operational notes, ERRATA log
- `tests/` — pytest suite (uses `pytest-django` with a minimal in-tree settings)
- `example/` — a minimal Django project that mounts `django_mcp` for manual
  smoke tests (created when first needed; not present at scaffold time)
- `pyproject.toml`, `requirements.txt`, `requirements-dev.txt`, `.venv/`

This repository is the source of truth for `django-mcp`. It has no submodules
and no generated artefacts checked in.

## Spec-Before-Code Planning Discipline

This project follows a standards-body-style planning cycle: every behaviour
change is preceded by a ratified per-phase concepts doc under `docs/concepts/`.
Vocabulary drift and invariant erosion are the dominant failure modes once a
plan crosses ~3 phases, so the cycle exists to prevent silent forks, not as
ceremony.

### Normative keywords (RFC 2119 / 8174)

The key words **MUST**, **MUST NOT**, **SHALL**, **SHOULD**, **SHOULD NOT**,
**MAY**, and **RECOMMENDED** in all docs under `docs/concepts/` are interpreted
per RFC 2119 and RFC 8174. Use capitals when invoking the keyword; lowercase
for ordinary English. Plain narrative without capitalised keywords is
advisory, not binding.

### Normative vs. informative sections

In a per-phase `TODO-DMCP-NN-*.md`:

- Sections referenced by the phase's **Acceptance** checklist are **normative**
  — binding on implementers.
- All other sections (problem statement, narrative, non-goals, change log) are
  **informative**.
- The `docs/README.md` initiative-index entry for a phase is **informative**;
  the per-phase doc is the normative artifact.

Do not re-derive normative rules in README narrative — cite the per-phase doc
and section number.

### Conformance targets

Initiative-level acceptance lists in `docs/README.md` MUST name the conforming
artifact (e.g. "a conforming django-mcp deployment MUST satisfy gates (a)–(d)
of DMCP-01"). Optional phases yield a second conformance level. This lets
reviewers reason about partial deployments without re-arguing scope.

### Definitions — reference vs. restatement

For every term that also exists in upstream Django, the upstream MCP spec, or
this repository's code, the glossary entry MUST cite the authoritative source
and mark the relationship:

- **"As defined in [file:line]; used without modification."** — upstream is
  canonical; spec references it.
- **"As defined in [file:line]; adapted: [delta]."** — upstream is canonical;
  spec extends/narrows it with a named delta.
- **"Owned by `<PHASE>`; does not exist upstream yet."** — spec is canonical;
  code will mirror once the phase lands.

Silent restatement of an existing Django or MCP-spec definition is how forks
form. Don't do it.

### Frozen enumerations — registration policy

Every frozen enum (tool kind, permission mapping, transport mode, etc.)
declares its registration policy in the owning concepts doc:

- **Standards Action** — adding a value requires a §15 amendment to the
  `-00-CONCEPTS` doc and a ratification commit. Use for enums encoding
  invariants or cross-phase contracts.
- **Specification Required** — adding a value requires a phase-owner update to
  the local phase doc; no CONCEPTS amendment. Use for enums local to one
  phase's contract surface.
- **Expert Review** — phase owner MAY add with a PR-level note. Use for
  internal enums with no cross-phase coupling.

Default to Standards Action when in doubt; demote later if churn justifies.

### Phase document shape

A per-phase concepts doc follows this section layout: §0 authority policy
(which external doc owns which vocabulary), §1 purpose, §2 problem statement
(evidence, pinned to code with `path:line` cites), §3 canonical glossary, §4
source-of-truth map (one owner per concept), §5–§9 frozen decisions (enums,
invariants), §10 reconciliation decisions vs. adjacent Django/MCP primitives,
§11 non-goals, §12 acceptance checklist, §13 files cited, §14 unblocks, §15
change log. Phases beyond `-00` MAY omit the sections that do not apply; §0,
§3/§4, §10, §12, §15 are load-bearing.

### Execution discipline

Once a concepts doc is ratified (§15 dated entry), execution commits:

- Cite the phase as `DMCP<NN><letter>` in commit subject
  (e.g. `DMCP01a:`, `DMCP02b:`).
- Name in the commit body which invariants (from §9 of the concepts doc) the
  change touches, and how each is preserved.
- Touching a frozen enum value or an invariant requires a §15 amendment
  **first**, in a separate commit. No behaviour commit rides on an unamended
  invariant.

### Errata log

`docs/concepts/ERRATA.md` is the in-repo institutional memory for accepted bug
reports and spec deviations. Shape: status legend (🟢/🟡/🔴/⚪), Open
Questions section with `EOQ-NNN-ERRATA-NNN` handles, Index table, per-entry
sections (Symptom/Root cause/Fix/Verification/Tracking), "How to add" footer.
Entries are permanent — resolved entries stay as institutional memory.

**Stealth-revert prohibition**: a behaviour change that undoes
ratified-and-implemented phase content while landing under an unrelated
commit's scope is prohibited. If a revert is structurally necessary, file an
ERRATA entry FIRST (in a separate commit), then land the unrelated change
citing the ERRATA id, then flip the status icon as appropriate. The phase
doc's §15 ALSO gets a dated entry pointing at the errata.

### Standards integration: authority boundary declarations

`django-mcp` integrates two externally-authored grammars: **Django** (the web
framework whose views, URL patterns, admin, forms, and permissions this
package re-projects) and the **MCP specification** (currently revision
2025-03-26, the wire grammar this package emits). Citing them is not enough —
each externally-authored concept that crosses the package boundary MUST
declare its `AuthorityRelationship` using the seven-value enum:

- **mirror** — copy verbatim, no local divergence on field names or grammars.
- **adapt** — copy verbatim, add named local affordances around it; the
  affordances MUST NOT leak across the upstream wire boundary.
- **extend** — add named local fields/values on top of an unchanged upstream
  grammar; extensions live in a declared namespace (e.g. `__django_mcp`).
- **compose** — use upstream terms as components in a higher-level construct
  this package owns.
- **own** — this package authors the grammar; full mutation rights gated by
  spec-before-code.
- **derive** — interpret/evaluate/preflight an upstream grammar without owning
  it; outputs are local, inputs are upstream.
- **represent** — visualise the upstream grammar; display semantics are local,
  payload round-trip preserves upstream names.

Each external-grammar concept MUST be recorded as a row with six axes:
**upstream authority**, **local representation**, **mutation rights**,
**divergence policy**, **downstream consumers**, **conformance test owner**.
An undeclared local mirror reads as `mirror` with no mutation rights — never
silently as `own`.

### Applicability

This discipline applies to every doc under `docs/concepts/`. Single-file
exploratory notes under `docs/notes/` MAY use informal form; the moment a doc
acquires a `-NN-` prefix it is subject to the full discipline.

## Code Discipline

- **Spec before code.** No production code lands without a ratified phase doc
  whose §12 acceptance checklist enumerates the gate(s) the code satisfies. A
  commit that fails to cite its phase + invariant set is incomplete.
- **Don't restate Django.** When a concept exists in Django (Model, View,
  URLconf, ModelAdmin, permission), reference it; do not rebuild it. The
  whole pitch of this package is that MCP falls out of those existing
  primitives.
- **No silent surface.** Every MCP tool, resource, or prompt that this
  package surfaces MUST have a discoverable derivation rule. "I added a
  hand-written tool" is a smell; the right move is to write a derivation rule
  the registered view participates in.
- **Async/ORM boundary.** MCP handlers are async; Django ORM is synchronous.
  Wrap blocking ORM calls in `asyncio.to_thread()` (or use Django's
  `sync_to_async`). This is INV-DMCP-1 (to be ratified in `-00-CONCEPTS`).
- **Tests live next to invariants.** Each invariant in a phase's §9 SHOULD
  have a corresponding test that fails if the invariant is violated. The
  invariant id appears in the test's docstring.
- **No backwards-compat shims pre-1.0.** Until the package cuts a 1.0 release,
  breaking changes are allowed; cite the §15 amendment in the commit. Post-1.0
  the rule flips.

## Assistant Operations

- **Working directory.** Operate from `/Users/iraabbott/django-mcp/`.
- **Context priming.** Read `CLAUDE.md` (this file), `docs/README.md`, and the
  most recent `-NN-CONCEPTS.md` first. The softoboros repo at
  `/Users/iraabbott/softoboros/backend/mcp/` is **prior art**, not source of
  truth — consult it for transport / async-handler shape, but do not crawl
  it as if it were this repository.
- **Virtual env.** All Python invocations go through `.venv/`. If `.venv/`
  does not exist, create it before running anything (`python3 -m venv .venv
  && .venv/bin/pip install -r requirements-dev.txt`).
- **Plan first.** When asked to implement a phase, confirm the phase's
  `-NN-CONCEPTS.md` is ratified (§15 has a dated entry) before writing code.
  If not ratified, draft / amend the spec first and ask for ratification.

## MCP Wire Spec Pin

Target MCP revision: **2025-03-26** (streamable HTTP transport, tools +
resources + prompts surface). Upgrades to a newer MCP revision MUST land as a
§15 amendment to `TODO-DMCP-00-CONCEPTS.md` first, then a behaviour commit.
