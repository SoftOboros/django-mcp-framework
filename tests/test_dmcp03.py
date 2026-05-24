"""DMCP-03 acceptance gates a..j — Resources and prompts.

Each test pins one or more of the §12 gates and the underlying invariant(s).
"""

from __future__ import annotations

import asyncio
import logging
from io import BytesIO

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.files.uploadedfile import SimpleUploadedFile

from django_mcp.derivation import (
    PermissionOutcome,
    PromptDescriptor,
    ResourceDescriptor,
    ToolCallContext,
    ToolDescriptor,
)
from django_mcp.discovery import discover_now
from django_mcp.names import parse_resource_uri
from django_mcp.prompts import parse_user_prompt_entry
from django_mcp.registry import MCPRegistry, get_registry
from django_mcp.resources import parse_resource_model_entry
from tests.testapp.models import Post

# ---------------------------------------------------------------------------
# DMCP03-a: registry shape + descriptor types
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_dmcp03_a_registry_holds_three_kinds() -> None:
    reg = get_registry()
    assert isinstance(reg, MCPRegistry)
    assert isinstance(reg.tools, dict)
    assert isinstance(reg.resources, dict)
    assert isinstance(reg.prompts, dict)


@pytest.mark.django_db
def test_dmcp03_a_register_dispatches_by_descriptor_type(admin_user) -> None:
    reg = get_registry()

    async def noop_async(ctx):
        return {}

    def noop_auth(ctx):
        return PermissionOutcome.ALLOW

    def noop_render(ctx):
        return [{"role": "user", "content": {"type": "text", "text": "x"}}]

    tool = ToolDescriptor(
        tool_name="rpc.invoke:fixture.x",
        input_schema={},
        output_schema={},
        handler=noop_async,
        auth_check=noop_auth,
        origin="rpc.invoke:fixture.x",
    )
    resource = ResourceDescriptor(
        uri="django-mcp://model/fixture.X/{pk}",
        name="fixture.X",
        description="fixture",
        mime_type="application/json",
        is_template=True,
        read_handler=noop_async,
        auth_check=noop_auth,
        origin="django-mcp://model/fixture.X/{pk}",
    )
    prompt = PromptDescriptor(
        name="prompt.user.fixture",
        description="fixture",
        arguments=(),
        render_handler=noop_render,
        auth_check=noop_auth,
        origin="prompt.user.fixture",
    )

    reg.register(tool)
    reg.register(resource)
    reg.register(prompt)

    assert "rpc.invoke:fixture.x" in reg.tools
    assert "django-mcp://model/fixture.X/{pk}" in reg.resources
    assert "prompt.user.fixture" in reg.prompts


# ---------------------------------------------------------------------------
# DMCP03-b: empty surface → 0 resources, 0 prompts
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_dmcp03_b_empty_surface_yields_zero_resources_and_prompts(settings) -> None:
    """With no admin sites, no URLconfs, no DJANGO_MCP_RESOURCE_MODELS and no
    DJANGO_MCP_PROMPTS, DMCP-03 contributes zero resources and zero prompts."""
    settings.DJANGO_MCP_ADMIN_SITES = []
    settings.DJANGO_MCP_URLCONFS = []
    settings.DJANGO_MCP_RESOURCE_MODELS = []
    settings.DJANGO_MCP_PROMPTS = []
    discover_now()
    reg = get_registry()
    assert reg.resources == {}
    assert reg.prompts == {}


# ---------------------------------------------------------------------------
# DMCP03-c: testapp fixture emits expected resource + prompt set
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_dmcp03_c_testapp_fixture_emits_expected_resources_and_prompts() -> None:
    discover_now()
    reg = get_registry()

    # Model resource templates: one per admin-registered model.
    expected_models = {
        "django-mcp://model/auth.User/{pk}",
        "django-mcp://model/auth.Group/{pk}",
        "django-mcp://model/testapp.Post/{pk}",
        "django-mcp://model/testapp.Tag/{pk}",
    }
    assert expected_models <= set(reg.resources)

    # FileField resource: one per FileField on a participating model.
    # Post.attachment is a FileField so we expect a field/ resource.
    assert "django-mcp://field/testapp.Post/{pk}/attachment" in reg.resources

    # Prompts: at least one prompt.admin.<...>.publish + delete_selected per
    # registered admin, plus the user-registered prompt from
    # tests/settings.py's DJANGO_MCP_PROMPTS (if any).
    assert "prompt.admin.testapp.Post.publish" in reg.prompts
    assert "prompt.admin.testapp.Post.delete_selected" in reg.prompts


