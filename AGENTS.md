# AGENTS.md

Agents working in this repository follow the discipline in
[`CLAUDE.md`](CLAUDE.md). This file is a pointer, not a duplicate.

## Quick orientation

1. Read `CLAUDE.md` — establishes spec-before-code, RFC 2119 keywords, the
   `AuthorityRelationship` matrix, and the phase document shape.
2. Read `docs/README.md` — current initiative index; which phases are
   ratified, which are in draft.
3. Read the most recent `docs/concepts/TODO-DMCP-NN-*.md` whose §15 has a
   dated entry. That is the binding spec for the surface you are touching.
4. Activate `.venv/` before running anything. If it does not exist, create it
   per the README.

## What this repo is NOT

- Not a place to bolt hand-written MCP tools alongside derived ones. Every
  surfaced tool MUST have a discoverable derivation rule from a registered
  Django view, URL pattern, admin registration, or model. If a tool cannot be
  derived, write the derivation rule first; the tool follows.
- Not a fork of the softoboros `backend/mcp/` module. That module is prior
  art consulted for shape (async handler pattern, streamable HTTP), not the
  source of truth. Do not crawl it as if it were part of this repo.

## Authorization scope

- Local edits, tests, doc commits: proceed.
- Any commit that touches a `§9 invariant` or a frozen enum value: stop and
  draft the §15 amendment first; ratification is a user action.
- Publishing to PyPI, force-pushing, or any operation that affects state
  outside this working directory: confirm with the user.
