import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class DjangoMcpConfig(AppConfig):
    name = "django_mcp"
    label = "django_mcp"
    verbose_name = "Django MCP"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        # Per DMCP-01 §10 / INV-DMCP-5: ready() primes the discovery module but
        # does NOT walk admin.site at import time — third-party apps may
        # register admins later in the boot sequence. The actual discovery
        # pass runs lazily on the first MCP request via
        # ``django_mcp.discovery.ensure_discovered`` (DMCP-01).
        from django_mcp import discovery  # noqa: F401

        logger.debug(
            "django_mcp ready [DMCP-01]: discovery primed; pass deferred to first MCP request"
        )
