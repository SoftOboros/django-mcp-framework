"""DMCP-02 acceptance gates a..j — Applications → MCP tools.

Each test pins one or more of the §12 gates and the underlying invariant(s).
"""

from __future__ import annotations

import asyncio

import pytest

from django_mcp.derivation import PermissionOutcome, ToolCallContext
from django_mcp.discovery import discover_now
from django_mcp.drf import DRF_AVAILABLE, emit_for_drf_views
from django_mcp.registry import get_registry
from django_mcp.search import emit_for_model_search, parse_model_search_entry
from django_mcp.urlwalker import ViewKind, walk_urls
from django_mcp.views import ViewInvokeRule, emit_for_walked_views

# ---------------------------------------------------------------------------
# DMCP02-a: discover_now extends to URLs + model search in the single pass
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_dmcp02_a_discovery_walks_urls_and_search() -> None:
    """A single discover_now call emits admin + view + drf + search tools."""
    discover_now()
    reg = get_registry()
    families = {d.tool_name.split(".", 1)[0] for d in reg}
    assert "admin" in families, "DMCP-01 admin pass must still run"
    assert "view" in families, "DMCP-02 view pass must contribute"


# ---------------------------------------------------------------------------
# DMCP02-b: empty URL surface → 0 DMCP-02 contributions
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_dmcp02_b_empty_urlconf_yields_no_view_tools(settings) -> None:
    """An URLconf with no non-admin patterns contributes zero view/drf tools."""
    import sys

    empty_mod = type(sys)("tests._empty_urls")
    empty_mod.urlpatterns = []  # type: ignore[attr-defined]
    sys.modules["tests._empty_urls"] = empty_mod
    try:
        settings.DJANGO_MCP_URLCONFS = ["tests._empty_urls"]
        settings.DJANGO_MCP_ADMIN_SITES = []
        discover_now()
        names = {d.tool_name for d in get_registry()}
        non_search = {n for n in names if not n.startswith("admin.") and not n.startswith("model.")}
        assert non_search == set(), f"unexpected view/drf tools: {non_search}"
    finally:
        del sys.modules["tests._empty_urls"]


# ---------------------------------------------------------------------------
# DMCP02-c: testapp fixture produces the §5-pinned set
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_dmcp02_c_testapp_fixture_emits_expected_view_tools() -> None:
    discover_now()
    names = {d.tool_name for d in get_registry()}

    # FBV
    assert "view.invoke:tests.testapp.views.hello" in names
    assert "view.invoke:tests.testapp.views.hello_authed" in names

    # CBV verb-narrowing per §5.2 / INV-DMCP02-5
    assert "view.retrieve:tests.testapp.views.PostDetailView" in names
    assert "view.list:tests.testapp.views.PostListView" in names
    # Multi-verb CBV falls back to view.invoke:
    assert "view.invoke:tests.testapp.views.PostMultiVerbView" in names

    # DRF APIView
    assert "view.invoke:tests.testapp.views.PingAPIView" in names

    # DRF ViewSet — five tools (PUT/PATCH collapse to one update — §10.1)
    drf_post_tools = {n for n in names if "tests.testapp.views.PostViewSet" in n}
    assert "view.list:tests.testapp.views.PostViewSet" in drf_post_tools
    assert "view.retrieve:tests.testapp.views.PostViewSet" in drf_post_tools
    assert "view.create:tests.testapp.views.PostViewSet" in drf_post_tools
    assert "view.update:tests.testapp.views.PostViewSet" in drf_post_tools
    assert "view.delete:tests.testapp.views.PostViewSet" in drf_post_tools


# ---------------------------------------------------------------------------
# DMCP02-d: path-arg parity (INV-DMCP02-2)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_dmcp02_d_int_pk_path_arg_schema() -> None:
    """An <int:pk> URL segment yields an integer schema in path.properties.pk."""
    discover_now()
    reg = get_registry()
    detail = next(
        d for d in reg if d.tool_name == "view.retrieve:tests.testapp.views.PostDetailView"
    )
    pk_schema = detail.input_schema["properties"]["path"]["properties"]["pk"]
    assert pk_schema == {"type": "integer"}, pk_schema