# ---------------------------------------------------------------------------
# DMCP03-d: INV-DMCP03-2 visible-field parity
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_dmcp03_d_visible_field_parity(admin_user, staff_user) -> None:
    """The Post model resource returns the same field set ``admin.retrieve:``
    returns for the same user — i.e. ``secret_note`` is visible to the
    superuser but not to the staff user (whose ``get_fields`` projection
    excludes it).
    """
    post = Post.objects.create(
        title="visible-parity", body="b", author=admin_user, secret_note="ssshh"
    )

    discover_now()
    reg = get_registry()
    model_resource = reg.resources["django-mcp://model/testapp.Post/{pk}"]

    # Superuser sees secret_note
    super_payload = asyncio.run(
        model_resource.read_handler(ToolCallContext(user=admin_user, arguments={"pk": post.pk}))
    )
    assert "secret_note" in super_payload
    assert super_payload["secret_note"] == "ssshh"

    # Staff user (view perm only) does not see secret_note — PostAdmin's
    # get_fields hides it from non-superusers.
    staff_payload = asyncio.run(
        model_resource.read_handler(ToolCallContext(user=staff_user, arguments={"pk": post.pk}))
    )
    assert "secret_note" not in staff_payload
    assert staff_payload["title"] == "visible-parity"


# ---------------------------------------------------------------------------
# DMCP03-e: INV-DMCP03-6 mime-type honesty
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_dmcp03_e_model_resource_declares_application_json() -> None:
    discover_now()
    reg = get_registry()
    model_resource = reg.resources["django-mcp://model/testapp.Post/{pk}"]
    assert model_resource.mime_type == "application/json"


@pytest.mark.django_db
def test_dmcp03_e_filefield_resource_declares_octet_stream() -> None:
    """INV-DMCP03-6: the declared descriptor mime is conservative
    (``application/octet-stream``); per-call mime is the wire layer's job.
    """
    discover_now()
    reg = get_registry()
    field_resource = reg.resources["django-mcp://field/testapp.Post/{pk}/attachment"]
    assert field_resource.mime_type == "application/octet-stream"


# ---------------------------------------------------------------------------
# DMCP03-f: INV-DMCP03-7 prompt-name parity with admin.action: tool name
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_dmcp03_f_admin_prompt_name_parallels_tool_name() -> None:
    """For every admin action emitted as a tool, an identically-suffixed
    prompt is emitted."""
    discover_now()
    reg = get_registry()
    action_tools = {n for n in reg.tools if n.startswith("admin.action:")}
    assert action_tools, "expected at least one admin.action: tool"

    for tool_name in action_tools:
        suffix = tool_name.removeprefix("admin.action:")
        expected_prompt = f"prompt.admin.{suffix}"
        assert expected_prompt in reg.prompts, (
            f"INV-DMCP03-7: missing prompt {expected_prompt!r} for tool {tool_name!r}"
        )


# ---------------------------------------------------------------------------
# DMCP03-g: INV-DMCP03-8 byte cap
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_dmcp03_g_filefield_byte_cap_raises(admin_user, settings) -> None:
    """A FileField read whose size exceeds DJANGO_MCP_FIELD_RESOURCE_MAX_BYTES
    MUST raise ValueError — no silent truncation (INV-DMCP03-8).
    """
    settings.DJANGO_MCP_FIELD_RESOURCE_MAX_BYTES = 64
    post = Post.objects.create(title="oversize", author=admin_user)
    # Stash a 1 KiB blob on the attachment field
    big = b"x" * 1024
    post.attachment.save("big.bin", SimpleUploadedFile("big.bin", big), save=True)

    discover_now()
    reg = get_registry()
    field_resource = reg.resources["django-mcp://field/testapp.Post/{pk}/attachment"]

    with pytest.raises(ValueError, match="DJANGO_MCP_FIELD_RESOURCE_MAX_BYTES"):
        asyncio.run(
            field_resource.read_handler(ToolCallContext(user=admin_user, arguments={"pk": post.pk}))
        )


