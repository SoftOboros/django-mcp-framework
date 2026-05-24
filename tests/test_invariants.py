"""INV-DMCP01-1 / -2 / -3 / -4 / -5 — DMCP-01 phase invariants."""

from __future__ import annotations

import asyncio
import logging

import pytest
from django.contrib.admin import AdminSite, ModelAdmin

from django_mcp.admin import emit_for_admin
from django_mcp.derivation import PermissionOutcome, ToolCallContext
from tests.testapp.models import Post

# ---------------------------------------------------------------------------
# INV-DMCP01-1 — search_fields drift mutates the input schema
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_inv_dmcp01_1_search_fields_drift_changes_list_schema() -> None:
    """DMCP01-d: mutating ``ModelAdmin.get_search_fields`` between discovery
    passes changes the ``admin.list:`` tool's input schema.

    The presence of ``q`` in input properties is the observable signal — when
    ``search_fields`` is empty, ``q`` MUST NOT appear; when non-empty, it
    MUST appear.
    """
    site = AdminSite(name="drift")

    class WithSearch(ModelAdmin):
        search_fields = ("title",)

    class WithoutSearch(ModelAdmin):
        search_fields = ()

    with_search = next(
        d for d in emit_for_admin(WithSearch(Post, site)) if d.tool_name.startswith("admin.list:")
    )
    without_search = next(
        d
        for d in emit_for_admin(WithoutSearch(Post, site))
        if d.tool_name.startswith("admin.list:")
    )

    assert "q" in with_search.input_schema["properties"]
    assert "q" not in without_search.input_schema["properties"]


# ---------------------------------------------------------------------------
# INV-DMCP01-2 — action permission parity
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_inv_dmcp01_2_action_permission_parity(admin_user, staff_user) -> None:
    """DMCP01-e: an action decorated with ``@admin.action(permissions=[...])``
    accepts/denies via MCP exactly as it does via the admin POST handler.

    Setup: ``publish`` declares ``permissions=["change"]``. ``admin_user`` is
    a superuser (has change_post implicitly); ``staff_user`` lacks change.
    """
    from tests.testapp.admin import PostAdmin

    site = AdminSite(name="action")
    descs = {d.tool_name: d for d in emit_for_admin(PostAdmin(Post, site))}
    publish_desc = descs["admin.action:testapp.Post.publish"]

    # The auth_check is the canonical surface for INV-DMCP-3 parity.
    assert (
        publish_desc.auth_check(ToolCallContext(user=admin_user, arguments={}))
        == PermissionOutcome.ALLOW
    )
    assert (
        publish_desc.auth_check(ToolCallContext(user=staff_user, arguments={}))
        == PermissionOutcome.DENY
    )


# ---------------------------------------------------------------------------
# INV-DMCP01-3 — visible-field parity
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_inv_dmcp01_3_visible_field_parity(admin_user, staff_user) -> None:
    """DMCP01-f: a field hidden by per-user ``get_fields`` is NOT returned
    in the serialised payload; a field shown is.

    ``PostAdmin.get_fields`` returns ``secret_note`` only for superusers.
    """
    from tests.testapp.admin import PostAdmin

    post = Post.objects.create(title="Hello", body="b", author=admin_user, secret_note="ssshh")

    site = AdminSite(name="visible")
    descs = {d.tool_name: d for d in emit_for_admin(PostAdmin(Post, site))}
    retrieve = descs["admin.retrieve:testapp.Post"]

    super_payload = asyncio.run(
        retrieve.handler(ToolCallContext(user=admin_user, arguments={"pk": post.pk}))
    )["object"]
    assert "secret_note" in super_payload
    assert super_payload["secret_note"] == "ssshh"

    # staff_user has view permission but not the superuser flag → secret_note
    # is excluded by the get_fields override.
    staff_payload = asyncio.run(
        retrieve.handler(ToolCallContext(user=staff_user, arguments={"pk": post.pk}))
    )["object"]
    assert "secret_note" not in staff_payload
    assert staff_payload["title"] == "Hello"


