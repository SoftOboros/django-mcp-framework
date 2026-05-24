"""DMCP-04 §5.1 / §7 — streamable-HTTP transport (single async view).

Implements the only HTTP endpoint per INV-DMCP04-1; bearer resolution per §6.2;
audit emission on every code path per INV-DMCP04-5; CSRF exempt scoped to this
view only per INV-DMCP04-4. The view is ``async def`` and wraps every blocking
ORM / bcrypt call through ``sync_to_async`` (for the database read) or
``asyncio.to_thread`` (for the CPU-bound ``check_password`` invoked by
``MCPAPIKey.verify_secret``) per INV-DMCP04-10.

Transport-level rejections (bad method, malformed JSON, missing/invalid bearer,
revoked key, inactive user) return an HTTP status with no JSON-RPC envelope per
§10.6. In-envelope failures (dispatch errors) come back as HTTP 200 with a
JSON-RPC error body — the dispatcher already shapes those.

``initialize`` is reachable without a bearer per INV-DMCP04-9. Every other
method requires a valid, active bearer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

from asgiref.sync import sync_to_async
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from django_mcp.audit import derive_outcome_from_error_code, emit_audit_entry
from django_mcp.discovery import ensure_discovered
from django_mcp.dispatch import DispatchContext, dispatch
from django_mcp.models import MCPAPIKey

logger = logging.getLogger(__name__)

TRANSPORT_LABEL = "streamable-http"


@csrf_exempt
async def mcp_endpoint(request: HttpRequest) -> HttpResponse:
    """The single streamable-HTTP endpoint per DMCP-04 §5.1.

    Step order matches §7: transport rejections → body parse → content-type
    check → bearer resolution (skipped for ``initialize`` per INV-DMCP04-9) →
    discovery → dispatch → audit → fire-and-forget ``last_used_at``.
    """
    correlation_id = uuid.uuid4().hex
    start = time.monotonic()

    if request.method != "POST":
        _emit(
            method="<pre-dispatch>",
            target=None,
            outcome="transport_error",
            duration_ms=_elapsed_ms(start),
            wire_status=405,
            error_code=None,
            error_message=f"method {request.method} not allowed",
            correlation_id=correlation_id,
            key_id=None,
            user_id=None,
        )
        return HttpResponse(status=405)

    raw_body = request.body or b"{}"
    try:
        envelope: Any = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        _emit(
            method="<pre-dispatch>",
            target=None,
            outcome="transport_error",
            duration_ms=_elapsed_ms(start),
            wire_status=400,
            error_code=-32700,
            error_message=f"invalid json body: {exc}",
            correlation_id=correlation_id,
            key_id=None,
            user_id=None,
        )
        return HttpResponse("invalid json body", status=400, content_type="text/plain")

    content_type = (request.content_type or "").lower()
    if "json" not in content_type:
        _emit(
            method=_envelope_method(envelope),
            target=_extract_target(envelope),
            outcome="transport_error",
            duration_ms=_elapsed_ms(start),
            wire_status=400,
            error_code=None,
            error_message="Content-Type must be application/json",
            correlation_id=correlation_id,
            key_id=None,
            user_id=None,
        )
        return HttpResponse(
            "Content-Type must be application/json", status=400, content_type="text/plain"
        )

    is_initialize = isinstance(envelope, dict) and envelope.get("method") == "initialize"
    auth_header = request.headers.get("Authorization", "")

    user: Any = None
    key: MCPAPIKey | None = None
    credential_present = auth_header.startswith("Bearer ")

    if credential_present:
        credential = auth_header[len("Bearer ") :].strip()
        key = await _resolve_key(credential)
        if key is not None:
            user = key.user

    if not is_initialize:
        if not credential_present:
            _emit(
                method=_envelope_method(envelope),
                target=_extract_target(envelope),
                outcome="unauthenticated",
                duration_ms=_elapsed_ms(start),
                wire_status=401,
                error_code=-32001,
                error_message="missing or malformed Authorization header",
                correlation_id=correlation_id,
                key_id=None,
                user_id=None,
            )
            return HttpResponse(status=401)
        if key is None:
            _emit(
                method=_envelope_method(envelope),
                target=_extract_target(envelope),
                outcome="unauthenticated",
                duration_ms=_elapsed_ms(start),
                wire_status=401,
                error_code=-32001,
                error_message="bearer credential did not resolve to an active key",
                correlation_id=correlation_id,
                key_id=None,
                user_id=None,
            )
            return HttpResponse(status=401)
        if not key.is_active():
            _emit(
                method=_envelope_method(envelope),
                target=_extract_target(envelope),
                outcome="deny",
                duration_ms=_elapsed_ms(start),
                wire_status=403,
                error_code=-32002,
                error_message="key revoked, expired, or user inactive",
                correlation_id=correlation_id,
                key_id=key.key_id,
                user_id=getattr(key.user, "pk", None),
            )
            return HttpResponse(status=403)

    try:
        await _ensure_discovered_async()
    except Exception as exc:  # noqa: BLE001 — discovery failure surfaces as internal error.
        logger.exception("django_mcp transport: discovery failed")
        _emit(
            method=_envelope_method(envelope),
            target=_extract_target(envelope),
            outcome="handler_error",
            duration_ms=_elapsed_ms(start),
            wire_status=500,
            error_code=-32603,
            error_message=f"discovery failed: {type(exc).__name__}: {exc}",
            correlation_id=correlation_id,
            key_id=(key.key_id if key else None),
            user_id=(getattr(user, "pk", None) if user else None),
        )
        return HttpResponse(status=500)

    ctx = DispatchContext(
        user=user,
        transport=TRANSPORT_LABEL,
        key_id=(key.key_id if key else None),
        allowed_tools=tuple(key.allowed_tools) if key else (),
        correlation_id=correlation_id,
    )

    response_envelope = await dispatch(envelope if isinstance(envelope, dict) else {}, ctx)

    error = response_envelope.get("error") if isinstance(response_envelope, dict) else None
    error_code = error["code"] if isinstance(error, dict) else None
    error_message = error["message"] if isinstance(error, dict) else None
    outcome = derive_outcome_from_error_code(error_code)

    _emit(
        method=_envelope_method(envelope),
        target=_extract_target(envelope),
        outcome=outcome,
        duration_ms=_elapsed_ms(start),
        wire_status=200,
        error_code=error_code,
        error_message=error_message,
        correlation_id=correlation_id,
        key_id=(key.key_id if key else None),
        user_id=(getattr(user, "pk", None) if user else None),
    )

    if key is not None and outcome == "allow":
        asyncio.create_task(_touch_last_used(key))

    return JsonResponse(response_envelope, status=200)


# --- helpers --------------------------------------------------------------


def _elapsed_ms(start: float) -> float:
    return (time.monotonic() - start) * 1000.0


def _envelope_method(envelope: Any) -> str:
    if isinstance(envelope, dict):
        method = envelope.get("method")
        if isinstance(method, str):
            return method
        return "<unknown>"
    return "<malformed>"


def _extract_target(envelope: Any) -> str | None:
    if not isinstance(envelope, dict):
        return None
    params = envelope.get("params") or {}
    if not isinstance(params, dict):
        return None
    target = params.get("name") or params.get("uri")
    return target if isinstance(target, str) else None


def _emit(
    *,
    method: str,
    target: str | None,
    outcome: str,
    duration_ms: float,
    wire_status: int | None,
    error_code: int | None,
    error_message: str | None,
    correlation_id: str,
    key_id: str | None,
    user_id: int | None,
) -> None:
    emit_audit_entry(
        transport=TRANSPORT_LABEL,
        key_id=key_id,
        user_id=user_id,
        method=method,
        target=target,
        outcome=outcome,
        duration_ms=duration_ms,
        wire_status=wire_status,
        error_code=error_code,
        error_message=error_message,
        correlation_id=correlation_id,
    )


async def _resolve_key(credential: str) -> MCPAPIKey | None:
    """Per DMCP-04 §6.2 steps 1-5. Returns the key on full success; ``None`` else.

    The "active" check (§6.2 step 4/6) is intentionally NOT done here — the
    caller distinguishes 401 (unresolved) from 403 (resolved-but-inactive) by
    calling ``key.is_active()`` after this returns.
    """
    if not credential or "." not in credential:
        return None
    key_id, _, secret = credential.partition(".")
    if not key_id or not secret:
        return None

    key = await sync_to_async(_lookup_key_sync, thread_sensitive=True)(key_id)
    if key is None:
        return None

    verified = await asyncio.to_thread(key.verify_secret, secret)
    if not verified:
        return None
    return key


def _lookup_key_sync(key_id: str) -> MCPAPIKey | None:
    return MCPAPIKey.objects.filter(key_id=key_id).select_related("user").first()


async def _ensure_discovered_async() -> None:
    await sync_to_async(ensure_discovered, thread_sensitive=True)()


async def _touch_last_used(key: MCPAPIKey) -> None:
    try:
        await sync_to_async(key.touch_last_used, thread_sensitive=True)()
    except Exception:  # noqa: BLE001 — INV-DMCP04-10 fire-and-forget.
        logger.exception("django_mcp transport: last_used_at update failed")
