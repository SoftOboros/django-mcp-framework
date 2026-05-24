"""End-to-end CRUD smoke for the admin → MCP rules — exercising the asyncio
handlers against a real DB. Anchors INV-DMCP-1 (async/ORM boundary) and the
DMCP-01 §5 frozen output surface.
"""

from __future__ import annotations

import asyncio

import pytest
from django.contrib.admin import AdminSite
from django.core.exceptions import PermissionDenied

from django_mcp.admin import emit_for_admin
from django_mcp.derivation import PermissionOutcome, ToolCallContext
from tests.testapp.admin import PostAdmin
from tests.testapp.models import Post


@pytest.fixture
def post_descs():
    site = AdminSite(name="crud")
    return {d.tool_name: d for d in emit_for_admin(PostAdmin(Post, site))}


@pytest.mark.django_db(transaction=True)
def test_list_handler_returns_paged_results(post_descs, admin_user) -> None:
    Post.objects.create(title="A", author=admin_user)
    Post.objects.create(title="B", author=admin_user)
    Post.objects.create(title="C", author=admin_user)

    out = asyncio.run(
        post_descs["admin.list:testapp.Post"].handler(
            ToolCallContext(user=admin_user, arguments={"page": 1, "page_size": 2})
        )
    )
    assert out["count"] == 3
    assert out["page"] == 1
    assert out["page_size"] == 2
    assert len(out["results"]) == 2


@pytest.mark.django_db(transaction=True)
def test_list_handler_honours_search_fields(post_descs, admin_user) -> None:
    Post.objects.create(title="needle", author=admin_user)
    Post.objects.create(title="haystack", author=admin_user)

    out = asyncio.run(
        post_descs["admin.list:testapp.Post"].handler(
            ToolCallContext(user=admin_user, arguments={"q": "needle"})
        )
    )
    assert out["count"] == 1
    assert out["results"][0]["title"] == "needle"


@pytest.mark.django_db(transaction=True)
def test_create_then_retrieve_then_delete_round_trip(post_descs, admin_user) -> None:
    create_args = {
        "title": "Round trip",
        "body": "",
        "author": admin_user.pk,
        "status": "draft",
        "visibility": "public_a",
        "published": False,
        "secret_note": "",
    }
    out = asyncio.run(
        post_descs["admin.create:testapp.Post"].handler(
            ToolCallContext(user=admin_user, arguments=create_args)
        )
    )
    pk = out["object"]["id"]
    assert out["object"]["title"] == "Round trip"

    retrieved = asyncio.run(
        post_descs["admin.retrieve:testapp.Post"].handler(
            ToolCallContext(user=admin_user, arguments={"pk": pk})
        )
    )["object"]
    assert retrieved["title"] == "Round trip"

    deleted = asyncio.run(
        post_descs["admin.delete:testapp.Post"].handler(
            ToolCallContext(user=admin_user, arguments={"pk": pk})
        )
    )
    assert deleted == {"deleted": True, "pk": pk}
    assert not Post.objects.filter(pk=pk).exists()


@pytest.mark.django_db(transaction=True)
def test_retrieve_missing_pk_raises_lookup_error(post_descs, admin_user) -> None:
    with pytest.raises(LookupError):
        asyncio.run(
            post_descs["admin.retrieve:testapp.Post"].handler(
                ToolCallContext(user=admin_user, arguments={"pk": 999999})
            )
        )


@pytest.mark.django_db(transaction=True)
def test_delete_without_permission_raises(post_descs, admin_user, plain_user) -> None:
    p = Post.objects.create(title="x", author=admin_user)
    # plain_user lacks delete_post; handler's per-obj re-check raises.
    with pytest.raises(PermissionDenied):
        asyncio.run(
            post_descs["admin.delete:testapp.Post"].handler(
                ToolCallContext(user=plain_user, arguments={"pk": p.pk})
            )
        )


@pytest.mark.django_db(transaction=True)
def test_action_handler_runs_and_returns_count(post_descs, admin_user) -> None:
    p1 = Post.objects.create(title="P1", author=admin_user)
    p2 = Post.objects.create(title="P2", author=admin_user)

    out = asyncio.run(
        post_descs["admin.action:testapp.Post.publish"].handler(
            ToolCallContext(user=admin_user, arguments={"pks": [p1.pk, p2.pk]})
        )
    )
    assert out["updated"] == 2
    assert Post.objects.filter(pk__in=[p1.pk, p2.pk], published=True).count() == 2


@pytest.mark.django_db(transaction=True)
def test_auth_check_anonymous_returns_unauthenticated(post_descs) -> None:
    from django.contrib.auth.models import AnonymousUser

    ctx = ToolCallContext(user=AnonymousUser(), arguments={})
    assert post_descs["admin.list:testapp.Post"].auth_check(ctx) == (
        PermissionOutcome.UNAUTHENTICATED
    )
