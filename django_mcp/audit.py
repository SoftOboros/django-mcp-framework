"""Audit-log emitter for django-mcp transport (DMCP-04 §8).

Emits one structured INFO record on the ``django_mcp.audit`` logger for every
MCP invocation — including pre-auth failures — per INV-DMCP-7 (inherited) and
INV-DMCP04-5 (audit on every call).

The field set serialised onto the wire is FROZEN by DMCP-04 §8; changing it
requires a §15 amendment to that doc. Operators pipe the JSONL output to their
SIEM, so the on-disk shape is part of the package's compatibility surface.

The emitter itself MUST NOT raise: a serialisation failure is caught and
re-logged at WARNING on the sibling ``django_mcp.audit.errors`` logger so the
audit contract holds even when the caller passes pathological inputs.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

AUDIT_LOGGER_NAME = "django_mcp.audit"
_AUDIT_ERRORS_LOGGER_NAME = "django_mcp.audit.errors"

OUTCOMES: frozenset[str] = frozenset(
    {
        "allow",
        "deny",
        "unauthenticated",
        "out_of_scope",
        "handler_error",
        "transport_error",
    }
)

_audit_logger = logging.getLogger(AUDIT_LOGGER_NAME)
_audit_errors_logger = logging.getLogger(_AUDIT_ERRORS_LOGGER_NAME)
_audit_errors_logger.propagate = False


def emit_audit_entry(
    *,
    transport: str,
    key_id: str | None,
    user_id: int | None,
    method: str,
    target: str | None,
    outcome: str,
    duration_ms: float,
    wire_status: int | None,
    error_code: int | None,
    error_message: str | None,
    correlation_id: str,
) -> None:
    """Emit one audit record per MCP invocation.

    Called from both the streamable-HTTP view and the STDIO server, on EVERY
    call path — successes, descriptor denials, handler exceptions, and
    pre-authentication failures alike (INV-DMCP-7 / INV-DMCP04-5). The
    function is synchronous and MUST NOT raise; on internal serialisation
    failure it logs at WARNING on ``django_mcp.audit.errors`` and returns.

    The record is emitted as a single JSONL line (``record.msg``) and also
    attached via ``extra=`` so structlog-style processors can pick up
    structured fields directly.
    """

    effective_outcome = outcome
    if outcome not in OUTCOMES:
        _audit_errors_logger.warning(
            "audit: invalid outcome %r; substituting 'transport_error'", outcome
        )
        effective_outcome = "transport_error"

    entry: dict[str, Any] = {
        "ts": datetime.now(UTC).isoformat(),
        "correlation_id": correlation_id,
        "transport": transport,
        "key_id": key_id,
        "user_id": user_id,
        "method": method,
        "target": target,
        "outcome": effective_outcome,
        "duration_ms": duration_ms,
        "wire_status": wire_status,
        "error_code": error_code,
        "error_message": error_message,
    }

    try:
        payload = json.dumps(entry, default=str)
    except (TypeError, ValueError) as exc:
        _audit_errors_logger.warning(
            "audit: failed to serialise entry (%s); correlation_id=%s", exc, correlation_id
        )
        return

    _audit_logger.info(payload, extra=entry)


def derive_outcome_from_error_code(code: int | None) -> str:
    """Map a JSON-RPC error code to the audit outcome label.

    Used by the HTTP view and STDIO server to translate dispatcher envelope
    output (DMCP-04 §5.5) into the audit ``outcome`` enum without rebuilding
    the mapping at each call site.
    """

    if code is None:
        return "allow"
    if code == -32001:
        return "unauthenticated"
    if code == -32002:
        return "deny"
    if code == -32003:
        return "out_of_scope"
    return "handler_error"
