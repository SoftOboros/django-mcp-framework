# TODO-DMCP-04-TRANSPORT — Transport, MCPAPIKey, audit

> **Status:** **ratified 2026-05-23** (see §15). DMCP-00..03 ratified
> earlier (2026-05-22 through 2026-05-23). This phase reuses their
> `DerivationRule` plumbing and per-call permission-resolver shape —
> invariants INV-DMCP-1..7, INV-DMCP01-1..5, INV-DMCP02-1..8, and
> INV-DMCP03-1..10 are inherited without modification unless §10 records
> a named deviation.

## §0 — Authority policy

DMCP-04 is the phase that finally makes everything reachable from an
external MCP client. It crosses three authority boundaries that earlier
phases only described.

| Upstream grammar | Wire revision / version | Crawl boundary |
|------------------|-------------------------|----------------|
| Model Context Protocol | 2025-03-26 (inherited from DMCP-00 §0) | The §"Streamable HTTP" transport sub-spec and the §"STDIO" sub-spec are now BOTH load-bearing. So is the §"Initialize" handshake, §"tools/*", §"resources/*", §"prompts/*", and the JSON-RPC 2.0 framing the wire requires. |
| JSON-RPC 2.0 | Specification 2010-03-26 | Used verbatim for `id` / `method` / `params` / `result` / `error` framing. Reserved error codes `-32700..-32603` honoured. Application errors live in `-32000..-32099` per the spec; DMCP-04 §5.5 freezes the per-outcome codes within that range. |
| Django | inherited | New crawl points: `django.views.View` (async), `django.contrib.auth.hashers` (for API-key secrets), `django.core.management.BaseCommand` (for the STDIO entry point), `django.db.models` (for the `MCPAPIKey` model itself). |

DMCP-04 introduces no new Django-side primitives — `MCPAPIKey` is a
plain Django model gated by spec-before-code §15 amendments to this doc,
the same way DMCP-01..03's emitted descriptors are.

## §1 — Purpose

Ship the wire and credential surface that lets an MCP client invoke the
tools, resources, and prompts emitted by DMCP-01..03 against a running
Django project.

Concretely, a project that has:

- `django_mcp` added to `INSTALLED_APPS`,
- `path("mcp/", include("django_mcp.urls"))` mounted in `ROOT_URLCONF`,
- One `MCPAPIKey` created via `manage.py mcp_key create alice
  --name="alice's laptop"`,

SHOULD acquire — with no further configuration — a working MCP HTTP
endpoint at `/mcp/` (and a separate `manage.py mcp_server` for STDIO),
both routing JSON-RPC `tools/list` / `tools/call` /
`resources/templates/list` / `resources/read` / `prompts/list` /
`prompts/get` to the existing registry. Authentication uses the printed
key string verbatim; per-call authorisation reuses the per-descriptor
`auth_check` from earlier phases (INV-DMCP-3 parity preserved end-to-end).

## §2 — Problem statement

Three failure modes drove the prior-art hand-written shape at
`/Users/iraabbott/softoboros/backend/mcp/`:

1. **Auth-credential entanglement.** Reusing Django session cookies or
   DRF tokens for MCP would mean revoking an MCP credential could log
   the human user out of the admin (or vice versa). Operators had to
   author a separate auth path by hand; the path drifted from the
   Django auth contract over time. INV-DMCP04-3 / §10.1 close this by
   making the MCP key the *only* credential the transport consults.

2. **Audit-trail erosion.** When tool calls fail, hand-written shapes
   tend to log only the failure path; the success path goes silent.
   Operators investigating "did Alice's key actually invoke X at
   timestamp Y?" hit logs that say "no". INV-DMCP-7 / INV-DMCP04-5
   anchor the every-call audit line.

3. **Permission-outcome ambiguity.** Returning a generic "403" for both
   "this user doesn't have the perm" and "this key isn't allowed to ask
   for this tool at all" hides the second failure from the operator
   (which is usually the more interesting one). The four-outcome enum
   in DMCP-00 §7 exists to surface this; DMCP-04 §5.5 maps the four to
   distinct JSON-RPC error codes so the client can tell them apart.

Evidence pins:
- `softoboros/backend/mcp/http_transport.py:1-1162` is the hand-written
  streamable-HTTP transport for the prior project. The shape we adopt
  is informed by its working pattern; the divergences are recorded in
  §10.
- `softoboros/backend/mcp/models.py:1-365` is the prior `MCPAPIKey`
  model. DMCP-04 §6 adapts the shape with named deviations.
- `softoboros/backend/mcp_server.py:1-212` is the prior STDIO entry
  point. DMCP-04 §5.2 mirrors the dispatch loop.

## §3 — Glossary (additions only; rest inherited from DMCP-00..03 §3)

- **JSON-RPC envelope** — As defined in JSON-RPC 2.0; used without
  modification on the wire. Carries `jsonrpc: "2.0"`, `id`, `method`,
  `params`; responses carry `id`, `result` OR `error`. Notifications
  (no `id`) are allowed per the spec but DMCP-04 emits NONE (responses
  are 1:1 with requests).
- **MCP method** — A string of the form `<surface>/<verb>` per MCP
  2025-03-26. The frozen set DMCP-04 dispatches: `initialize`,
  `tools/list`, `tools/call`, `resources/list`, `resources/templates/
  list`, `resources/read`, `prompts/list`, `prompts/get`. Other MCP
  methods (notifications, sampling, `resources/subscribe`, etc.) are
  out of scope; see §11.