@pytest.mark.django_db
def test_dmcp02_d_string_path_arg_schema() -> None:
    """A <str:who> URL segment yields a plain string schema."""
    discover_now()
    reg = get_registry()
    hello = next(d for d in reg if d.tool_name == "view.invoke:tests.testapp.views.hello")
    who_schema = hello.input_schema["properties"]["path"]["properties"]["who"]
    assert who_schema == {"type": "string"}


# ---------------------------------------------------------------------------
# DMCP02-e: INV-DMCP02-4 — REQUIRE_AUTH=True culls views without detected gate
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_dmcp02_e_require_auth_true_culls_unguarded_views(settings) -> None:
    """With REQUIRE_AUTH=True, a bare FBV (no decorator, no mixin) is skipped.

    ``hello`` has no detectable auth gate; ``hello_authed`` does
    (@login_required). The first MUST be absent; the second MUST be present.
    """
    settings.DJANGO_MCP_REQUIRE_AUTH = True
    discover_now()
    names = {d.tool_name for d in get_registry()}
    assert "view.invoke:tests.testapp.views.hello" not in names, (
        "bare FBV with no auth gate must be skipped when REQUIRE_AUTH=True"
    )
    assert "view.invoke:tests.testapp.views.hello_authed" in names, (
        "FBV with @login_required must be emitted"
    )


@pytest.mark.django_db
def test_dmcp02_e_require_auth_false_emits_unguarded_views(settings) -> None:
    """With REQUIRE_AUTH=False, bare views are emitted with ALLOW auth_check."""
    settings.DJANGO_MCP_REQUIRE_AUTH = False
    discover_now()
    reg = get_registry()
    bare = next((d for d in reg if d.tool_name == "view.invoke:tests.testapp.views.hello"), None)
    assert bare is not None
    # Public surface → ALLOW for any caller (including anonymous)
    from django.contrib.auth.models import AnonymousUser

    assert bare.auth_check(ToolCallContext(user=AnonymousUser(), arguments={})) == (
        PermissionOutcome.ALLOW
    )


# ---------------------------------------------------------------------------
# DMCP02-f: verb-narrowing precedence (INV-DMCP02-5)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_dmcp02_f_verb_narrowing_get_only_chooses_retrieve_or_list() -> None:
    """A CBV with only get → retrieve (or list when ListView in MRO)."""
    discover_now()
    names = {d.tool_name for d in get_registry()}
    # PostDetailView has DetailView in MRO + only get → view.retrieve:
    assert "view.retrieve:tests.testapp.views.PostDetailView" in names
    # PostListView has ListView in MRO + only get → view.list:
    assert "view.list:tests.testapp.views.PostListView" in names


@pytest.mark.django_db
def test_dmcp02_f_verb_narrowing_multi_method_falls_back_to_invoke() -> None:
    """A CBV with both get and post → view.invoke: (no single-verb narrowing)."""
    discover_now()
    names = {d.tool_name for d in get_registry()}
    multi = {n for n in names if "PostMultiVerbView" in n}
    assert "view.invoke:tests.testapp.views.PostMultiVerbView" in multi
    # And NO single-verb variant for the multi-verb CBV
    for verb in ("retrieve", "list", "create", "update", "delete"):
        assert f"view.{verb}:tests.testapp.views.PostMultiVerbView" not in multi


# ---------------------------------------------------------------------------
# DMCP02-g: DRF degradation (INV-DMCP02-8)
# ---------------------------------------------------------------------------


def test_dmcp02_g_drf_available_flag() -> None:
    """When DRF is installed (the default in this test env), DRF_AVAILABLE is True."""
    assert DRF_AVAILABLE is True


@pytest.mark.django_db
def test_dmcp02_g_emit_for_drf_views_returns_empty_for_non_drf_kinds() -> None:
    """``emit_for_drf_views`` produces zero descriptors when fed FBV-only walked views."""
    fbv_only = [w for w in walk_urls() if w.kind == ViewKind.FBV]
    descs = list(emit_for_drf_views(fbv_only))
    assert descs == []


