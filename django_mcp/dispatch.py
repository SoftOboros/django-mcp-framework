"""DMCP-04 — wire-agnostic JSON-RPC dispatcher.

This module owns the JSON-RPC 2.0 method-routing surface shared by the
streamable-HTTP and STDIO transports. The dispatcher is intentionally free of
wire-specific concerns: bearer parsing, HTTP-status mapping, and stdin/stdout
framing are the caller's job. The caller MUST run discovery
(``django_mcp.discovery.ensure_discovered``) before invoking ``dispatch`` so
the registry is populated and frozen per INV-DMCP-5.

Audit-outcome derivation is left to the caller, which inspects the returned
envelope's ``error.code`` (when present): ``-32001`` maps to
``"unauthenticated"``, ``-32002`` to ``"deny"``, ``-32003`` to
``"out_of_scope"``, any other error to ``"handler_error"``. A response without
an ``error`` field is ``"allow"``. Transport-level rejections (bad bearer,
malformed JSON body) never reach this module — they map to ``"transport_error"``
on the audit side.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

from asgiref.sync import sync_to_async
from django.conf import settings

import django_mcp
from django_mcp.derivation import (
    PermissionOutcome,
    PromptDescriptor,
    ResourceDescriptor,
    ToolCallContext,
    ToolDescriptor,
)
from django_mcp.names import ResourceURIError, parse_resource_uri
from django_mcp.registry import get_registry

MCP_PROTOCOL_VERSION = "2025-03-26"
SERVER_NAME = "django-mcp"
_DEFAULT_PAGE_SIZE = 100


@dataclass(frozen=True, slots=True)
class JsonRpcError(Exception):
    """JSON-RPC 2.0 error payload per DMCP-04 §5.5.

    Raised internally by ``_initialize`` / ``_tools_call`` / ``_resource_read``
    / ``_prompt_get`` and the envelope-validation step; ``dispatch`` catches
    these and emits the error envelope.
    """

    code: int
    message: str
    data: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        Exception.__init__(self, self.message)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            payload["data"] = self.data
        return payload

    @classmethod
    def unauthenticated(cls) -> JsonRpcError:
        return cls(code=-32001, message="unauthenticated")

    @classmethod
    def forbidden(cls) -> JsonRpcError:
        return cls(code=-32002, message="forbidden")

    @classmethod
    def out_of_scope(cls, target: str) -> JsonRpcError:
        return cls(
            code=-32003,
            message="out_of_scope: tool not in this key's allowlist",
            data={"target": target},
        )

    @classmethod
    def parse_error(cls) -> JsonRpcError:
        return cls(code=-32700, message="Parse error")

    @classmethod
    def invalid_request(cls, reason: str) -> JsonRpcError:
        return cls(code=-32600, message=f"Invalid Request: {reason}")

    @classmethod
    def method_not_found(cls, method: str) -> JsonRpcError:
        return cls(
            code=-32601,
            message=f"Method not found: {method!r}",
            data={"method": method},
        )

    @classmethod
    def invalid_params(cls, reason: str, data: dict[str, Any] | None = None) -> JsonRpcError:
        return cls(code=-32602, message=f"Invalid params: {reason}", data=data)

    @classmethod
    def internal_error(cls, reason: str) -> JsonRpcError:
        return cls(code=-32603, message=f"Internal error: {reason}")


@dataclass(frozen=True, slots=True)
class DispatchContext:
    """Per-request context handed to ``dispatch``.

    ``user`` may be ``None`` / ``AnonymousUser`` only on the
    INV-DMCP04-9 unauthenticated ``initialize`` path; every other route
    expects an authenticated user resolved from the MCPAPIKey.
    """

    user: Any
    transport: str
    key_id: str | None
    allowed_tools: tuple[str, ...]
    correlation_id: str


async def dispatch(envelope: dict[str, Any], ctx: DispatchContext) -> dict[str, Any]:
    """Route a JSON-RPC request envelope through the per-method handlers."""
    request_id: Any = envelope.get("id") if isinstance(envelope, dict) else None
    try:
        if not isinstance(envelope, dict):
            raise JsonRpcError.invalid_request("envelope must be a JSON object")
        if envelope.get("jsonrpc") != "2.0":
            raise JsonRpcError.invalid_request("jsonrpc must be '2.0'")
        method = envelope.get("method")
        if not isinstance(method, str):
            raise JsonRpcError.invalid_request("method must be a string")
        if "id" not in envelope:
            raise JsonRpcError.invalid_request("notifications are not supported")
        request_id = envelope["id"]
        if request_id is not None and not isinstance(request_id, (int, str)):
            raise JsonRpcError.invalid_request("id must be int, string, or null")
        params = envelope.get("params") or {}
        if not isinstance(params, dict):
            raise JsonRpcError.invalid_request("params must be an object")

        result = await _route(method, params, ctx)
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    except JsonRpcError as exc:
        return {"jsonrpc": "2.0", "id": request_id, "error": exc.to_dict()}


async def _route(method: str, params: dict[str, Any], ctx: DispatchContext) -> dict[str, Any]:
    if method == "initialize":
        return await _initialize(params, ctx)

    registry = get_registry()
    surface = method.split("/", 1)[0]
    if surface == "tools" and not registry.tools:
        raise JsonRpcError.method_not_found(method)
    if surface == "resources" and not registry.resources:
        raise JsonRpcError.method_not_found(method)
    if surface == "prompts" and not registry.prompts:
        raise JsonRpcError.method_not_found(method)

    if method == "tools/list":
        return _tools_list(params, ctx)
    if method == "tools/call":
        return await _tools_call(params, ctx)
    if method == "resources/list":
        return _resources_list(params, ctx)
    if method == "resources/templates/list":
        return _resource_templates_list(params, ctx)
    if method == "resources/read":
        return await _resource_read(params, ctx)
    if method == "prompts/list":
        return _prompts_list(params, ctx)
    if method == "prompts/get":
        return await _prompt_get(params, ctx)
    raise JsonRpcError.method_not_found(method)


def _build_capabilities() -> dict[str, Any]:
    registry = get_registry()
    caps: dict[str, Any] = {}
    if registry.tools:
        caps["tools"] = {"listChanged": False}
    if registry.resources:
        caps["resources"] = {"listChanged": False, "subscribe": False}
    if registry.prompts:
        caps["prompts"] = {"listChanged": False}
    return caps


async def _initialize(params: dict[str, Any], ctx: DispatchContext) -> dict[str, Any]:
    return {
        "protocolVersion": MCP_PROTOCOL_VERSION,
        "capabilities": _build_capabilities(),
        "serverInfo": {
            "name": SERVER_NAME,
            "version": django_mcp.__version__,
            "djangoMcpProtocolPhase": "DMCP-04",
        },
    }


def _page_size() -> int:
    return int(getattr(settings, "DJANGO_MCP_PAGE_SIZE", _DEFAULT_PAGE_SIZE))


def _decode_cursor(cursor: Any) -> int:
    if cursor is None or cursor == "":
        return 0
    if not isinstance(cursor, str):
        raise JsonRpcError.invalid_params("cursor must be a string")
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("ascii")
        offset = int(decoded)
    except (ValueError, UnicodeDecodeError) as exc:
        raise JsonRpcError.invalid_params(f"invalid cursor: {exc}") from exc
    if offset < 0:
        raise JsonRpcError.invalid_params("cursor offset must be non-negative")
    return offset


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode("ascii")).decode("ascii")


def _paginate(items: list[Any], cursor: Any) -> tuple[list[Any], str | None]:
    offset = _decode_cursor(cursor)
    page_size = _page_size()
    page = items[offset : offset + page_size]
    next_cursor = _encode_cursor(offset + page_size) if offset + page_size < len(items) else None
    return page, next_cursor


def _tools_list(params: dict[str, Any], ctx: DispatchContext) -> dict[str, Any]:
    registry = get_registry()
    items = sorted(registry.tools.values(), key=lambda d: d.tool_name)
    page, next_cursor = _paginate(items, params.get("cursor"))
    tools = [
        {
            "name": d.tool_name,
            # DMCP-00 §3 2026-05-23 amendment: rules supply `description`; fall
            # back to the previously-ratified derived-from-origin text per
            # DMCP-04 §5.3.1 when a rule produced no text.
            "description": d.description or f"Derived MCP tool from {d.origin}",
            "inputSchema": d.input_schema,
        }
        for d in page
    ]
    return {"tools": tools, "nextCursor": next_cursor}


def _resources_list(params: dict[str, Any], ctx: DispatchContext) -> dict[str, Any]:
    registry = get_registry()
    concrete = [d for d in registry.resources.values() if not d.is_template]
    concrete.sort(key=lambda d: d.uri)
    page, next_cursor = _paginate(concrete, params.get("cursor"))
    resources = [
        {
            "uri": d.uri,
            "name": d.name,
            "description": d.description,
            "mimeType": d.mime_type,
        }
        for d in page
    ]
    return {"resources": resources, "nextCursor": next_cursor}


def _resource_templates_list(params: dict[str, Any], ctx: DispatchContext) -> dict[str, Any]:
    registry = get_registry()
    templates = [d for d in registry.resources.values() if d.is_template]
    templates.sort(key=lambda d: d.uri)
    page, next_cursor = _paginate(templates, params.get("cursor"))
    resource_templates = [
        {
            "uriTemplate": d.uri,
            "name": d.name,
            "description": d.description,
            "mimeType": d.mime_type,
        }
        for d in page
    ]
    return {"resourceTemplates": resource_templates, "nextCursor": next_cursor}


def _prompts_list(params: dict[str, Any], ctx: DispatchContext) -> dict[str, Any]:
    registry = get_registry()
    items = sorted(registry.prompts.values(), key=lambda d: d.name)
    page, next_cursor = _paginate(items, params.get("cursor"))
    prompts = [
        {
            "name": d.name,
            "description": d.description,
            "arguments": [
                {"name": a.name, "description": a.description, "required": a.required}
                for a in d.arguments
            ],
        }
        for d in page
    ]
    return {"prompts": prompts, "nextCursor": next_cursor}


def _translate_outcome(outcome: PermissionOutcome, target: str) -> None:
    if outcome is PermissionOutcome.ALLOW:
        return
    if outcome is PermissionOutcome.UNAUTHENTICATED:
        raise JsonRpcError.unauthenticated()
    if outcome is PermissionOutcome.DENY:
        raise JsonRpcError.forbidden()
    if outcome is PermissionOutcome.OUT_OF_SCOPE:
        raise JsonRpcError.out_of_scope(target)
    raise JsonRpcError.internal_error(f"unknown permission outcome {outcome!r}")


def _build_call_context(ctx: DispatchContext, arguments: dict[str, Any]) -> ToolCallContext:
    return ToolCallContext(
        user=ctx.user,
        arguments=arguments,
        request_meta={
            "correlation_id": ctx.correlation_id,
            "transport": ctx.transport,
            "key_id": ctx.key_id,
        },
    )


async def _tools_call(params: dict[str, Any], ctx: DispatchContext) -> dict[str, Any]:
    name = params.get("name")
    if not isinstance(name, str) or not name:
        raise JsonRpcError.invalid_params("tools/call requires a string 'name'")
    registry = get_registry()
    descriptor: ToolDescriptor | None = registry.tools.get(name)
    if descriptor is None:
        raise JsonRpcError.invalid_params(f"unknown tool {name!r}", data={"name": name})

    if ctx.allowed_tools and name not in ctx.allowed_tools:
        raise JsonRpcError.out_of_scope(name)

    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        raise JsonRpcError.invalid_params("'arguments' must be an object")

    call_ctx = _build_call_context(ctx, arguments)
    outcome = await sync_to_async(descriptor.auth_check, thread_sensitive=True)(call_ctx)
    _translate_outcome(outcome, name)

    try:
        result = await descriptor.handler(call_ctx)
    except Exception as exc:  # noqa: BLE001 — MCP convention: handler errors are in-envelope.
        return {
            "content": [
                {"type": "text", "text": f"{type(exc).__name__}: {exc}"},
            ],
            "isError": True,
        }
    return {
        "content": [{"type": "text", "text": json.dumps(result, default=str)}],
        "isError": False,
    }


def _match_resource_uri(
    uri: str,
) -> tuple[ResourceDescriptor, dict[str, Any]] | None:
    """Resolve ``uri`` to a (descriptor, placeholders) pair, or None."""
    registry = get_registry()
    exact = registry.resources.get(uri)
    if exact is not None:
        return exact, {}
    try:
        concrete = parse_resource_uri(uri)
    except ResourceURIError:
        return None
    if concrete.is_template:
        return None
    for descriptor in registry.resources.values():
        if not descriptor.is_template:
            continue
        try:
            template = parse_resource_uri(descriptor.uri)
        except ResourceURIError:
            continue
        if template.host != concrete.host or template.target != concrete.target:
            continue
        if len(template.segments) != len(concrete.segments):
            continue
        placeholders: dict[str, Any] = {}
        matched = True
        for tmpl_seg, real_seg in zip(template.segments, concrete.segments, strict=False):
            if tmpl_seg.startswith("{") and tmpl_seg.endswith("}"):
                placeholders[tmpl_seg[1:-1]] = real_seg
            elif tmpl_seg != real_seg:
                matched = False
                break
        if matched:
            return descriptor, placeholders
    return None


async def _resource_read(params: dict[str, Any], ctx: DispatchContext) -> dict[str, Any]:
    uri = params.get("uri")
    if not isinstance(uri, str) or not uri:
        raise JsonRpcError.invalid_params("resources/read requires a string 'uri'")

    match = _match_resource_uri(uri)
    if match is None:
        raise JsonRpcError.invalid_params(f"unknown resource uri {uri!r}", data={"uri": uri})
    descriptor, placeholders = match

    if ctx.allowed_tools and descriptor.uri not in ctx.allowed_tools:
        raise JsonRpcError.out_of_scope(descriptor.uri)

    call_ctx = _build_call_context(ctx, placeholders)
    outcome = await sync_to_async(descriptor.auth_check, thread_sensitive=True)(call_ctx)
    _translate_outcome(outcome, descriptor.uri)

    try:
        result = await descriptor.read_handler(call_ctx)
    except JsonRpcError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise JsonRpcError.internal_error(f"{type(exc).__name__}: {exc}") from exc

    if isinstance(result, (bytes, bytearray)):
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": descriptor.mime_type,
                    "blob": base64.b64encode(bytes(result)).decode("ascii"),
                }
            ]
        }
    return {
        "contents": [
            {
                "uri": uri,
                "mimeType": descriptor.mime_type,
                "text": json.dumps(result, default=str),
            }
        ]
    }


async def _prompt_get(params: dict[str, Any], ctx: DispatchContext) -> dict[str, Any]:
    name = params.get("name")
    if not isinstance(name, str) or not name:
        raise JsonRpcError.invalid_params("prompts/get requires a string 'name'")
    registry = get_registry()
    descriptor: PromptDescriptor | None = registry.prompts.get(name)
    if descriptor is None:
        raise JsonRpcError.invalid_params(f"unknown prompt {name!r}", data={"name": name})

    if ctx.allowed_tools and name not in ctx.allowed_tools:
        raise JsonRpcError.out_of_scope(name)

    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        raise JsonRpcError.invalid_params("'arguments' must be an object")

    call_ctx = _build_call_context(ctx, arguments)
    outcome = await sync_to_async(descriptor.auth_check, thread_sensitive=True)(call_ctx)
    _translate_outcome(outcome, name)

    try:
        messages = descriptor.render_handler(call_ctx)
    except JsonRpcError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise JsonRpcError.internal_error(f"{type(exc).__name__}: {exc}") from exc

    return {"description": descriptor.description, "messages": messages}
