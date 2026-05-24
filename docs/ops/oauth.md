# OAuth integration — OAuth-mints-MCPAPIKey pattern

**Status:** operational runbook (informative). The binding architecture
this builds on is DMCP-04 §6 (`MCPAPIKey`) and §10.1 (auth-credential
separation). DMCP-04 §11 explicitly defers OAuth-token-as-bearer to a
future phase; the pattern below is the v1.0-ready alternative.

## The pattern in one paragraph

A Django project that wants OAuth-authenticated MCP access runs the OAuth
flow exactly the way it would for any other web flow. On successful
callback (where Django has resolved the OAuth identity to a
`User` instance), the project mints an `MCPAPIKey` for that user and
returns the wire credential `<key_id>.<secret>` to the caller. The
caller uses that credential on subsequent MCP requests via
`Authorization: Bearer <credential>`. The MCP transport sees a normal
`MCPAPIKey` and doesn't know — or care — that an OAuth flow produced it.

The MCP transport's authentication boundary (DMCP-04 §6.2) is unchanged.
No package code changes are needed. Operators wire the OAuth flow once,
in their own project's `urls.py`.

## Why this works without changing django-mcp

INV-DMCP04-3 (auth-by-key, not session) says the MCP transport
authenticates by `MCPAPIKey` only. It doesn't constrain how the
`MCPAPIKey` got into the database. An OAuth callback view that creates
keys is a credential *issuer*, the MCP transport is a credential
*verifier*, and the two communicate through the `MCPAPIKey` table.

