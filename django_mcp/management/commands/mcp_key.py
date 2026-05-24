"""DMCP04 §6.4 — ``manage.py mcp_key`` lifecycle command.

Subcommands: ``create``, ``list``, ``revoke``, ``rotate``, ``inspect``. Secrets
are surfaced ONCE via ``create`` / ``rotate`` per DMCP-04 §6.3; never via
``list`` / ``inspect``. The wire credential line is plain (no ANSI styling) so
operators can pipe it directly to ``xclip`` / ``pbcopy``.
"""

from __future__ import annotations

from argparse import Namespace
from datetime import timedelta
from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils import timezone

from django_mcp.models import MCPAPIKey

_ADVISORY = "This secret is shown ONCE. Store it now."

_LIST_HEADERS = ("key_id", "name", "user", "status", "created_at", "last_used_at")


def _parse_allowed_tools(raw: str | None) -> list[str]:
    if raw is None:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _resolve_user(username: str) -> Any:
    user_model = get_user_model()
    try:
        user = user_model._default_manager.get(**{user_model.USERNAME_FIELD: username})
    except user_model.DoesNotExist as exc:
        raise CommandError(f"user not found: {username!r}") from exc
    if not getattr(user, "is_active", True):
        raise CommandError(f"user is not active: {username!r}")
    return user


def _get_key(key_id: str) -> MCPAPIKey:
    try:
        return MCPAPIKey.objects.get(key_id=key_id)
    except MCPAPIKey.DoesNotExist as exc:
        raise CommandError(f"key not found: {key_id!r}") from exc


def _format_dt(value: Any) -> str:
    if value is None:
        return "-"
    return value.isoformat()


def _format_table(rows: list[tuple[str, ...]]) -> str:
    if not rows:
        return ""
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    lines = []
    for row in rows:
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
    return "\n".join(lines)


class Command(BaseCommand):
    help = "Manage django-mcp API keys (create / list / revoke / rotate / inspect)."

    def add_arguments(self, parser: CommandParser) -> None:
        subparsers = parser.add_subparsers(dest="subcommand", required=True)

        create_p = subparsers.add_parser(
            "create",
            help="Create an MCP API key for a user; prints the wire credential ONCE.",
        )
        create_p.add_argument("username", help="Django username to bind the key to.")
        create_p.add_argument("--name", required=True, help="Operator-facing label.")
        create_p.add_argument(
            "--allowed-tools",
            default=None,
            help="Comma-separated allowlist (tool names / resource templates / prompt names).",
        )
        create_p.add_argument(
            "--expires-in",
            type=int,
            default=None,
            help="Days until the key expires (omit for no expiry).",
        )

        list_p = subparsers.add_parser("list", help="List MCP API keys. Never prints secrets.")
        list_p.add_argument("--user", default=None, help="Filter to a single username.")

        revoke_p = subparsers.add_parser(
            "revoke",
            help="Revoke a key; the next request authenticated with it fails (DMCP-04 §6.3).",
        )
        revoke_p.add_argument("key_id", help="The key_id to revoke.")

        rotate_p = subparsers.add_parser(
            "rotate",
            help="Issue a fresh secret; prints the new wire credential. Allowed on revoked keys.",
        )
        rotate_p.add_argument("key_id", help="The key_id to rotate.")

        inspect_p = subparsers.add_parser(
            "inspect",
            help="Show key fields incl. allowed_tools. Never prints the secret.",
        )
        inspect_p.add_argument("key_id", help="The key_id to inspect.")

    def handle(self, *args: Any, **options: Any) -> None:
        subcommand = options["subcommand"]
        ns = Namespace(**options)
        handler = {
            "create": self._handle_create,
            "list": self._handle_list,
            "revoke": self._handle_revoke,
            "rotate": self._handle_rotate,
            "inspect": self._handle_inspect,
        }[subcommand]
        handler(ns)

    # --- subcommand handlers ---------------------------------------------

    def _handle_create(self, opts: Namespace) -> None:
        user = _resolve_user(opts.username)
        allowed_tools = _parse_allowed_tools(opts.allowed_tools)
        expires_at = None
        if opts.expires_in is not None:
            if opts.expires_in <= 0:
                raise CommandError("--expires-in must be a positive integer (days)")
            expires_at = timezone.now() + timedelta(days=opts.expires_in)

        key, secret = MCPAPIKey.objects.create_key(
            user=user,
            name=opts.name,
            allowed_tools=allowed_tools,
            expires_at=expires_at,
        )
        self.stderr.write(self.style.WARNING(_ADVISORY))
        self.stdout.write(f"{key.key_id}.{secret}")

    def _handle_list(self, opts: Namespace) -> None:
        qs = MCPAPIKey.objects.all().select_related("user").order_by("created_at")
        if opts.user is not None:
            user_model = get_user_model()
            qs = qs.filter(**{f"user__{user_model.USERNAME_FIELD}": opts.user})

        rows: list[tuple[str, ...]] = [_LIST_HEADERS]
        user_field = get_user_model().USERNAME_FIELD
        for key in qs:
            rows.append(
                (
                    key.key_id,
                    key.name,
                    str(getattr(key.user, user_field, key.user_id)),
                    key.status_label(),
                    _format_dt(key.created_at),
                    _format_dt(key.last_used_at),
                )
            )
        if len(rows) == 1:
            self.stderr.write("no keys found")
            return
        self.stdout.write(_format_table(rows))

    def _handle_revoke(self, opts: Namespace) -> None:
        key = _get_key(opts.key_id)
        key.revoke()
        self.stdout.write(f"revoked: {key.key_id}")

    def _handle_rotate(self, opts: Namespace) -> None:
        key = _get_key(opts.key_id)
        secret = key.rotate()
        self.stderr.write(self.style.WARNING(_ADVISORY))
        self.stdout.write(f"{key.key_id}.{secret}")

    def _handle_inspect(self, opts: Namespace) -> None:
        key = _get_key(opts.key_id)
        user_field = get_user_model().USERNAME_FIELD
        allowed = (
            ",".join(key.allowed_tools)
            if isinstance(key.allowed_tools, list) and key.allowed_tools
            else "[]"
        )
        fields = [
            ("key_id", key.key_id),
            ("name", key.name),
            ("user", str(getattr(key.user, user_field, key.user_id))),
            ("status", key.status_label()),
            ("created_at", _format_dt(key.created_at)),
            ("last_used_at", _format_dt(key.last_used_at)),
            ("expires_at", _format_dt(key.expires_at)),
            ("revoked_at", _format_dt(key.revoked_at)),
            ("allowed_tools", allowed),
        ]
        width = max(len(name) for name, _ in fields)
        for name, value in fields:
            self.stdout.write(f"{name.ljust(width)}  {value}")
