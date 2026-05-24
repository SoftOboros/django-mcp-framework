"""DMCP-04 §5.1 — single MCP endpoint at the URL include's root.

INV-DMCP04-1 anchors single-endpoint-per-transport: ``urlpatterns`` carries
exactly one entry. INV-DMCP04-4 (scoped CSRF exemption) is satisfied because
``@csrf_exempt`` lives on the view in ``transport.py``, not in project-wide
middleware configuration — consumers mounting this include do not relax CSRF
for the rest of their site.
"""

from __future__ import annotations

from django.urls import path

from django_mcp.transport import mcp_endpoint

app_name = "django_mcp"

urlpatterns = [
    path("", mcp_endpoint, name="mcp"),
]