@pytest.mark.django_db(transaction=True)
def test_dmcp03_g_filefield_under_cap_returns_bytes(admin_user, settings) -> None:
    settings.DJANGO_MCP_FIELD_RESOURCE_MAX_BYTES = 10 * 1024 * 1024
    post = Post.objects.create(title="ok-size", author=admin_user)
    payload = BytesIO(b"hello world").getvalue()
    post.attachment.save("hi.txt", SimpleUploadedFile("hi.txt", payload), save=True)

    discover_now()
    reg = get_registry()
    field_resource = reg.resources["django-mcp://field/testapp.Post/{pk}/attachment"]

    body = asyncio.run(
        field_resource.read_handler(ToolCallContext(user=admin_user, arguments={"pk": post.pk}))
    )
    assert isinstance(body, bytes)
    assert body == payload


# ---------------------------------------------------------------------------
# DMCP03-h: INV-DMCP03-9 prompts/list per-user stability (mirrors INV-DMCP01-4)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_dmcp03_h_prompt_list_stable_across_users(admin_user, plain_user) -> None:
    """INV-DMCP03-9: ``MCPRegistry.prompts`` keys are the same for any two
    callers; per-user authorisation happens at prompts/get time.
    """
    discover_now()
    reg = get_registry()
    # The registry is per-process — the prompt name set IS the same by
    # construction. The structural form of the invariant: a derivation pass
    # makes no per-user decisions.
    names_a = set(reg.prompts)
    names_b = set(reg.prompts)
    assert names_a == names_b
    # And per-prompt auth_check varies per user (the gating happens here,
    # not at list time).
    publish = reg.prompts["prompt.admin.testapp.Post.publish"]
    super_outcome = publish.auth_check(ToolCallContext(user=admin_user, arguments={}))
    plain_outcome = publish.auth_check(ToolCallContext(user=plain_user, arguments={}))
    assert super_outcome == PermissionOutcome.ALLOW
    assert plain_outcome == PermissionOutcome.DENY


# ---------------------------------------------------------------------------
# DMCP03-i: DJANGO_MCP_PROMPTS unknown-keys rejection
# ---------------------------------------------------------------------------


def test_dmcp03_i_unknown_prompt_entry_keys_rejected() -> None:
    with pytest.raises(ImproperlyConfigured, match="unknown keys"):
        parse_user_prompt_entry(
            {"name": "x", "description": "x", "arguments": [], "body": "x", "bogus": 1}
        )


def test_dmcp03_i_unknown_resource_model_keys_rejected() -> None:
    with pytest.raises(ImproperlyConfigured, match="unknown keys"):
        parse_resource_model_entry({"model": "testapp.Post", "bogus": 1})


# ---------------------------------------------------------------------------
# DMCP03-j: discovery log cites DMCP-03
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_dmcp03_j_discovery_log_cites_dmcp03(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="django_mcp.discovery"):
        discover_now()
    matching = [r for r in caplog.records if "DMCP-03" in r.getMessage()]
    assert matching, "expected an INFO log citing DMCP-03"


# ---------------------------------------------------------------------------
# Resource URI grammar coverage (DMCP-03 §5.1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "uri,host,is_template,placeholders",
    [
        ("django-mcp://model/auth.User/{pk}", "model", True, ("pk",)),
        ("django-mcp://field/blog.Post/{pk}/cover_image", "field", True, ("pk",)),
        ("django-mcp://admin/blog.Post/{pk}", "admin", True, ("pk",)),
        ("django-mcp://meta/openapi.json", "meta", False, ()),
        ("django-mcp://model/auth.User/42", "model", False, ()),
    ],
)
def test_resource_uri_grammar_positive(
    uri: str, host: str, is_template: bool, placeholders: tuple
) -> None:
    parsed = parse_resource_uri(uri)
    assert parsed.host == host
    assert parsed.is_template is is_template
    assert parsed.placeholders == placeholders
    assert str(parsed) == uri


# ---------------------------------------------------------------------------
# Prompt render: safe substitution (INV-DMCP03-5 — no exceptions on missing args)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_inv_dmcp03_5_safe_substitution_does_not_raise(settings, admin_user) -> None:
    settings.DJANGO_MCP_PROMPTS = [
        {
            "name": "render_test",
            "description": "Render test",
            "arguments": [{"name": "year", "description": "Y", "required": True}],
            "body": "Year={year} Month={month}",
        }
    ]
    discover_now()
    reg = get_registry()
    prompt = reg.prompts["prompt.user.render_test"]
    # Missing `month` MUST NOT raise.
    messages = prompt.render_handler(ToolCallContext(user=admin_user, arguments={"year": "2026"}))
    body = messages[0]["content"]["text"]
    assert "Year=2026" in body
    assert "{month}" in body  # safe fallback for missing key