# ---------------------------------------------------------------------------
# INV-DMCP01-4 — per-user tool-list stability
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_inv_dmcp01_4_tool_list_does_not_depend_on_user(admin_user, plain_user) -> None:
    """DMCP01-g: ``tools/list`` returns the same set of names for users with
    disjoint permissions. Only per-tool invoke outcomes differ.

    Structural form of the invariant: ``emit_for_admin`` takes no user, so the
    name set is by construction user-independent. We additionally assert it
    here in the spirit of the gate.
    """
    from tests.testapp.admin import PostAdmin

    site = AdminSite(name="stability")
    names = {d.tool_name for d in emit_for_admin(PostAdmin(Post, site))}

    # Re-derivation must produce the same names (the rule is pure of user).
    names_again = {d.tool_name for d in emit_for_admin(PostAdmin(Post, site))}
    assert names == names_again

    # And auth_check returning ALLOW vs DENY does NOT mutate the descriptor's
    # tool_name — it only changes the per-call decision.
    descs = {d.tool_name: d for d in emit_for_admin(PostAdmin(Post, site))}
    list_desc = descs["admin.list:testapp.Post"]
    super_outcome = list_desc.auth_check(ToolCallContext(user=admin_user, arguments={}))
    plain_outcome = list_desc.auth_check(ToolCallContext(user=plain_user, arguments={}))
    assert super_outcome == PermissionOutcome.ALLOW
    assert plain_outcome == PermissionOutcome.DENY
    # The descriptor's tool_name is the same object both times.
    assert list_desc.tool_name == "admin.list:testapp.Post"


# ---------------------------------------------------------------------------
# INV-DMCP01-5 — inlines do NOT surface as separate tools
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_inv_dmcp01_5_inlines_do_not_emit_tools_for_inline_targets() -> None:
    """DMCP01-h: ``PostAdmin`` declares a ``TabularInline`` whose model is the
    Post.tags through-table; that inline MUST NOT cause a Post-tags tool to
    appear (the inline's target only surfaces if it is independently
    registered in admin.site).
    """
    from tests.testapp.admin import PostAdmin

    site = AdminSite(name="inline")
    site.register(Post, PostAdmin)  # Tag intentionally NOT registered here.

    descriptors: list = []
    for _model, model_admin in site._registry.items():
        descriptors.extend(emit_for_admin(model_admin))

    names = {d.tool_name for d in descriptors}
    # The Post side surfaces normally:
    assert "admin.list:testapp.Post" in names
    # The through-table (or Tag) does NOT surface from the inline alone:
    for n in names:
        assert "Post_tags" not in n, f"unexpected through-table tool: {n}"
        assert ":testapp.Tag" not in n, f"unexpected Tag tool from inline: {n}"


# ---------------------------------------------------------------------------
# ERRATA-001 — optgroup ChoiceField conservative fallback + warning
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_errata_001_optgroup_fallback_and_warning(caplog) -> None:
    """ERRATA-001 verification clause: a ChoiceField with optgroup-shaped
    choices emits the generic string fallback AND logs a WARNING citing the
    errata. A flat-choices ChoiceField continues to emit the enum schema.
    """
    from django import forms

    from django_mcp.schemas import field_to_json_schema

    flat = forms.ChoiceField(choices=[("a", "A"), ("b", "B")])
    assert field_to_json_schema(flat) == {"enum": ["a", "b"]}

    with caplog.at_level(logging.WARNING, logger="django_mcp.schemas"):
        grouped = forms.ChoiceField(
            choices=[
                ("Group A", [("a1", "A one"), ("a2", "A two")]),
                ("Group B", [("b1", "B one")]),
            ]
        )
        out = field_to_json_schema(grouped)

    assert out == {"type": "string"}
    matching = [r for r in caplog.records if "optgroups" in r.getMessage()]
    assert matching, "expected an optgroup WARNING log record (ERRATA-001)"