# ---------------------------------------------------------------------------
# DMCP02-h: PUT/PATCH collapse (§10.1)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_dmcp02_h_put_patch_collapse_one_update_tool() -> None:
    """A ModelViewSet with both update and partial_update emits ONE view.update tool.

    The tool's input schema MUST mark every field as optional (PATCH-style).
    """
    discover_now()
    reg = get_registry()
    update = next(d for d in reg if d.tool_name == "view.update:tests.testapp.views.PostViewSet")
    # PATCH semantics: required list is empty for the body fields
    body_schema = update.input_schema["properties"]["body"]
    assert body_schema.get("required") in ([], None), (
        f"§10.1 PATCH-style: every field optional; got required={body_schema.get('required')}"
    )

    # And exactly one view.update: tool for this ViewSet
    update_tools = [
        d.tool_name
        for d in reg
        if d.tool_name.startswith("view.update:tests.testapp.views.PostViewSet")
    ]
    assert update_tools == ["view.update:tests.testapp.views.PostViewSet"]


# ---------------------------------------------------------------------------
# DMCP02-i: DRF parity smoke (handler returns a serializer-shaped payload)
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_dmcp02_i_drf_create_handler_round_trip(admin_user) -> None:
    """Invoking the view.create:<...> tool with valid args produces a serialized
    object whose shape matches the serializer's declared field set.
    """
    discover_now()
    reg = get_registry()
    create = next(d for d in reg if d.tool_name == "view.create:tests.testapp.views.PostViewSet")

    out = asyncio.run(
        create.handler(
            ToolCallContext(
                user=admin_user,
                arguments={
                    "body": {
                        "title": "Hello from DRF",
                        "body": "",
                        "author": admin_user.pk,
                        "status": "draft",
                        "published": False,
                    }
                },
            )
        )
    )
    # Output keys are serializer-shaped, wrapped in {"object": ...}
    obj = out["object"]
    keys = set(obj.keys())
    assert keys >= {"id", "title", "body", "author", "status", "published"}, keys
    assert obj["title"] == "Hello from DRF"


# ---------------------------------------------------------------------------
# DMCP02-j: discovery log cites DMCP-02
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_dmcp02_j_discovery_log_cites_dmcp02(caplog) -> None:
    """The discovery INFO log line includes [DMCP-01+DMCP-02]."""
    import logging

    with caplog.at_level(logging.INFO, logger="django_mcp.discovery"):
        discover_now()
    matching = [r for r in caplog.records if "DMCP-02" in r.getMessage()]
    assert matching, "expected an INFO log citing DMCP-02"


# ---------------------------------------------------------------------------
# ModelSearchRule + DJANGO_MCP_MODEL_SEARCH (§5.4 / §10.2)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_model_search_parse_string_form() -> None:
    """§10.2 string-form: a dotted model path uses defaults."""
    spec = parse_model_search_entry("testapp.Post")
    assert spec.model.__name__ == "Post"
    assert spec.permission == "testapp.view_post"
    assert spec.search_fields == []


@pytest.mark.django_db
def test_model_search_rejects_unknown_keys() -> None:
    """§10.2 unknown top-level keys → ImproperlyConfigured (frozen shape)."""
    from django.core.exceptions import ImproperlyConfigured

    with pytest.raises(ImproperlyConfigured):
        parse_model_search_entry({"model": "testapp.Post", "bogus_key": 1})


@pytest.mark.django_db(transaction=True)
def test_model_search_handler_q_search(admin_user) -> None:
    from tests.testapp.models import Post

    Post.objects.create(title="needle", author=admin_user)
    Post.objects.create(title="haystack", author=admin_user)

    descs = list(
        emit_for_model_search(
            [{"model": "testapp.Post", "search_fields": ["title"], "filter_fields": ["status"]}]
        )
    )
    assert len(descs) == 1
    d = descs[0]
    assert d.tool_name == "model.search:testapp.Post"

    out = asyncio.run(d.handler(ToolCallContext(user=admin_user, arguments={"q": "needle"})))
    assert out["count"] == 1
    assert out["results"][0]["title"] == "needle"


# ---------------------------------------------------------------------------
# Sanity: ViewInvokeRule + emit_for_walked_views are imported and callable
# ---------------------------------------------------------------------------


def test_view_invoke_rule_is_a_derivation_rule_subclass() -> None:
    from django_mcp.derivation import DerivationRule

    assert issubclass(ViewInvokeRule, DerivationRule)
    assert ViewInvokeRule.family == "view"


def test_emit_for_walked_views_accepts_empty_iter() -> None:
    assert list(emit_for_walked_views([])) == []
