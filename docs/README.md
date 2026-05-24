# django-mcp — Documentation Index

This index is **informative**. The binding artifacts are the per-phase
`-NN-CONCEPTS.md` documents under `concepts/`. When this index disagrees with
a ratified concepts doc, the concepts doc wins.

## Spec-Before-Code Discipline

See [`../CLAUDE.md`](../CLAUDE.md) for the full discipline (RFC 2119 keywords,
AuthorityRelationship matrix, phase document shape, ERRATA log conventions,
execution discipline).

## Initiative: DMCP

`django-mcp` exposes a Django project's registered views and URL patterns as
MCP tools / resources / prompts. The unifying principle: derivation rules
walk Django's existing registries — `admin.site`, the root URLconf, model
registries, app configs — and emit MCP surface area mechanically.

### Phases

| Phase | Title | Status | Doc |
|-------|-------|--------|-----|
| DMCP-00 | Foundational concepts and vocabulary | **ratified 2026-05-22** | [`concepts/TODO-DMCP-00-CONCEPTS.md`](concepts/TODO-DMCP-00-CONCEPTS.md) |
| DMCP-01 | Admin → MCP tools | **ratified 2026-05-22** | [`concepts/TODO-DMCP-01-ADMIN.md`](concepts/TODO-DMCP-01-ADMIN.md) |
| DMCP-02 | Applications → MCP tools | **ratified 2026-05-23** | [`concepts/TODO-DMCP-02-APPLICATIONS.md`](concepts/TODO-DMCP-02-APPLICATIONS.md) |
| DMCP-03 | Resources and prompts (beyond tools) | **ratified 2026-05-23** | [`concepts/TODO-DMCP-03-RESOURCES-PROMPTS.md`](concepts/TODO-DMCP-03-RESOURCES-PROMPTS.md) |
| DMCP-04 | Transport (streamable HTTP + STDIO + MCPAPIKey + audit) | **ratified 2026-05-23** | [`concepts/TODO-DMCP-04-TRANSPORT.md`](concepts/TODO-DMCP-04-TRANSPORT.md) |

### Conformance

A conforming `django-mcp` deployment MUST satisfy the acceptance gates listed
in each ratified phase's §12.

- **Core conformance** (when DMCP-00 and DMCP-01 are ratified and
  implemented): gates DMCP00-(a..n) ∪ DMCP01-(a..n).
- **Application conformance** (adds DMCP-02): plus DMCP02-(a..n).
- **Full conformance** (all phases): plus DMCP-03 and DMCP-04 gates.

### Errata

Active and historical issues live in
[`concepts/ERRATA.md`](concepts/ERRATA.md). See the file's "How to add" footer
for entry shape.

## Other documentation

- `notes/` — exploratory drafts and design sketches. **Not normative.**
  Material that hardens lands in a `-NN-CONCEPTS.md` doc; the note may stay
  as a pointer or be removed.
- [`ops/`](ops/) — operational runbooks. Informative, not normative.
  Current entries:
  - [`ops/oauth.md`](ops/oauth.md) — OAuth-mints-MCPAPIKey integration
    pattern; reuses DMCP-04 §6 MCPAPIKey lifecycle without any
    package code changes.