- **Transport mode** — One of `streamable-http` (HTTP POST endpoint,
  the default) or `stdio` (the `manage.py mcp_server` entry point).
  Frozen in DMCP-00 §8; DMCP-04 implements both.
- **Streamable HTTP** — The MCP 2025-03-26 transport that uses an HTTP
  POST endpoint. The "streamable" affordance (SSE responses for long-
  running ops) is OPTIONAL per the spec and OUT OF SCOPE for
  DMCP-04 — every response is a single JSON body. See §11.
- **MCPAPIKey** — Owned by DMCP-00 §3 / DMCP-04. A Django model
  carrying `key_id`, `secret_hash`, `user` (FK to `AUTH_USER_MODEL`),
  `name`, `created`, `last_used_at`, `expires_at`, `revoked_at`,
  `allowed_tools`. The wire credential string is `<key_id>.<secret>`;
  the secret is never stored in plaintext.
- **Wire credential** — Owned by DMCP-04. A single string of the form
  `<key_id>.<secret>`. Presented in the `Authorization: Bearer <wire
  credential>` HTTP header (streamable-http) or as the first line of
  STDIO input (handshake; see §5.2). Per INV-DMCP04-3 this is the only
  credential the transport consults.
- **Capability set** — Owned by DMCP-04. The dict returned in
  `initialize`'s `capabilities` field, declaring which surfaces the
  server supports. Per INV-DMCP04-2 only surfaces with non-empty
  registries are advertised.
- **Audit entry** — Owned by DMCP-04 §8. A structured log record
  containing the fields named in INV-DMCP-7; serialised as a single
  JSON line on a configurable Python logger.

## §4 — Source-of-truth map (additions only)

| Concept | Upstream authority | Local representation | Mutation rights | Divergence policy | Downstream consumers | Conformance test owner |
|---------|--------------------|----------------------|-----------------|-------------------|----------------------|------------------------|
| MCP `initialize` request/response | MCP 2025-03-26 | `mirror` — wire shape verbatim, server capabilities filled from the registry | None on wire fields | A new MCP revision lands as a §15 amendment to DMCP-00 §0 first | All MCP clients | DMCP-04 |
| MCP `tools/list`, `tools/call` | MCP 2025-03-26 | `mirror` | None | Same as above | All MCP clients | DMCP-04 |
| MCP `resources/list`, `resources/templates/list`, `resources/read` | MCP 2025-03-26 | `mirror` | None | Same as above | All MCP clients | DMCP-04 |
| MCP `prompts/list`, `prompts/get` | MCP 2025-03-26 | `mirror` | None | Same as above | All MCP clients | DMCP-04 |
| JSON-RPC 2.0 framing | JSON-RPC 2.0 spec | `mirror` | None | Frozen spec | All MCP clients | DMCP-04 |
| HTTP `Authorization: Bearer` header | RFC 6750 | `derive` — DMCP-04 parses the header to extract the wire credential; bearer-token grammar is RFC-imposed | None | DMCP-04 does NOT consult `WWW-Authenticate` challenge negotiation; the bearer is the only flow | All MCP clients | DMCP-04 |
| Django auth user resolution | Django | `derive` — the resolved user comes from `MCPAPIKey.user`; Django session middleware is NOT consulted (INV-DMCP04-3) | None | A future amendment MAY add a session-backed dev mode; pre-1.0 the rule is strict | All phases | DMCP-04 |
| MCPAPIKey schema | (none — locally owned, informed by softoboros prior art) | `own` | Full, gated by §15 amendments | n/a | All phases | DMCP-04 |
| Error-code mapping (PermissionOutcome → JSON-RPC) | (none — locally owned) | `own` | Full, gated by §15; client compat means changes are not free | n/a | All MCP clients | DMCP-04 |
| Audit log line shape | (none — locally owned) | `own` | Full | Operators rely on the shape for SIEM ingestion; changes go through §15 | All operators | DMCP-04 |

## §5 — Frozen wire surface

### §5.1 — Streamable HTTP transport

A single Django view is mounted at the URL include's root path:

```python
# Consumer's urls.py
path("mcp/", include("django_mcp.urls"))
```

`django_mcp.urls` exposes exactly one endpoint: `POST <mount>/`. The
mount-trailing-slash is normative — the spec name "MCP endpoint" refers
to this exact path. INV-DMCP04-1 anchors single-endpoint-per-transport.

Request shape:

- Method: `POST`.
- Headers: `Content-Type: application/json`; `Authorization: Bearer
  <wire_credential>` (REQUIRED, per INV-DMCP04-3).
- Body: a JSON-RPC 2.0 request envelope: `{"jsonrpc": "2.0", "id":
  <int|string>, "method": "<mcp_method>", "params": {...}}`.

Response shape:

- Status: `200 OK` for valid JSON-RPC envelopes, even when the contained
  call surfaces an application error (per the JSON-RPC spec —
  application errors are inside the envelope, not at the HTTP layer).
  Transport-level rejections (malformed bearer, missing
  `Content-Type`, body not JSON) return `400 Bad Request` with a plain-
  text body. `401 Unauthorized` is reserved for missing/invalid
  bearer; `403 Forbidden` for a key that is revoked OR expired. See
  §7 for the full flow.
- Headers: `Content-Type: application/json`.
- Body: a JSON-RPC 2.0 response envelope: `{"jsonrpc": "2.0", "id":
  <same as request>, "result": {...}}` OR `{"jsonrpc": "2.0", "id":
  <...>, "error": {"code": <int>, "message": "<...>", "data": {...}?}}`.

