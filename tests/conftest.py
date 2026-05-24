"""Shared fixtures for the django-mcp test suite."""

from __future__ import annotations

import pytest

from django_mcp.registry import get_registry


@pytest.fixture(autouse=True)
def reset_tool_registry():
    """INV-DMCP-5 says the discovery pass runs once *per process*; in tests we
    explicitly clear the singleton between tests so each test starts from a
    fresh, unfrozen registry. Production code never calls ``clear``.
    """
    reg = get_registry()
    reg.clear()
    yield
    reg.clear()


@pytest.fixture
def admin_user(django_user_model):
    return django_user_model.objects.create_superuser(
        username="root", email="root@example.test", password="x"
    )


@pytest.fixture
def staff_user(django_user_model):
    from django.contrib.auth.models import Permission

    user = django_user_model.objects.create_user(
        username="staffer", email="staffer@example.test", password="x", is_staff=True
    )
    # Grant view on Post / Tag so has_view_permission passes the codename check
    # but withhold change/delete unless a test explicitly grants them.
    for codename in ("view_post", "view_tag"):
        user.user_permissions.add(Permission.objects.get(codename=codename))
    return user


@pytest.fixture
def plain_user(django_user_model):
    return django_user_model.objects.create_user(
        username="plain", email="plain@example.test", password="x"
    )