The clean separation also means revoking an OAuth session and revoking
the MCP key are independent operations — see [Lifecycle](#lifecycle)
below.

## Pattern A: provision-on-callback (long-lived MCP key)

The OAuth callback view mints one MCP key per user-device combination.
Suitable for desktop MCP clients (Claude Desktop, MCP Inspector) where
the user explicitly grants access and is willing to keep a key around.

```python
# myproject/oauth_views.py — example using django-allauth-style hooks
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views.decorators.http import require_POST
from django.views.generic import View

from django_mcp.models import MCPAPIKey


@method_decorator(login_required, name="dispatch")
@method_decorator(require_POST, name="dispatch")
class IssueMcpKeyView(View):
    """POST /oauth/mcp-key/?name=<label>

    Returns a freshly-minted MCP wire credential for the logged-in
    OAuth user. The plaintext secret appears in the response body ONCE
    — the client MUST store it; the server cannot recover it.
    """

    def post(self, request):
        label = request.POST.get("name") or "oauth-issued"
        # Optional: scope-to-tool mapping (see "Scope mapping" below).
        allowed_tools = _scopes_to_tools(request.user)

        key, secret = MCPAPIKey.objects.create_key(
            user=request.user,
            name=label,
            allowed_tools=allowed_tools,
            expires_at=None,  # long-lived; user can revoke explicitly
        )
        return JsonResponse({
            "key_id": key.key_id,
            "wire_credential": f"{key.key_id}.{secret}",
            "allowed_tools": list(key.allowed_tools),
            "expires_at": None,
        })
```

Add to `urls.py`:

```python
urlpatterns = [
    ...,
    path("oauth/mcp-key/", IssueMcpKeyView.as_view(), name="issue-mcp-key"),
    path("mcp/", include("django_mcp.urls")),
]
```

Behavioural notes:

- The `login_required` decorator is doing the load-bearing work — by the
  time `post()` runs, `request.user` is the OAuth-resolved user. The
  underlying OAuth library (django-allauth, python-social-auth,
  django-oauth-toolkit, etc.) populates this.
- The `JsonResponse` body returns the wire credential ONCE. The client
  is responsible for storing it; the server cannot reconstruct it after
  this response is gone.
- No CSRF token is sent in the example because the client is calling
  this from a programmatic context. For browser-driven flows, drop the
  `@require_POST` and add CSRF protection per Django's normal rules —
  this view is NOT in the MCP CSRF-exempt scope (INV-DMCP04-4 limits
  the exemption to the MCP mount point only).

## Pattern B: provision-on-demand (short-lived MCP key)

A short-lived MCP key whose `expires_at` matches the OAuth access
token's expiry. Suitable for confidential-client flows where the OAuth
session itself is the lifetime boundary.

```python
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone

from django_mcp.models import MCPAPIKey


@login_required
def issue_short_lived_mcp_key(request):
    # The OAuth library exposes the access token's expiry on the session
    # or on a separate model — adapt to your provider.
    oauth_expires_at = request.user.socialaccount_set.first().extra_data.get(
        "expires_at"
    )
    expires_at = (
        timezone.now() + timedelta(seconds=int(oauth_expires_at))
        if oauth_expires_at
        else timezone.now() + timedelta(hours=1)
    )

    key, secret = MCPAPIKey.objects.create_key(
        user=request.user,
        name=f"oauth-session-{timezone.now().isoformat()}",
        expires_at=expires_at,
    )
    return JsonResponse({
        "wire_credential": f"{key.key_id}.{secret}",
        "expires_at": expires_at.isoformat(),
    })
```

The MCP transport per DMCP-04 §6.2 step (4) checks
`expires_at <= now` on every request and returns `403 Forbidden`
when expired. No additional package code is needed to honour the
expiry.

## Scope mapping (OAuth scopes → `allowed_tools`)

OAuth scopes are strings carried in the access token (e.g.
`"posts.read posts.write users.read"`). `MCPAPIKey.allowed_tools`
is an exact-match list of tool/resource/prompt names per DMCP-04
§10.5.

A typical mapping function:

```python
# A project-owned mapping table — operators decide which scopes admit
# which tools. NOT shipped by django-mcp.
SCOPE_TO_TOOLS: dict[str, list[str]] = {
    "posts.read": [
        "admin.list:blog.Post",
        "admin.retrieve:blog.Post",
        "model.search:blog.Post",
        "django-mcp://model/blog.Post/{pk}",
    ],
    "posts.write": [
        "admin.create:blog.Post",
        "admin.update:blog.Post",
        "admin.delete:blog.Post",
        "admin.action:blog.Post.publish",
        "prompt.admin.blog.Post.publish",
    ],
    "users.read": [
        "admin.list:auth.User",
        "admin.retrieve:auth.User",
        "django-mcp://model/auth.User/{pk}",
    ],
}


def _scopes_to_tools(user) -> list[str]:
    """Map the OAuth scopes on `user`'s session to MCPAPIKey.allowed_tools."""
    scopes_string = user.socialaccount_set.first().extra_data.get("scope", "")
    scopes = scopes_string.split()
    allowed: set[str] = set()
    for scope in scopes:
        allowed.update(SCOPE_TO_TOOLS.get(scope, []))
    # An empty list in MCPAPIKey.allowed_tools means "any tool the user
    # can see via descriptor auth_check"; a non-empty list is exact-match
    # enforcement (DMCP-04 §10.5). Returning an empty list when scopes
    # produced no matches would PROMOTE the key beyond what the scopes
    # authorise — return [""] (a sentinel that matches nothing) instead.
    return sorted(allowed) or ["__no_scope_match__"]
```

Two operational gotchas the mapping function MUST handle:

1. **Empty `allowed_tools` means UNRESTRICTED.** Per DMCP-04 §10.5 an
   empty list grants every tool the user's `auth_check` allows. If the
   OAuth scope set produces zero matches and you return `[]`, you have
   accidentally promoted the key. Use a sentinel (`"__no_scope_match__"`)
   or refuse to issue the key.

2. **Resource URI templates are matched verbatim per §10.5.** The
   allowlist entry for a model resource is the full template string
   (`"django-mcp://model/blog.Post/{pk}"`), not a concrete URI. The
   transport substitutes the placeholder at call time.

## Lifecycle

| Event | What to do |
|-------|------------|
| OAuth refresh-token rotation | No-op for MCP — the MCP key has its own lifecycle. Optionally call `MCPAPIKey.objects.create_key()` again with updated `expires_at`. |
| OAuth session logout | Call `key.revoke()` on every MCP key tied to the OAuth identity. INV-DMCP04-6 (revoke is immediate) makes this take effect on the next request. |
| OAuth scope downgrade | Issue a new key with the narrower `allowed_tools`; revoke the old key. Avoid in-place editing of `allowed_tools` — the audit log entry's `key_id` becomes ambiguous about which scope set was in force. |
| User account deactivation | The Django user's `is_active=False` causes DMCP-04 §6.2 step (6) to reject the key with `403`. No additional cleanup needed. |
| Mass revocation (incident response) | `MCPAPIKey.objects.filter(user__email__endswith="@evil-tenant.example").update(revoked_at=timezone.now())`. Each subsequent request hits the DB and sees the revocation (INV-DMCP04-6). |

## Audit considerations

DMCP-04 §8 emits one audit record per MCP call. The `key_id` field on
the audit record is the `MCPAPIKey.key_id`, NOT the OAuth identity.
Operators tracing an MCP call back to an OAuth identity should:

- Pre-populate `MCPAPIKey.name` with the OAuth identity at issue time
  (e.g. `"oauth:alice@example.test:laptop-2026"`); the `name` is
  retrievable from the DB at audit-investigation time.
- Tag the SIEM record with a join key (e.g. the user_id, which IS in
  the audit record) so MCP activity correlates to OAuth session logs
  in the same query.

Per **INV-DMCP04-3 (auth-by-key, not session)** the audit record does
NOT carry OAuth-specific fields (token jti, scope, etc.). If you need
those, log them at the OAuth callback view alongside the
`create_key()` call; the two log streams correlate by `key_id`.

## What this pattern does NOT support

- **OAuth token as the wire bearer.** A client cannot present a raw
  OAuth access token in the `Authorization: Bearer` header and have
  the MCP transport validate it. The transport only accepts
  `<key_id>.<secret>` per DMCP-04 §6.2. This is the "future DMCP-05
  candidate" referenced in DMCP-04 §11.
- **Browser-based MCP without a key.** A client running in a browser
  context would need to expose the wire credential to the MCP HTTP
  call somehow. This package does not ship a browser-friendly token
  helper. If you build one, treat it as a separate
  project-owned concern.
- **Per-tool OAuth consent flows.** The OAuth flow runs once at
  credential-issue time; the resulting MCP key is then used without
  further user prompts. Step-up authentication for a sensitive tool
  call is out of scope.

## Migration to OAuth-token-as-bearer (future)

If a future phase ratifies an `OAuthBearerResolver` per DMCP-04 §10.1's
unblocks list, the migration shape is:

1. Add `DJANGO_MCP_BEARER_RESOLVER = "myproject.oauth.MyResolver"`
   setting in the consuming project.
2. The resolver returns the same `(user, key_id_for_audit,
   allowed_tools)` tuple `_resolve_key` returns today; the rest of the
   transport is unchanged.
3. The OAuth-mints-MCPAPIKey pattern continues to work as a parallel
   path — the resolver chain considers `MCPAPIKey` first, then falls
   through to OAuth-token validation if the credential doesn't parse
   as `<key_id>.<secret>`.

The shape is deliberately additive — there's no need to remove the
MCPAPIKey path. Some clients will always prefer issued credentials over
token-passthrough; both flavours can coexist.

## Worked example: end-to-end smoke

```bash
# 1. Operator: register an OAuth provider with django-allauth (provider
#    docs apply; the package is unopinionated about which library).

# 2. User: complete the OAuth flow in a browser → ends up logged into
#    Django via session.

# 3. User's MCP client: hit the IssueMcpKeyView endpoint with the
#    session cookie attached, get back the wire credential.

curl -sX POST -b "sessionid=<the cookie>" \
     -d "name=alice-laptop" \
     https://myproject.example/oauth/mcp-key/

# Response:
# {"key_id": "...", "wire_credential": "<24>.<32>", "allowed_tools": [...], "expires_at": null}

# 4. MCP client: configure with the wire credential. All subsequent
#    MCP calls use it directly.

curl -sX POST \
     -H "Authorization: Bearer <key_id>.<secret>" \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
     https://myproject.example/mcp/

# 5. User logs out of the OAuth session → the project's logout view
#    revokes the MCP key.
```

The MCP layer never touches OAuth machinery directly. The OAuth layer
never touches MCP wire protocol directly. The `MCPAPIKey` row is the
contract between them.
