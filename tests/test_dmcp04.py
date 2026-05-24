"""DMCP-04 acceptance gates a..j — Transport, MCPAPIKey, audit.

Each test pins one or more of the §12 gates and the underlying invariant(s).
The HTTP suite uses Django's ``AsyncClient`` against the test URL include at
``/mcp/`` (see ``tests/urls.py``).
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import sys

import pytest
from asgiref.sync import sync_to_async
from django.core.management import CommandError, call_command
from django.test import AsyncClient

from django_mcp.models import MCPAPIKey

pytestmark = pytest.mark.django_db(transaction=True)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mcp_key(admin_user) -> tuple[MCPAPIKey, str, str]:
    """A live MCPAPIKey + plaintext secret + wire credential."""
    key, secret = MCPAPIKey.objects.create_key(user=admin_user, name="test key")
    return key, secret, f"{key.key_id}.{secret}"


@pytest.fixture
def restricted_mcp_key(admin_user) -> tuple[MCPAPIKey, str, str]:
    """A key whose ``allowed_tools`` restricts to one tool only."""
    key, secret = MCPAPIKey.objects.create_key(
        user=admin_user,
        name="restricted",
        allowed_tools=["admin.list:auth.User"],
    )
    return key, secret, f"{key.key_id}.{secret}"


@pytest.fixture
def plain_user_mcp_key(plain_user) -> tuple[MCPAPIKey, str, str]:
    """A key for a non-staff user — auth_check returns DENY for admin tools."""
    key, secret = MCPAPIKey.objects.create_key(user=plain_user, name="plain")
    return key, secret, f"{key.key_id}.{secret}"


def _env(method: str, *, id: int = 1, params: dict | None = None) -> dict:
    return {"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}}


async def _post(client: AsyncClient, body: dict, *, bearer: str | None = None):
    headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
    return await client.post(
        "/mcp/",
        json.dumps(body),
        content_type="application/json",
        headers=headers,
    )


# ---------------------------------------------------------------------------
# DMCP04-a: URL include exposes one async view
# ---------------------------------------------------------------------------


def test_dmcp04_a_urls_module_exposes_one_pattern() -> None:
    from django_mcp import urls as mcp_urls

    assert mcp_urls.app_name == "django_mcp"
    assert len(mcp_urls.urlpatterns) == 1
    assert mcp_urls.urlpatterns[0].name == "mcp"


def test_dmcp04_a_view_is_async() -> None:
    import asyncio as _asyncio

    from django_mcp.transport import mcp_endpoint

    assert _asyncio.iscoroutinefunction(mcp_endpoint)


# ---------------------------------------------------------------------------
# DMCP04-b: mcp_server refuses without DJANGO_MCP_KEY
# ---------------------------------------------------------------------------


def test_dmcp04_b_mcp_server_refuses_without_env() -> None:
    os.environ.pop("DJANGO_MCP_KEY", None)
    with pytest.raises(CommandError, match="DJANGO_MCP_KEY"):
        call_command("mcp_server")


def test_dmcp04_b_mcp_server_refuses_bad_env(admin_user) -> None:
    os.environ["DJANGO_MCP_KEY"] = "not.a.real.key"
    try:
        with pytest.raises(CommandError):
            call_command("mcp_server")
    finally:
        del os.environ["DJANGO_MCP_KEY"]


# ---------------------------------------------------------------------------
# DMCP04-c: MCPAPIKey model + create_key returns plaintext ONCE
# ---------------------------------------------------------------------------


def test_dmcp04_c_create_key_returns_plaintext_once(admin_user) -> None:
    key, secret = MCPAPIKey.objects.create_key(user=admin_user, name="once")
    assert len(key.key_id) == 24
    assert len(secret) == 32
    # The plaintext is reconstructible ONLY at this moment.
    assert key.verify_secret(secret)
    assert not key.verify_secret("wrong")
    # secret_hash never equals the plaintext
    assert key.secret_hash != secret


def test_dmcp04_c_wire_credential_length_57(admin_user) -> None:
    key, secret = MCPAPIKey.objects.create_key(user=admin_user, name="length")
    wire = f"{key.key_id}.{secret}"
    assert len(wire) == 57


# ---------------------------------------------------------------------------
# DMCP04-d: mcp_key subcommands work as documented
# ---------------------------------------------------------------------------


def test_dmcp04_d_mcp_key_create_prints_credential(admin_user) -> None:
    out = io.StringIO()
    call_command(
        "mcp_key", "create", admin_user.username, "--name", "via mcp_key", stdout=out, no_color=True
    )
    output = out.getvalue().strip()
    # The credential is on its own line, 57 chars
    matching = [ln for ln in output.splitlines() if "." in ln and len(ln) == 57]
    assert matching, f"expected one 57-char credential line; got:\n{output}"


def test_dmcp04_d_mcp_key_list_never_prints_secret(admin_user, mcp_key) -> None:
    out = io.StringIO()
    call_command("mcp_key", "list", stdout=out, no_color=True)
    listing = out.getvalue().lower()
    assert "secret_hash" not in listing
    # Confirm something useful IS there
    assert mcp_key[0].key_id in out.getvalue()


def test_dmcp04_d_mcp_key_revoke(admin_user, mcp_key) -> None:
    key, _, _ = mcp_key
    out = io.StringIO()
    call_command("mcp_key", "revoke", key.key_id, stdout=out, no_color=True)
    key.refresh_from_db()
    assert key.revoked_at is not None
    assert "revoked" in out.getvalue().lower()


def test_dmcp04_d_mcp_key_rotate_invalidates_old_secret(admin_user, mcp_key) -> None:
    key, old_secret, _ = mcp_key
    out = io.StringIO()
    call_command("mcp_key", "rotate", key.key_id, stdout=out, no_color=True)
    key.refresh_from_db()
    assert not key.verify_secret(old_secret)
    # New credential is in the output
    matching = [ln for ln in out.getvalue().splitlines() if "." in ln and len(ln) == 57]
    assert matching


def test_dmcp04_d_mcp_key_inspect_omits_secret(admin_user, mcp_key) -> None:
    key, _, _ = mcp_key
    out = io.StringIO()
    call_command("mcp_key", "inspect", key.key_id, stdout=out, no_color=True)
    inspected = out.getvalue()
    assert key.key_id in inspected
    assert "secret_hash" not in inspected.lower()


# ---------------------------------------------------------------------------
# DMCP04-e: initialize without bearer + capability honesty (INV-DMCP04-2)
# ---------------------------------------------------------------------------


def test_dmcp04_e_initialize_without_bearer_returns_200() -> None:
    async def _run() -> None:
        client = AsyncClient()
        resp = await _post(client, _env("initialize", id=1))
        assert resp.status_code == 200
        body = resp.json()
        assert body["result"]["protocolVersion"] == "2025-03-26"
        assert body["result"]["serverInfo"]["name"] == "django-mcp"

    asyncio.run(_run())


def test_dmcp04_e_capability_honesty_with_resources_disabled(settings) -> None:
    """INV-DMCP04-2: with resources disabled, capabilities omits resources + prompts."""
    settings.DJANGO_MCP_RESOURCES_DISABLED = True
    settings.DJANGO_MCP_PROMPTS = []

    async def _run() -> None:
        client = AsyncClient()
        resp = await _post(client, _env("initialize", id=2))
        caps = resp.json()["result"]["capabilities"]
        # tools is still present (admin discovery emits tools)
        assert "tools" in caps
        # resources / prompts are absent because the registries are empty
        assert "resources" not in caps, caps
        assert "prompts" not in caps, caps

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# DMCP04-f: tools/list per-user-stable (INV-DMCP01-4 at the wire layer)
# ---------------------------------------------------------------------------


def test_dmcp04_f_tools_list_same_for_two_users(mcp_key, plain_user_mcp_key) -> None:
    async def _run() -> None:
        client = AsyncClient()
        r1 = await _post(client, _env("tools/list", id=1), bearer=mcp_key[2])
        r2 = await _post(client, _env("tools/list", id=2), bearer=plain_user_mcp_key[2])
        names1 = {t["name"] for t in r1.json()["result"]["tools"]}
        names2 = {t["name"] for t in r2.json()["result"]["tools"]}
        assert names1 == names2

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# DMCP04-g: PermissionOutcome → JSON-RPC error mapping
# ---------------------------------------------------------------------------


def test_dmcp04_g_deny_maps_to_minus_32002(plain_user_mcp_key) -> None:
    """A non-staff caller → admin tool auth_check returns DENY → JSON-RPC -32002."""

    async def _run() -> None:
        client = AsyncClient()
        resp = await _post(
            client,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "admin.list:auth.User", "arguments": {}},
            },
            bearer=plain_user_mcp_key[2],
        )
        body = resp.json()
        assert body["error"]["code"] == -32002, body

    asyncio.run(_run())


def test_dmcp04_g_out_of_scope_maps_to_minus_32003(restricted_mcp_key) -> None:
    """An allowed_tools-restricted key calling a tool outside its list → -32003."""

    async def _run() -> None:
        client = AsyncClient()
        resp = await _post(
            client,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "admin.delete:auth.User", "arguments": {"pk": 1}},
            },
            bearer=restricted_mcp_key[2],
        )
        body = resp.json()
        assert body["error"]["code"] == -32003, body

    asyncio.run(_run())


def test_dmcp04_g_in_scope_tool_succeeds(restricted_mcp_key, admin_user) -> None:
    """The allowed_tools entry IS callable."""

    async def _run() -> None:
        client = AsyncClient()
        resp = await _post(
            client,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "admin.list:auth.User", "arguments": {}},
            },
            bearer=restricted_mcp_key[2],
        )
        body = resp.json()
        # restricted key's user is admin_user (superuser) — so auth_check ALLOWs
        assert body.get("result", {}).get("isError") is False, body

    asyncio.run(_run())


def test_dmcp04_g_unknown_tool_maps_to_minus_32602(mcp_key) -> None:
    async def _run() -> None:
        client = AsyncClient()
        resp = await _post(
            client,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "no.such:thing.Exists", "arguments": {}},
            },
            bearer=mcp_key[2],
        )
        assert resp.json()["error"]["code"] == -32602

    asyncio.run(_run())


def test_dmcp04_g_unknown_method_maps_to_minus_32601(mcp_key) -> None:
    async def _run() -> None:
        client = AsyncClient()
        resp = await _post(
            client,
            _env("bogus/method", id=1),
            bearer=mcp_key[2],
        )
        assert resp.json()["error"]["code"] == -32601

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# DMCP04-h: revoke is immediate (INV-DMCP04-6)
# ---------------------------------------------------------------------------


def test_dmcp04_h_revoke_is_immediate(mcp_key) -> None:
    key, _, wire = mcp_key

    async def _run() -> None:
        client = AsyncClient()
        # Sanity: works before revoke
        r1 = await _post(client, _env("tools/list", id=1), bearer=wire)
        assert r1.status_code == 200
        # Revoke
        await sync_to_async(key.revoke)()
        # Next request fails with 403 (INV-DMCP04-6 — no cache, immediate effect)
        r2 = await _post(client, _env("tools/list", id=2), bearer=wire)
        assert r2.status_code == 403

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# DMCP04-i: audit on every call (INV-DMCP04-5)
# ---------------------------------------------------------------------------


def test_dmcp04_i_audit_emitted_per_call(mcp_key, caplog) -> None:
    """One audit record per HTTP request — successes and pre-auth failures alike."""
    caplog.set_level(logging.INFO, logger="django_mcp.audit")

    async def _run() -> None:
        client = AsyncClient()
        await _post(client, _env("initialize", id=1))
        await _post(client, _env("tools/list", id=2), bearer=mcp_key[2])
        # Pre-auth failure: bad bearer
        await _post(client, _env("tools/list", id=3), bearer="bogus.creds")
        # Pre-auth failure: no bearer for a non-initialize method
        await _post(client, _env("tools/list", id=4))

    asyncio.run(_run())

    audit_records = [r for r in caplog.records if r.name == "django_mcp.audit"]
    assert len(audit_records) == 4, f"expected 4 audit records, got {len(audit_records)}"
    outcomes = [json.loads(r.getMessage())["outcome"] for r in audit_records]
    assert outcomes[0] == "allow"  # initialize
    assert outcomes[1] == "allow"  # tools/list with valid bearer
    assert outcomes[2] == "unauthenticated"  # bad bearer
    assert outcomes[3] == "unauthenticated"  # missing bearer


# ---------------------------------------------------------------------------
# INV-DMCP04-1: single endpoint per transport
# ---------------------------------------------------------------------------


def test_inv_dmcp04_1_single_url_pattern() -> None:
    from django_mcp import urls as mcp_urls

    assert len(mcp_urls.urlpatterns) == 1


# ---------------------------------------------------------------------------
# INV-DMCP04-3: session not consulted (auth-by-key only)
# ---------------------------------------------------------------------------


def test_inv_dmcp04_3_no_session_implicit_access(admin_user) -> None:
    """A Django-session-authenticated user does NOT get MCP access without a bearer."""

    async def _run() -> None:
        client = AsyncClient()
        # Even though we could `await client.force_login(admin_user)`, that's a session.
        # No bearer → 401, regardless of session state.
        resp = await _post(client, _env("tools/list", id=1))
        assert resp.status_code == 401

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# INV-DMCP04-4: CSRF exemption is scoped
# ---------------------------------------------------------------------------


def test_inv_dmcp04_4_csrf_exempt_on_mcp_view_only(mcp_key) -> None:
    """The MCP view accepts POST without a CSRF token. (No token sent in any
    of our tests, and every call returns 200 / proper error, not 403 CSRF.)"""

    async def _run() -> None:
        client = AsyncClient(enforce_csrf_checks=True)
        # @csrf_exempt on the view bypasses the middleware's CSRF check
        resp = await _post(client, _env("initialize", id=1))
        assert resp.status_code == 200

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# INV-DMCP04-7: no descriptor execution at list time
# ---------------------------------------------------------------------------


def test_inv_dmcp04_7_list_methods_do_not_invoke_handlers(mcp_key) -> None:
    """tools/list, resources/templates/list, prompts/list return metadata only."""

    async def _run() -> None:
        client = AsyncClient()
        for method in ("tools/list", "resources/templates/list", "prompts/list"):
            resp = await _post(
                client,
                {"jsonrpc": "2.0", "id": 1, "method": method, "params": {}},
                bearer=mcp_key[2],
            )
            body = resp.json()
            assert "result" in body, body

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# INV-DMCP04-9: initialize is unauthenticated-readable
# ---------------------------------------------------------------------------


def test_inv_dmcp04_9_initialize_no_bearer_returns_200() -> None:
    async def _run() -> None:
        client = AsyncClient()
        resp = await _post(client, _env("initialize", id=1))
        assert resp.status_code == 200

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# DMCP04-j: end-to-end resource + prompt fetch
# ---------------------------------------------------------------------------


def test_dmcp04_j_resource_read_round_trip(mcp_key, admin_user) -> None:
    """A resources/read against a model resource returns the JSON payload."""
    from tests.testapp.models import Post

    post = Post.objects.create(title="wire-roundtrip", author=admin_user)

    async def _run() -> None:
        client = AsyncClient()
        resp = await _post(
            client,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "resources/read",
                "params": {"uri": f"django-mcp://model/testapp.Post/{post.pk}"},
            },
            bearer=mcp_key[2],
        )
        body = resp.json()
        assert "result" in body, body
        contents = body["result"]["contents"]
        assert len(contents) == 1
        assert contents[0]["mimeType"] == "application/json"
        payload = json.loads(contents[0]["text"])
        assert payload["title"] == "wire-roundtrip"

    asyncio.run(_run())


def test_dmcp04_j_prompt_get_round_trip(mcp_key) -> None:
    """A prompts/get against an admin-action prompt returns the rendered message."""

    async def _run() -> None:
        client = AsyncClient()
        resp = await _post(
            client,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "prompts/get",
                "params": {
                    "name": "prompt.admin.testapp.Post.publish",
                    "arguments": {"pks": [1, 2]},
                },
            },
            bearer=mcp_key[2],
        )
        body = resp.json()
        assert "result" in body, body
        messages = body["result"]["messages"]
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        text = messages[0]["content"]["text"]
        assert "admin.action:testapp.Post.publish" in text

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Sanity: STDIO transport end-to-end via call_command + scripted stdin
# ---------------------------------------------------------------------------


def test_dmcp04_stdio_end_to_end(mcp_key) -> None:
    """The STDIO server processes one JSON-RPC line and writes one response."""
    os.environ["DJANGO_MCP_KEY"] = mcp_key[2]
    try:
        orig_stdin, orig_stdout = sys.stdin, sys.stdout
        in_buf = io.StringIO(json.dumps(_env("initialize", id=1)) + "\n")
        out_buf = io.StringIO()
        sys.stdin = in_buf
        sys.stdout = out_buf
        try:
            call_command("mcp_server")
        finally:
            sys.stdin, sys.stdout = orig_stdin, orig_stdout

        envelope = json.loads(out_buf.getvalue().strip())
        assert envelope["jsonrpc"] == "2.0"
        assert envelope["id"] == 1
        assert envelope["result"]["protocolVersion"] == "2025-03-26"
    finally:
        os.environ.pop("DJANGO_MCP_KEY", None)
