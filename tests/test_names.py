"""DMCP00-f / DMCP01-i — tool-name grammar parser positive and negative cases."""

from __future__ import annotations

import pytest

from django_mcp.names import (
    RuleFamily,
    ToolName,
    ToolNameError,
    Verb,
    is_valid,
    parse,
)


@pytest.mark.parametrize(
    "name",
    [
        "admin.list:auth.User",
        "admin.retrieve:auth.User",
        "admin.create:blog.Post",
        "admin.update:blog.Post",
        "admin.delete:blog.Post",
        "admin.action:blog.Post.publish",
        "admin.action:auth.User.delete_selected",
        "view.invoke:billing.InvoiceDetailView",
        "model.search:catalog.Product",
        "rpc.invoke:reports.monthly_revenue",
        # 2026-05-23 §15 amendment: leading-underscore components are now legal
        # (PEP-3131-style id_start = ALPHA / "_"). Covers the __main__ smoke case
        # and any module path that uses a leading-_ submodule.
        "view.invoke:__main__.hello",
        "view.invoke:myproj._internal.View",
    ],
)
def test_grammar_accepts_dmcp_examples(name: str) -> None:
    """DMCP-00 §5 examples round-trip through parse + str."""
    parsed = parse(name)
    assert isinstance(parsed, ToolName)
    assert str(parsed) == name
    assert is_valid(name)


@pytest.mark.parametrize(
    "name,reason_fragment",
    [
        ("admin.bogus:auth.User", "verb"),
        ("bogus.list:auth.User", "family"),
        ("admin.list:User", "two components"),
        ("admin.list:auth..User", "empty"),
        ("admin.list:auth.User ", "whitespace"),
        ("admin.list:auth.üser", "non-ASCII"),
        ("admin.list:1bad.User", "ALPHA or '_'"),
    ],
)
def test_grammar_rejects_violations(name: str, reason_fragment: str) -> None:
    assert not is_valid(name)
    with pytest.raises(ToolNameError) as info:
        parse(name)
    assert reason_fragment in info.value.reason


def test_enum_values_match_dmcp00_section_5() -> None:
    """RuleFamily / Verb StrEnum values are exactly the §5 frozen sets."""
    assert {f.value for f in RuleFamily} == {"admin", "view", "model", "action", "rpc"}
    assert {v.value for v in Verb} == {
        "list",
        "retrieve",
        "create",
        "update",
        "delete",
        "search",
        "invoke",
        "action",
    }