CSRF: the endpoint is `@csrf_exempt`. INV-DMCP04-4 names the scope —
this exempts ONLY the configured MCP mount points, never the whole
project.

### §5.2 — STDIO transport (`manage.py mcp_server`)

`manage.py mcp_server` exposes the same dispatch over stdin/stdout for
Claude Desktop-style clients. Loop:

1. Read one line of JSON from stdin.
2. Parse as a JSON-RPC request envelope.
3. Dispatch through the same per-method handlers the HTTP transport
   uses (no duplication).
4. Write one line of JSON to stdout (the response envelope).
5. Repeat until stdin closes.

Authentication for STDIO: the wire credential is read from the
`DJANGO_MCP_KEY` environment variable at server start (the host process
sets it; the user never sees it). A missing `DJANGO_MCP_KEY` is a hard
startup error — STDIO MUST NOT be reachable anonymously.

The STDIO server SHOULD log to stderr (not stdout — stdout is the wire).

### §5.3 — JSON-RPC method dispatch

The dispatcher accepts exactly the following methods. Anything else
returns JSON-RPC error `-32601 Method not found`.

| Method | Params | Result |
|--------|--------|--------|
| `initialize` | `{"protocolVersion": "<client wants>", "capabilities": {...}, "clientInfo": {...}}` | `{"protocolVersion": "2025-03-26", "capabilities": <see §5.4>, "serverInfo": {"name": "django-mcp", "version": "<pkg version>"}}` |
| `tools/list` | `{"cursor": "<opt>"}` | `{"tools": [<ToolDescriptor wire repr>...], "nextCursor": "<opt>"}` — see §5.3.1 |
| `tools/call` | `{"name": "<tool_name>", "arguments": {...}}` | `{"content": [...], "isError": false}` OR error envelope |
| `resources/list` | `{"cursor": "<opt>"}` | `{"resources": [<concrete-uri descriptors>], "nextCursor": "<opt>"}` |
| `resources/templates/list` | `{"cursor": "<opt>"}` | `{"resourceTemplates": [<template-uri descriptors>], "nextCursor": "<opt>"}` |
| `resources/read` | `{"uri": "<concrete uri>"}` | `{"contents": [{"uri": "<...>", "mimeType": "<...>", "text" | "blob": "<...>"}]}` |
| `prompts/list` | `{"cursor": "<opt>"}` | `{"prompts": [<PromptDescriptor wire repr>...], "nextCursor": "<opt>"}` |
| `prompts/get` | `{"name": "<prompt_name>", "arguments": {...}}` | `{"description": "<...>", "messages": [<message wire shape>]}` |

**Registration policy for this enum:** Standards Action. Adding a new
dispatched method (e.g. `completion/complete`, `sampling/createMessage`)
requires a §15 amendment HERE plus a §15 amendment to whichever phase
owns the underlying descriptor type.

#### §5.3.1 — `tools/list` wire shape

Each tool's wire representation is constructed from the
`ToolDescriptor` per phase DMCP-01 / DMCP-02:

```json
{
  "name": "<tool_name>",
  "description": "<derived; see below>",
  "inputSchema": <ToolDescriptor.input_schema verbatim>
}
```

`description` derivation (when the descriptor has no explicit one — and
the current `ToolDescriptor` from DMCP-00 §3 does not — DMCP-04 derives
from `origin`): `f"Derived MCP tool from {origin}"`. **Promotion to a
first-class `description` field on `ToolDescriptor` is a future §15
amendment** to DMCP-00 §3; recorded here as an open question, not a
DMCP-04 blocker.

The response splits the registry by 100 entries per page (default
`DJANGO_MCP_PAGE_SIZE = 100`); pagination uses an opaque cursor (an
integer offset, base64-encoded so clients treat it as opaque).
INV-DMCP04-7 applies: list-time MUST NOT invoke any descriptor's
handler.

The `tools/list` result is **the same set of names for every caller**
per INV-DMCP01-4 (and the parallel INV-DMCP03-9 for prompts). The
per-user gate happens at `tools/call` time.

#### §5.3.2 — `tools/call` flow

1. Look up `name` in `MCPRegistry.tools`. Miss → JSON-RPC error
   `-32602 Invalid params: unknown tool "<name>"`.
