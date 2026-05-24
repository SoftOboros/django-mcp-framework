# Operations

Runbooks and integration recipes for `django-mcp`. **Informative**, not
normative — these documents describe how to operate the package; the
binding contracts live in `docs/concepts/`.

## Recipes

- [`oauth.md`](oauth.md) — OAuth-mints-MCPAPIKey integration pattern.
  How to bolt an OAuth flow onto a Django project so that successful
  authentication produces a long-lived (or short-lived) MCP API key
  that the client uses for subsequent MCP calls. Pattern A
  (provision-on-callback) and Pattern B (provision-on-demand) are
  both covered.

## How operational decisions relate to spec docs

When an operational decision implies a change to the package's binding
contracts (a new setting, a new permission semantic, a new wire field),
the runbook MUST reference the concepts doc that owns the contract and
the §15 amendment that the change would require. Operations docs do
NOT redefine spec; they teach how to live with it.
