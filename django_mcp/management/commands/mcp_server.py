"""DMCP04 §5.2 — STDIO transport via ``manage.py mcp_server``.

Runs the wire-agnostic JSON-RPC dispatcher (``django_mcp.dispatch``) against
stdin/stdout for Claude Desktop-style hosts. The wire credential is read once
from ``DJANGO_MCP_KEY`` at startup; a missing or unresolvable credential is a
hard startup error (INV-DMCP04-3 — only the env-var bearer authenticates).

Per INV-DMCP04-6, key revocation in the DB invalidates new HTTP requests
immediately but does NOT terminate an already-running STDIO process — the
credential is a host-process artefact, and the host is expected to restart
the server on revocation. This is documented at the resolution site below.

Per §5.2: stdout is the wire. Startup banners and any narrative go to stderr;
audit records flow through the ``django_mcp.audit`` logger (configured by the
operator, not this command).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from django_mcp.audit import derive_outcome_from_error_code, emit_audit_entry
from django_mcp.discovery import ensure_discovered
from django_mcp.dispatch import DispatchContext, dispatch
from django_mcp.models import MCPAPIKey


class Command(BaseCommand):
    help = (
        "Run the MCP server over STDIO (JSON-RPC on stdin/stdout). "
        "Requires DJANGO_MCP_KEY env var as the wire credential."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        credential = os.environ.get("DJANGO_MCP_KEY", "").strip()
        if not credential:
            raise CommandError(
                "DJANGO_MCP_KEY environment variable is required for STDIO transport"
            )

        # INV-DMCP04-6: the bearer is read ONCE at process start. Revoking the
        # key in the DB does NOT terminate this loop — the host process must
        # restart the server to pick up the revocation. HTTP requests honour
        # revocation immediately; STDIO trades that for the per-process model
        # MCP hosts assume.
        key = self._resolve_key(credential)
        if key is None:
            raise CommandError("DJANGO_MCP_KEY does not resolve to a valid MCPAPIKey")
        if not key.is_active():
            raise CommandError(
                f"MCPAPIKey {key.key_id} is not active (status={key.status_label()})"
            )

        user = key.user
        ensure_discovered()

        self.stderr.write(
            self.style.SUCCESS(f"django-mcp STDIO server ready (user={user.pk}, key={key.key_id})")
        )

        try:
            for line in sys.stdin:
                stripped = line.strip()
                if not stripped:
                    continue
                self._handle_one_line(stripped, user=user, key=key)
        except KeyboardInterrupt:
            self.stderr.write(self.style.WARNING("django-mcp STDIO server interrupted"))

    def _handle_one_line(self, line: str, *, user: Any, key: MCPAPIKey) -> None:
        correlation_id = uuid.uuid4().hex
        start = time.monotonic()
        method = "<malformed>"
        target: str | None = None
        envelope_in: Any = None
        envelope_out: dict[str, Any]

        try:
            envelope_in = json.loads(line)
            if isinstance(envelope_in, dict):
                raw_method = envelope_in.get("method")
                method = raw_method if isinstance(raw_method, str) else "<unknown>"
                params = envelope_in.get("params") or {}
                if isinstance(params, dict):
                    candidate = params.get("name") or params.get("uri")
                    if isinstance(candidate, str):
                        target = candidate

            ctx = DispatchContext(
                user=user,
                transport="stdio",
                key_id=key.key_id,
                allowed_tools=tuple(key.allowed_tools or ()),
                correlation_id=correlation_id,
            )
            envelope_out = asyncio.run(dispatch(envelope_in, ctx))
        except json.JSONDecodeError as exc:
            envelope_out = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32700,
                    "message": "Parse error",
                    "data": {"detail": str(exc)},
                },
            }
        except Exception as exc:  # noqa: BLE001 — the loop MUST NOT crash per §5.2.
            request_id = envelope_in.get("id") if isinstance(envelope_in, dict) else None
            envelope_out = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32603,
                    "message": "Internal error",
                    "data": {"detail": f"{type(exc).__name__}: {exc}"},
                },
            }

        sys.stdout.write(json.dumps(envelope_out) + "\n")
        sys.stdout.flush()

        error = envelope_out.get("error") if isinstance(envelope_out, dict) else None
        error_code = error.get("code") if isinstance(error, dict) else None
        error_message = error.get("message") if isinstance(error, dict) else None
        outcome = derive_outcome_from_error_code(error_code)
        if error_code == -32700 or error_code == -32600:
            outcome = "transport_error"

        emit_audit_entry(
            transport="stdio",
            key_id=key.key_id,
            user_id=getattr(user, "pk", None),
            method=method,
            target=target,
            outcome=outcome,
            duration_ms=(time.monotonic() - start) * 1000.0,
            wire_status=None,
            error_code=error_code,
            error_message=error_message,
            correlation_id=correlation_id,
        )

    @staticmethod
    def _resolve_key(credential: str) -> MCPAPIKey | None:
        if "." not in credential:
            return None
        key_id, _, secret = credential.partition(".")
        if not key_id or not secret:
            return None
        key = MCPAPIKey.objects.filter(key_id=key_id).select_related("user").first()
        if key is None or not key.verify_secret(secret):
            return None
        return key
