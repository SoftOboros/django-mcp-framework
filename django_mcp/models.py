"""DMCP04 §6 — MCPAPIKey model.

A dedicated MCP authentication credential, separate from Django sessions and
DRF tokens (DMCP-00 §10 auth-credential separation). The wire credential is
``<key_id>.<secret>``; only the secret's hash lands in the database.

Importing this module requires Django settings to be configured. Per DMCP00-b
the top-level ``import django_mcp`` does NOT import this module, so the bare
package still loads without ``DJANGO_SETTINGS_MODULE``.
"""

from __future__ import annotations

import secrets
from typing import Any

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.utils import timezone

_KEY_ID_LENGTH = 24
_SECRET_LENGTH = 32


def _generate_key_id() -> str:
    return secrets.token_urlsafe(18)[:_KEY_ID_LENGTH]


def _generate_secret() -> str:
    return secrets.token_urlsafe(24)[:_SECRET_LENGTH]


class MCPAPIKeyManager(models.Manager["MCPAPIKey"]):
    def create_key(
        self,
        user: Any,
        *,
        name: str,
        allowed_tools: list[str] | None = None,
        expires_at: Any = None,
    ) -> tuple[MCPAPIKey, str]:
        """Create a key; return ``(MCPAPIKey, plaintext_secret)``.

        The plaintext secret is shown ONCE. Subsequent reads of the
        ``MCPAPIKey`` row only have access to ``secret_hash``. To replace
        the secret, use ``MCPAPIKey.rotate()``.
        """
        # Loop-on-collision is defensive — secrets.token_urlsafe(18) collision
        # within a 24-char prefix has astronomical odds, but the DB constraint
        # is the real guarantee.
        for _ in range(8):
            key_id = _generate_key_id()
            if not self.filter(key_id=key_id).exists():
                break
        else:
            raise RuntimeError("could not generate a unique key_id after 8 attempts")

        secret = _generate_secret()
        key = self.create(
            key_id=key_id,
            secret_hash=make_password(secret),
            user=user,
            name=name,
            allowed_tools=list(allowed_tools) if allowed_tools is not None else [],
            expires_at=expires_at,
        )
        return key, secret


class MCPAPIKey(models.Model):
    """Per DMCP-04 §6.1. The wire credential is ``<key_id>.<secret>``."""

    key_id = models.CharField(max_length=_KEY_ID_LENGTH, unique=True, db_index=True)
    secret_hash = models.CharField(max_length=128)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="mcp_api_keys",
    )
    name = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True, db_index=True)
    # Empty list = "every tool/resource/prompt visible via the descriptor's
    # auth_check"; non-empty = exact-match allowlist (DMCP-04 §10.5).
    allowed_tools = models.JSONField(default=list, blank=True)

    objects: MCPAPIKeyManager = MCPAPIKeyManager()

    class Meta:
        app_label = "django_mcp"
        verbose_name = "MCP API key"
        verbose_name_plural = "MCP API keys"

    def __str__(self) -> str:
        status = self.status_label()
        return f"<MCPAPIKey {self.key_id} user={self.user_id} name={self.name!r} {status}>"

    # --- lifecycle helpers ----------------------------------------------

    def status_label(self) -> str:
        if self.revoked_at is not None:
            return "revoked"
        if self.expires_at is not None and self.expires_at <= timezone.now():
            return "expired"
        return "active"

    def is_active(self) -> bool:
        return self.status_label() == "active" and getattr(self.user, "is_active", True)

    def verify_secret(self, plaintext_secret: str) -> bool:
        return check_password(plaintext_secret, self.secret_hash)

    def revoke(self) -> None:
        self.revoked_at = timezone.now()
        self.save(update_fields=["revoked_at"])

    def rotate(self) -> str:
        """Issue a new secret; invalidate the old one. Returns the plaintext."""
        secret = _generate_secret()
        self.secret_hash = make_password(secret)
        self.save(update_fields=["secret_hash"])
        return secret

    def touch_last_used(self) -> None:
        """Best-effort timestamp update. Callers MAY fire-and-forget this."""
        self.last_used_at = timezone.now()
        self.save(update_fields=["last_used_at"])

    @property
    def wire_credential_for_testing(self) -> None:  # pragma: no cover - intentional null
        """Production code never has access to the plaintext after ``create_key``.

        This property exists as documentation: do not add a backdoor that
        materialises the plaintext from ``secret_hash``. The hash is one-way
        by design.
        """
        return None