2. Build a `ToolCallContext(user=<resolved_user>, arguments=params.arguments, request_meta={"transport": "...", "correlation_id": "<...>"})`.
3. Invoke `tool.auth_check(ctx)`. Translate the four outcomes per §5.5.
4. On `ALLOW`: await `tool.handler(ctx)` in the async event loop.
5. Translate the handler's return dict into `{"content":
   [{"type": "text", "text": json.dumps(<result>)}], "isError":
   false}`. Tools that want richer content shapes (multi-content,
   images) are deferred to a future amendment.
6. On any exception inside the handler: catch, log per §8, return
   `{"content": [{"type": "text", "text": "<safe error message>"}],
   "isError": true}` per the MCP convention that *tool* errors are
   in-envelope, not JSON-RPC errors. Auth errors at step (3) ARE
   JSON-RPC errors.

#### §5.3.3 — `resources/list` vs `resources/templates/list`

Per MCP 2025-03-26 the two methods split by URI shape:

- `resources/list` returns only ResourceDescriptors with `is_template
  is False`.
- `resources/templates/list` returns only ResourceDescriptors with
  `is_template is True`.

DMCP-01..03 emit predominantly templates (`django-mcp://model/...
/{pk}`); the concrete-resource list is typically empty unless a future
phase emits `meta/` or `static/` concrete URIs.

#### §5.3.4 — `resources/read` flow

1. Parse `params.uri`. Look up an exact match in
   `MCPRegistry.resources` (concrete case) OR find the template whose
   pattern matches the concrete URI (template case — match the path
   segment by segment, extracting `{placeholder}` substitutions into a
   dict).
2. Miss → JSON-RPC `-32602 Invalid params: unknown resource uri`.
3. Build `ToolCallContext(user=<...>, arguments=<extracted
   placeholders>, request_meta={...})`.
4. Invoke `resource.auth_check(ctx)`. Translate per §5.5.
5. On `ALLOW`: await `resource.read_handler(ctx)`. The handler returns
   either a JSON-serialisable Python value (DMCP-03 model resources)
   OR `bytes` (DMCP-03 field resources).
6. Wire serialisation:
   - JSON-serialisable: `{"contents": [{"uri": "<concrete uri>",
     "mimeType": "<resource.mime_type>", "text": json.dumps(<value>)}]}`.
   - Bytes: `{"contents": [{"uri": "<concrete uri>", "mimeType":
     "<resource.mime_type>", "blob": "<base64>"}]}`. INV-DMCP03-6 is
     preserved — the declared mime is what the descriptor said.

#### §5.3.5 — `prompts/get` flow

Parallel to `tools/call`, but calls `prompt.render_handler` (sync) and
returns `{"description": prompt.description, "messages": <list>}`. The
render handler MUST NOT raise on missing arguments (INV-DMCP03-5); a
handler exception is treated as JSON-RPC error `-32603 Internal error`
and logged per §8.

### §5.4 — Initialize handshake and capability declaration

Server's `initialize` response:

```json
{
  "protocolVersion": "2025-03-26",
  "capabilities": {
    "tools":     { "listChanged": false },
    "resources": { "listChanged": false, "subscribe": false },
    "prompts":   { "listChanged": false }
  },
  "serverInfo": {
    "name": "django-mcp",
    "version": "<django_mcp.__version__>",
    "djangoMcpProtocolPhase": "DMCP-04"
  }
}
```

Per **INV-DMCP04-2 (capability declaration honesty)**: a capability
block is OMITTED from the response when its corresponding registry dict
is empty. A project that uses only `django.contrib.auth` admin
registrations and emits no resources or prompts (e.g.
`DJANGO_MCP_RESOURCES_DISABLED=True`) MUST NOT advertise
`resources` or `prompts` capabilities. Clients that try
`resources/list` against such a server get `-32601 Method not found`.

`listChanged: false` is permanent for DMCP-04 — discovery happens once
per process (INV-DMCP-5), so the server CANNOT change its list mid-
session. `subscribe: false` matches INV-DMCP03-10.

The `protocolVersion` echoed back is the wire revision DMCP-00 §0
pinned, NOT whatever the client requested. If the client's requested
revision differs, the server still returns its pinned value; the client
is then responsible for deciding whether to continue. A future
amendment MAY add negotiation.

### §5.5 — Error-code mapping (PermissionOutcome → JSON-RPC)

**Registration policy for this enum:** Standards Action.

| PermissionOutcome | JSON-RPC code | JSON-RPC message | HTTP status (transport-level) |
|-------------------|---------------|------------------|-------------------------------|
| `UNAUTHENTICATED` | `-32001` | `"unauthenticated"` | `401 Unauthorized` (only if the failure is at the bearer-resolution step; otherwise 200 with in-envelope error) |
| `DENY` | `-32002` | `"forbidden"` | `200 OK` (in-envelope error) |
| `OUT_OF_SCOPE` | `-32003` | `"out_of_scope: tool not in this key's allowlist"` | `200 OK` |
| `ALLOW` | n/a (no error) | n/a | n/a |

Reserved JSON-RPC codes DMCP-04 uses:

- `-32700` Parse error — body is not valid JSON.
- `-32600` Invalid request — JSON-RPC envelope is missing required
  fields.
- `-32601` Method not found — unknown MCP method, OR a capability not
  advertised in §5.4.
- `-32602` Invalid params — missing/bad tool/resource/prompt name, bad
  arguments shape that fails before the handler runs.
- `-32603` Internal error — handler exception not classified above; the
  audit log carries the stack trace, the wire message stays generic.

Application-specific codes `-32000..-32099` are reserved for DMCP-04's
auth outcomes (above) and future per-phase amendments.

## §6 — MCPAPIKey model

### §6.1 — Schema

Per DMCP-00 §10 (auth-credential separation): a Django model named
`MCPAPIKey` lives in `django_mcp/models.py`. Frozen fields:

| Field | Type | Notes |
|-------|------|-------|
| `key_id` | `CharField(max_length=24, unique=True, db_index=True)` | Public identifier; URL-safe random 24 chars. Used as the lookup key on every request. |
| `secret_hash` | `CharField(max_length=128)` | Hashed via `django.contrib.auth.hashers.make_password` (PBKDF2 by default). The plaintext secret is shown to the operator once at creation and never again. |
| `user` | `ForeignKey(AUTH_USER_MODEL, on_delete=CASCADE)` | The Django user this key resolves to. Cascade-delete: removing the user invalidates the key. |
| `name` | `CharField(max_length=120)` | Operator-supplied label. |
| `created_at` | `DateTimeField(auto_now_add=True)` | Audit. |
| `last_used_at` | `DateTimeField(null=True)` | Updated on every successful auth (best-effort; written async without blocking the response). |
| `expires_at` | `DateTimeField(null=True)` | Optional. `None` means "no expiry". |
| `revoked_at` | `DateTimeField(null=True, db_index=True)` | Set when the operator runs `mcp_key revoke`; checked on every request. |
| `allowed_tools` | `JSONField(default=list)` | Empty list = "all tools". Non-empty list = OUT_OF_SCOPE for any tool whose `tool_name` is not in the list. Resource URIs and prompt names use the same matcher (the list element can be a tool name, a resource URI template, or a prompt name). |

**Registration policy:** Standards Action for the field set; adding or
removing a field requires a §15 amendment to this section AND a Django
migration in the same wave.

### §6.2 — Wire credential format

The credential the operator copies into the client is:

```
<key_id>.<secret>
```

`key_id` is the 24-char DB identifier; `secret` is a 32-char URL-safe
random token. The two are joined by a literal `.` (period). The wire
credential length is therefore 24 + 1 + 32 = 57 chars.

On request:

1. Parse the `Authorization: Bearer <credential>` header.
2. Split on `.`. If there are not exactly two parts → `401`.
3. Look up `MCPAPIKey.objects.filter(key_id=<first part>).first()`. If
   None → `401`.
4. Check `revoked_at is None` AND (`expires_at is None or expires_at
   > now`). If either fails → `403`.
5. Verify the secret against `secret_hash` using
   `django.contrib.auth.hashers.check_password`. Fail → `401`.
6. Resolve `user = key.user`. The user MUST also have `is_active =
   True`; else `403`.
7. Update `last_used_at` (best-effort, non-blocking).

The `<key_id>.<secret>` form is the only credential format DMCP-04
accepts. Bare-token formats (no separator) and base64-wrapped variants
are rejected at step (2).

### §6.3 — Lifecycle

- **Create.** A new key is minted by the management command (§6.4) OR
  programmatically via `MCPAPIKey.objects.create_key(user=..., name=
  ...)` — a custom manager method that returns `(MCPAPIKey, plaintext_
  secret)`. The plaintext is returned ONCE; subsequent calls must
  rotate.
- **Rotate.** The management command `mcp_key rotate <key_id>` issues a
  fresh secret, updates `secret_hash`, and prints the new wire
  credential. The previous secret is immediately invalid.
- **Revoke.** Sets `revoked_at = now`. The next request fails at §6.2
  step (4). **INV-DMCP04-6 (immediate)**: there is no in-process cache
  of resolved keys — each request hits the DB. A future amendment MAY
  add a memcache layer with explicit invalidation.
- **Expire.** Operator-set `expires_at` causes step (4) to fail past
  the timestamp.

### §6.4 — Management commands

`django_mcp/management/commands/mcp_key.py` exposes one Django
management command with subcommands:

| Subcommand | Usage | Effect |
|------------|-------|--------|
| `create` | `manage.py mcp_key create <username> --name "<label>" [--allowed-tools NAME1,NAME2,...] [--expires-in <days>]` | Creates the key; prints the wire credential ONCE. |
| `list` | `manage.py mcp_key list [--user <username>]` | Lists keys (key_id, name, user, status). Never prints secrets. |
| `revoke` | `manage.py mcp_key revoke <key_id>` | Sets `revoked_at = now`. |
| `rotate` | `manage.py mcp_key rotate <key_id>` | Issues a new secret; prints the new wire credential. |
| `inspect` | `manage.py mcp_key inspect <key_id>` | Shows fields incl. `allowed_tools`; never the secret. |

The command MUST exit with a non-zero status when authenticating
against a missing user (`create`) or a missing key (`revoke`/`rotate`/
`inspect`).

## §7 — Authentication & authorization flow at request time

Per request (HTTP):

1. **Transport rejections.**
   - Body not JSON → `400 Bad Request`, plain-text reason.
   - `Authorization` header missing → `401 Unauthorized`, no body.
   - `Authorization` header malformed (not `Bearer <...>`) → `401`.
2. **Credential resolution (§6.2).** A failure at any step (1)–(6)
   returns `401` (missing/invalid) or `403` (revoked/expired/inactive
   user). The wire response body is empty; the audit log carries the
   reason (§8).
3. **JSON-RPC envelope validation.** Failures here are in-envelope
   per §5.5: `-32700` (parse), `-32600` (invalid request).
4. **Method dispatch.** Unknown method → `-32601`. The `initialize`
   handshake has no auth_check; every other method routes to its
   descriptor lookup.
5. **`allowed_tools` enforcement.** For `tools/call`, `resources/read`,
   `prompts/get`: if the key's `allowed_tools` is non-empty AND the
   requested name/URI is not in the list → JSON-RPC `-32003
   out_of_scope` (DMCP-00 §7 `OUT_OF_SCOPE`).
6. **Descriptor `auth_check` (§5.5).** The per-descriptor check
   determines `ALLOW` / `DENY` / `UNAUTHENTICATED`. The check MUST
   never see `OUT_OF_SCOPE` — that decision is upstream at step (5).
7. **Handler invocation.** `ALLOW` only. Handler exceptions are caught
   per §5.3.2 (tools/in-envelope) / §5.3.5 (prompts/JSON-RPC).
8. **Audit log emit (§8).** Always — successes AND failures.
9. **`last_used_at` update.** Async / fire-and-forget.

For STDIO: same flow, sans the HTTP-status differences. The wire
response is always a JSON-RPC envelope on stdout. Bearer-resolution
failures produce JSON-RPC envelopes with code `-32001 unauthenticated`.

## §8 — Audit logging

A single Python logger `django_mcp.audit` carries every invocation as a
structured INFO record. Per **INV-DMCP-7 (audit trail)** and
**INV-DMCP04-5**:

```python
{
  "ts": "2026-05-24T15:23:11.123456Z",          # ISO-8601 UTC
  "correlation_id": "<uuid4>",                   # ToolCallContext.request_meta["correlation_id"]
  "transport": "streamable-http" | "stdio",
  "key_id": "<MCPAPIKey.key_id>" or null,        # null only for pre-auth failures
  "user_id": <int> or null,
  "method": "tools/call" | "resources/read" | ...,
  "target": "<tool_name | resource uri | prompt name>" or null,
  "outcome": "allow" | "deny" | "unauthenticated" | "out_of_scope" | "handler_error" | "transport_error",
  "duration_ms": <float>,
  "wire_status": <http status int> | null,        # null for stdio
  "error_code": <json-rpc code int> | null,
  "error_message": "<short>" | null
}
```

The `wire_status`, `error_code`, `error_message` triple is null for
`outcome=allow`. The serialisation is a single JSON-line per record
(JSONL); operators pipe to their SIEM of choice.

The audit logger's level is INFO. Operators who want to suppress audit
records SHOULD configure the `django_mcp.audit` logger; the package
itself does not provide a kill switch (audit is on by default and is
mandatory per INV-DMCP-7).

## §9 — Invariants (this phase; inherits earlier invariants)

- **INV-DMCP04-1 (single endpoint per transport).** Streamable HTTP
  exposes exactly ONE URL pattern. STDIO exposes exactly ONE
  management command. Routing inside is JSON-RPC `method`-based, not
  HTTP-path-based.
- **INV-DMCP04-2 (capability declaration honesty).** The `initialize`
  response declares only capabilities whose registry is non-empty. A
  client that calls a method whose capability was not advertised gets
  `-32601 Method not found`.
- **INV-DMCP04-3 (authenticated-by-key, not by session).** Django
  session middleware is NOT consulted for MCP requests. The resolved
  user comes from `MCPAPIKey.user`. A signed-in admin user in the
  same browser session does NOT get implicit MCP access; they need a
  key.
- **INV-DMCP04-4 (CSRF exemption is scoped).** Only the configured MCP
  mount-points carry `@csrf_exempt`. The rest of the project's URLs
  retain their normal CSRF posture.
- **INV-DMCP04-5 (audit on every call).** Every `tools/call`,
  `resources/read`, `prompts/get` — and every pre-auth failure —
  produces one audit log record per §8.
- **INV-DMCP04-6 (key revocation is immediate).** Revoking a key
  causes the next request authenticated with it to fail at §6.2 step
  (4). There is no in-process cache of resolved keys.
- **INV-DMCP04-7 (no descriptor execution at list time).** `tools/
  list`, `resources/list`, `resources/templates/list`, and `prompts/
  list` return metadata only. Handlers are NOT invoked.
- **INV-DMCP04-8 (per-call auth_check is the gate).** Every tool /
  resource / prompt invocation runs the descriptor's `auth_check`
  BEFORE the handler. The four-outcome enum maps to JSON-RPC errors
  per §5.5. The order is: `allowed_tools` enforcement first
  (`OUT_OF_SCOPE`), then `auth_check` (`ALLOW`/`DENY`/`UNAUTHENTICATED`).
- **INV-DMCP04-9 (initialize is unauthenticated-readable).** The
  `initialize` method is reachable WITHOUT a valid bearer — but it
  returns ONLY server identity + capabilities, never descriptor
  contents. Rationale: clients negotiate capabilities before
  authenticating. (Other methods still require a bearer.)
  Counterargument considered and rejected: requiring bearer for
  `initialize` would force clients to attempt a method without knowing
  what's available, which deteriorates UX for no security gain — the
  server identity and capability set are not secret.
- **INV-DMCP04-10 (async/ORM in transport).** The streamable-HTTP view
  is an `async def`. Inside, every blocking ORM call (MCPAPIKey
  lookup, `last_used_at` update, descriptor handler invocation) goes
  through `asyncio.to_thread` or `asgiref.sync_to_async`. Parallel to
  INV-DMCP-1 but at the transport layer.

## §10 — Reconciliation with adjacent primitives

### §10.1 — Auth credential separation (revisited from DMCP-00 §10)

DMCP-00 §10 pinned the policy ("MCP callers authenticate with a
dedicated MCPAPIKey, not by reusing Django session cookies or DRF
tokens"). DMCP-04 implements it. The implementation does NOT consult:

- `django.contrib.auth.middleware.AuthenticationMiddleware` (no
  `request.user` is set by Django for MCP requests; DMCP-04 sets its
  own `request.user` from the resolved key).
- `rest_framework.authentication.*` (DRF auth backends).
- Any OAuth flow.

A future phase MAY add an OAuth integration; that phase would land as
DMCP-05 (or later) and would amend §4 to record the new authority
boundary. Pre-1.0, the rule is strict.

### §10.2 — CSRF middleware interaction

The MCP view is `@csrf_exempt`. The reasoning:

- POST requests carrying JSON bodies authenticated by bearer token are
  not CSRF-vulnerable in the classical sense (the attacker cannot
  forge the `Authorization` header from a victim's browser without
  XSS).
- Enforcing CSRF would require the MCP client to send a cookie-bound
  CSRF token, which clients do not naturally have.

INV-DMCP04-4 makes the exemption scoped — only the MCP mount points
are exempt. The project's HTML form views, admin POSTs, etc. keep
their normal CSRF protection.

### §10.3 — Async dispatch and ORM

The MCP HTTP endpoint is implemented as an `async def` view. Django
4.2+ supports this natively. Inside the view, the bearer-resolution
step does a DB read (`MCPAPIKey.objects.filter`); descriptor handlers
already wrap their ORM in `asyncio.to_thread` per INV-DMCP-1. DMCP-04
extends the same discipline to its own DB ops via `sync_to_async` on
the manager call. The `last_used_at` update is intentionally not
awaited — fire-and-forget via `asyncio.create_task` — so a request's
critical-path latency is not affected.

### §10.4 — URL placement: package-level vs site-level

The default is to mount via `include("django_mcp.urls")` in the
project's `ROOT_URLCONF`. The package does NOT auto-mount via
`AppConfig.ready` (no URL injection — would surprise the operator).
The package's `urls.py` exposes exactly the streamable-HTTP endpoint
at the include's root path.

A project can change the mount path freely (e.g. `path("api/mcp/",
include("django_mcp.urls"))`); the audit log's `transport=
"streamable-http"` does not record the mount path. Operators tracking
multiple mounts SHOULD use distinct Python loggers under
`django_mcp.audit.<name>`.

### §10.5 — `allowed_tools` matching

A non-empty `MCPAPIKey.allowed_tools` is compared against:

- The exact `tool_name` for `tools/call`.
- The exact resource URI template (e.g.
  `"django-mcp://model/auth.User/{pk}"`) for `resources/read`.
  Concrete URIs match if any template that expands to them is in the
  allowlist.
- The exact prompt name for `prompts/get`.

Wildcards / glob patterns are out of scope; a future amendment MAY add
them. Pre-1.0, the list is exact-match. Empty list means "every tool /
resource / prompt this key's user can see via descriptor auth_check".

### §10.6 — Transport-level vs JSON-RPC errors

Some failures could be expressed either way (e.g. a missing bearer
could be JSON-RPC `-32001` in a 200 body OR HTTP `401`). DMCP-04
splits them:

- **Pre-envelope failures** (bad bearer, malformed JSON, missing
  `Content-Type`) → HTTP status, NO JSON-RPC envelope. The client may
  not even have a valid `id` to echo back.
- **In-envelope failures** (unknown method, unknown tool name,
  auth_check `DENY`, handler exception) → HTTP 200, JSON-RPC error in
  the response body.

The split mirrors the MCP 2025-03-26 spec's own treatment.

## §11 — Non-goals

- **SSE / streaming responses.** Every response is a single JSON
  body. Long-running operations that would benefit from incremental
  output are out of scope for DMCP-04. A future amendment MAY add SSE.
- **`resources/subscribe`.** Inherited from DMCP-03 §11 /
  INV-DMCP03-10.
- **`sampling/createMessage` and other MCP server→client methods.**
  DMCP-04 is request/response only. The server does not initiate
  messages to the client.
- **OAuth, OIDC, SAML.** Bearer + MCPAPIKey only. A future phase may
  add an OAuth bridge that mints MCPAPIKeys on successful flow
  completion; the bridge would land as its own phase.
- **Multi-tenant routing.** A single Django process serves one
  `MCPRegistry`. Multi-tenancy (per-tenant registries, per-tenant
  keys) is out of scope.
- **Rate limiting.** Operators wanting rate limiting deploy a reverse
  proxy (nginx, envoy) in front of the MCP mount. DMCP-04 does not
  ship its own rate limiter.
- **WebSocket transport.** MCP 2025-03-26 deprecates the prior
  WebSocket transport; DMCP-04 does not implement it.
- **Anonymous tool calling.** `initialize` is the only unauthenticated
  method (INV-DMCP04-9). Every other method requires a bearer.

## §12 — Acceptance checklist

A conforming DMCP-04 deployment MUST satisfy:

- **DMCP04-a.** `path("mcp/", include("django_mcp.urls"))` mounts an
  `async def` view that handles `POST /` and responds with JSON-RPC
  2.0 envelopes. The view is the *only* URL the include exposes.
- **DMCP04-b.** A `manage.py mcp_server` command runs the same
  dispatcher over stdin/stdout, reads `DJANGO_MCP_KEY` from the
  environment at startup, and refuses to start without it.
- **DMCP04-c.** `MCPAPIKey` model exists with the §6.1 field set; the
  `objects.create_key()` manager method returns
  `(MCPAPIKey, plaintext_secret)` and the plaintext is reconstructible
  ONLY at that moment. A migration ships in the same commit.
- **DMCP04-d.** `manage.py mcp_key create/list/revoke/rotate/inspect`
  subcommands work per §6.4; secrets are printed ONLY on create / rotate.
- **DMCP04-e.** `initialize` is reachable WITHOUT a bearer and returns
  the `protocolVersion`/`capabilities`/`serverInfo` block per §5.4.
  The capabilities block omits any surface whose registry is empty
  (INV-DMCP04-2 / DMCP-03-disabled deployment test).
- **DMCP04-f.** `tools/list` with a valid bearer returns every tool in
  the registry, regardless of the user's per-tool permissions
  (INV-DMCP01-4 parity at the wire level). The same set is returned
  for two users with disjoint permissions.
- **DMCP04-g.** `tools/call` invoking a tool whose `auth_check` returns
  `DENY` produces a JSON-RPC `-32002 forbidden` error in a 200 body.
  `OUT_OF_SCOPE` produces `-32003`. `UNAUTHENTICATED` (anonymous user
  somehow reaching this — shouldn't happen post-§6.2) produces
  `-32001`.
- **DMCP04-h.** Revoking a key (`mcp_key revoke`) causes the next
  request authenticated with it to return HTTP `403` (INV-DMCP04-6).
- **DMCP04-i.** Every invocation — including pre-auth failures — emits
  exactly one structured log record on the `django_mcp.audit` logger
  (INV-DMCP04-5).
- **DMCP04-j.** This doc's §15 carries a dated ratification entry; the
  audit log's `outcome` enum AND the §5.5 error-code map are stable
  across the test suite; INV-DMCP04-1..10 each have at least one
  passing test.

## §13 — Files cited

- [`TODO-DMCP-00-CONCEPTS.md`](TODO-DMCP-00-CONCEPTS.md) §3, §7, §8,
  §10 — `MCPAPIKey` reservation, permission outcomes, transport mode
  enum.
- [`TODO-DMCP-01-ADMIN.md`](TODO-DMCP-01-ADMIN.md) §8 — synthesised-
  request shape reused by descriptor handlers; transport calls those
  handlers without inspection.
- [`TODO-DMCP-03-RESOURCES-PROMPTS.md`](TODO-DMCP-03-RESOURCES-PROMPTS.md)
  §5.1, §8.2, §10.3 — resource URI grammar, prompt auth model,
  subscribe-not-implemented baseline reused at the wire layer.
- `../../django_mcp/registry.py` — `MCPRegistry.tools / resources /
  prompts` are the source of `tools/list`, `resources/*/list`,
  `prompts/list`.
- `../../django_mcp/discovery.py` — `ensure_discovered()` is called by
  the HTTP view on first request (lazy single pass per INV-DMCP-5).
- `/Users/iraabbott/softoboros/backend/mcp/http_transport.py` — prior
  art for streamable-HTTP shape; consulted but not crawled.
- `/Users/iraabbott/softoboros/backend/mcp_server.py` — prior art for
  STDIO loop; consulted but not crawled.
- `/Users/iraabbott/softoboros/backend/mcp/models.py` — prior art for
  the MCPAPIKey shape; §6 records the named deviations.
- `/Users/iraabbott/softoboros/backend/mcp/oauth_urls.py` — prior art
  for an OAuth bridge that DMCP-04 explicitly does NOT implement (see
  §11).

## §14 — Unblocks

Ratifying and implementing DMCP-04 makes the package end-to-end
reachable:

- An MCP client (Claude Desktop, MCP Inspector, custom) can list and
  invoke tools, read resources, and render prompts against a running
  Django project.
- DMCP-01..03 acceptance gates that named "DMCP-04 transport" as a
  parity test target (DMCP01-i, DMCP02-i, DMCP03-d) can finally run
  end-to-end through the wire.
- A future "live updates" phase has the streamable-HTTP scaffold to
  bolt SSE onto (without re-litigating the auth or registry layer).
- A future "OAuth bridge" phase has the `MCPAPIKey` mint path to wire
  into (the bridge produces keys; the transport doesn't care how they
  got there).
- v1.0 cut: with DMCP-04 in place, the public API stabilises and the
  pre-1.0 "no backwards-compat shims" rule (CLAUDE.md) flips.

## §15 — Change log

- **2026-05-23** — Initial ratification. All sections (§0–§14) frozen.
  Signed off by: repository owner (`abbott.ira.r@gmail.com`). Ratified
  on the same day as DMCP-02, DMCP-03, and the DMCP-00 §5 grammar
  amendment. Closes the four-phase planning arc; the package is now
  spec-complete for the v1.0 surface.

  Inherits INV-DMCP-1..7, INV-DMCP01-1..5, INV-DMCP02-1..8, and
  INV-DMCP03-1..10 without modification. Introduces phase-local
  INV-DMCP04-1..10 and the MCPAPIKey model owned by §6.

  **Open question deferred (not blocking ratification):** §5.3.1 derives
  the `description` field of a tool's wire representation from the
  descriptor's `origin`. Promoting `description` to a first-class
  field on `ToolDescriptor` (DMCP-00 §3) is a small §15 amendment that
  would let derivation rules supply richer text. Tracked as a future
  amendment; current behaviour ("derive from origin") is the canonical
  fallback.

  Unblocks implementation of:
  - `django_mcp/models.py` — the `MCPAPIKey` model and its
    `objects.create_key()` manager method (and the corresponding
    migration `0001_initial.py`).
  - `django_mcp/management/commands/mcp_key.py` — the operator-facing
    key lifecycle command (create / list / revoke / rotate / inspect).
  - `django_mcp/management/commands/mcp_server.py` — the STDIO
    transport.
  - `django_mcp/dispatch.py` — the wire-agnostic JSON-RPC dispatcher
    shared by HTTP and STDIO transports.
  - `django_mcp/transport.py` and `django_mcp/urls.py` — the async
    streamable-HTTP view and its URL include.
  - `django_mcp/audit.py` — the audit-log emitter on the
    `django_mcp.audit` Python logger.
  - End-to-end DMCP-04 a..j gates; in particular DMCP04-f (per-user-
    stable `tools/list` parity) and DMCP04-g (PermissionOutcome →
    JSON-RPC error mapping at the wire layer).

  Commit: pending (carries `DMCP04:` subject prefix).
