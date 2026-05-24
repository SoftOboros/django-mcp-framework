"""DMCP01-b, DMCP01-c, INV-DMCP-5, INV-DMCP-2 — discovery surface tests."""

from __future__ import annotations

import pytest
from django.contrib.admin import AdminSite

from django_mcp.admin import emit_for_admin
from django_mcp.discovery import discover_now
from django_mcp.names import parse
from django_mcp.registry import get_registry


@pytest.mark.django_db
def test_dmcp01_b_empty_adminsite_yields_no_tools() -> None:
    """DMCP01-b: a custom AdminSite with no registered models emits zero descriptors."""
    empty = AdminSite(name="empty")
    descriptors = []
    for _model, model_admin in empty._registry.items():
        descriptors.extend(emit_for_admin(model_admin))
    assert descriptors == []


@pytest.mark.django_db
def test_dmcp01_c_auth_user_and_group_emit_twelve_tools(settings) -> None:
    """DMCP01-c: with only django.contrib.auth admin registrations, the admin
    pass yields 6 tools per model × 2 models = 12 admin tools whose names match
    §5/§6.

    Note: this test only asserts on the admin-family tools; DMCP-02 may
    contribute additional view.*/model.* tools from fixtures, which is
    intentional and out of DMCP01-c's scope.
    """
    from django.contrib.admin import site as default_site

    settings.DJANGO_MCP_ADMIN_SITES = ("django.contrib.admin.site",)

    # Temporarily yank out non-auth models so we have exactly User + Group.
    from django.contrib.auth.models import Group, User

    saved_registry = dict(default_site._registry)
    default_site._registry = {
        model: ma for model, ma in saved_registry.items() if model in (User, Group)
    }
    try:
        discover_now()
    finally:
        default_site._registry = saved_registry

    reg = get_registry()
    expected_admin = {
        "admin.list:auth.Group",
        "admin.list:auth.User",
        "admin.retrieve:auth.Group",
        "admin.retrieve:auth.User",
        "admin.create:auth.Group",
        "admin.create:auth.User",
        "admin.update:auth.Group",
        "admin.update:auth.User",
        "admin.delete:auth.Group",
        "admin.delete:auth.User",
        "admin.action:auth.Group.delete_selected",
        "admin.action:auth.User.delete_selected",
    }
    actual_admin = {d.tool_name for d in reg if d.tool_name.startswith("admin.")}
    assert actual_admin == expected_admin


@pytest.mark.django_db
def test_dmcp01_i_every_emitted_name_parses() -> None:
    """DMCP01-i: every name produced by a rule MUST parse against §5."""
    discover_now()
    reg = get_registry()
    assert len(reg) > 0
    for d in reg:
        parsed = parse(d.tool_name)
        assert str(parsed) == d.tool_name
        # INV-DMCP-2: origin matches tool_name (descriptor came from a rule).
        assert d.origin == d.tool_name


@pytest.mark.django_db
def test_inv_dmcp_5_discovery_is_single_pass() -> None:
    """INV-DMCP-5: second discover_now is a no-op on a frozen registry."""
    n1 = discover_now()
    assert n1 > 0
    n2 = discover_now()
    assert n2 == 0
    n3 = discover_now()
    assert n3 == 0
