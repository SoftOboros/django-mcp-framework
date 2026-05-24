"""DMCP00 / DMCP03 — per-process MCPRegistry guarding the single discovery pass.

The registry holds three kinds of descriptors emitted by derivation rules:

- ``tools`` — :class:`~django_mcp.derivation.ToolDescriptor` (DMCP-01 / DMCP-02)
- ``resources`` — :class:`~django_mcp.derivation.ResourceDescriptor` (DMCP-03)
- ``prompts`` — :class:`~django_mcp.derivation.PromptDescriptor` (DMCP-03)

One ``threading.Lock`` guards the whole pass (INV-DMCP-5). Callers acquire
``lock`` around discovery; ``register`` and ``freeze`` are thread-safe via
that lock when used that way (the lock is not acquired implicitly).
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from typing import Any

from django_mcp.derivation import ToolDescriptor

# ResourceDescriptor / PromptDescriptor land in DMCP-03 task #27; importing them
# eagerly would create a forward-ref problem during the staged rollout. Keep the
# isinstance dispatch open and resolve lazily inside ``register``.


class MCPRegistry:
    """Holds tools + resources + prompts emitted by the single discovery pass.

    Iteration / ``len`` / ``in`` act on the ``tools`` dict for backwards
    compatibility with DMCP-01 / DMCP-02 callers; ``resources`` and
    ``prompts`` are accessed via their named attributes.
    """

    def __init__(self) -> None:
        self.tools: dict[str, ToolDescriptor] = {}
        self.resources: dict[str, Any] = {}
        self.prompts: dict[str, Any] = {}
        self.lock: threading.Lock = threading.Lock()
        self._frozen: bool = False

    def register(self, descriptor: Any) -> None:
        """Register ``descriptor`` into the appropriate per-kind dict.

        Dispatches by isinstance: ToolDescriptor → tools; ResourceDescriptor →
        resources; PromptDescriptor → prompts. Unknown kinds raise TypeError.
        Duplicate keys within a kind raise ValueError; registering on a
        frozen registry raises RuntimeError.
        """
        if self._frozen:
            raise RuntimeError(f"MCPRegistry is frozen; cannot register {descriptor!r}")
        target, key = self._target_for(descriptor)
        if key in target:
            raise ValueError(f"duplicate {target_kind_label(target, self)}: {key!r}")
        target[key] = descriptor

    def _target_for(self, descriptor: Any) -> tuple[dict[str, Any], str]:
        if isinstance(descriptor, ToolDescriptor):
            return self.tools, descriptor.tool_name
        # Lazy imports for DMCP-03 descriptor kinds: this lets DMCP-01/DMCP-02
        # callers use the registry before django_mcp.derivation grows the
        # ResourceDescriptor / PromptDescriptor types.
        try:
            from django_mcp.derivation import PromptDescriptor, ResourceDescriptor
        except ImportError:
            ResourceDescriptor = None  # type: ignore[assignment]
            PromptDescriptor = None  # type: ignore[assignment]
        if ResourceDescriptor is not None and isinstance(descriptor, ResourceDescriptor):
            return self.resources, descriptor.uri
        if PromptDescriptor is not None and isinstance(descriptor, PromptDescriptor):
            return self.prompts, descriptor.name
        raise TypeError(
            f"MCPRegistry.register: unknown descriptor type {type(descriptor).__name__}"
        )

    def freeze(self) -> None:
        self._frozen = True

    def is_frozen(self) -> bool:
        return self._frozen

    def clear(self) -> None:
        # Resets all three dicts to their pre-discovery state. Intended for
        # test fixtures and process-restart scenarios; not part of the
        # normative discovery flow (INV-DMCP-5 requires a single pass per
        # *process*).
        self.tools.clear()
        self.resources.clear()
        self.prompts.clear()
        self._frozen = False

    def __iter__(self) -> Iterator[ToolDescriptor]:
        # Back-compat with DMCP-01 / DMCP-02 callers: iterate tools.
        return iter(self.tools.values())

    def __len__(self) -> int:
        # Total descriptor count across all three kinds — useful for
        # discovery summaries but back-compat callers should be aware.
        return len(self.tools) + len(self.resources) + len(self.prompts)

    def __contains__(self, key: object) -> bool:
        # Back-compat: tool-name membership. Resource URIs and prompt names
        # have their own namespaces — callers check ``registry.resources``
        # or ``registry.prompts`` directly.
        return key in self.tools


def target_kind_label(target: dict[str, Any], registry: MCPRegistry) -> str:
    if target is registry.tools:
        return "tool_name"
    if target is registry.resources:
        return "resource uri"
    if target is registry.prompts:
        return "prompt name"
    return "descriptor key"


_registry: MCPRegistry | None = None
_singleton_lock = threading.Lock()


def get_registry() -> MCPRegistry:
    """Return the process-singleton MCPRegistry, creating it on first call."""
    global _registry
    if _registry is None:
        with _singleton_lock:
            if _registry is None:
                _registry = MCPRegistry()
    return _registry


# Pre-1.0 deprecation-free rename per DMCP-03 §10.1 — the previous name
# ``ToolRegistry`` is gone. Callers MUST use ``MCPRegistry`` going forward.
