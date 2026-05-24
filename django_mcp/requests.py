"""Synthesise HttpRequest objects for ModelAdmin permission checks (DMCP-01 §8)."""

from __future__ import annotations

import json

from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest, QueryDict

MCP_REQUEST_META_KEY = "HTTP_X_DJANGO_MCP"

# Frozen verb -> HTTP method table. Registration policy: Standards Action
# (matches DMCP-01 §5 surface). Adding a verb requires a §15 amendment.
_VERB_METHOD: dict[str, str] = {
    "list": "GET",
    "retrieve": "GET",
    "create": "POST",
    "update": "PUT",
    "delete": "DELETE",
    "action": "POST",
}


def build_admin_request(
    user: AbstractBaseUser | AnonymousUser,
    *,
    verb: str,
    body: dict | None = None,
) -> HttpRequest:
    """Synthesise an HttpRequest for ModelAdmin.has_*_permission per DMCP-01 §8."""
    try:
        method = _VERB_METHOD[verb]
    except KeyError as exc:
        raise ValueError(f"unknown verb: {verb}") from exc

    request = HttpRequest()
    request.method = method
    request.path = f"/__django_mcp__/{verb}"
    # INV-DMCP-6: the only field we set across the Django boundary is namespaced.
    request.META[MCP_REQUEST_META_KEY] = "1"
    request.user = user  # type: ignore[assignment]
    request.GET = QueryDict("", mutable=False)

    if body is not None and method in ("POST", "PUT"):
        post = QueryDict("", mutable=True)
        for key, value in body.items():
            # QueryDict only stores strings; coerce non-strings to their JSON form
            # so nested structures round-trip predictably for downstream inspectors.
            if isinstance(value, str):
                post[key] = value
            else:
                post[key] = json.dumps(value)
        post._mutable = False  # type: ignore[attr-defined]
        request.POST = post
    else:
        request.POST = QueryDict("", mutable=False)

    return request


def is_mcp_request(request: HttpRequest) -> bool:
    """Return True if the request was synthesised by django-mcp."""
    return request.META.get(MCP_REQUEST_META_KEY) == "1"
