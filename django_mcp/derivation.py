"""DMCP00 / DMCP03 — descriptors, rules, and permission outcomes.

This module owns the in-process record types emitted by derivation rules:

- DMCP-00 §3: ``ToolDescriptor``, ``ToolCallContext``, ``DerivationRule``,
  ``PermissionOutcome``.
- DMCP-03 §3: ``ResourceDescriptor``, ``PromptDescriptor``, ``PromptArgument``,
  ``ResourceDerivationRule``, ``PromptDerivationRule``.

The MCP wire-side translation lives in the transport layer (DMCP-04); these
dataclasses are the package's internal lingua franca.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar


class PermissionOutcome(StrEnum):
    """Frozen outcomes of MCP permission resolution (DMCP-00 §7)."""

    ALLOW = "allow"
    DENY = "deny"
    UNAUTHENTICATED = "unauthenticated"
    OUT_OF_SCOPE = "out_of_scope"


@dataclass(frozen=True, slots=True)
class ToolCallContext:
    """Per-invocation context handed to handlers and auth checks.

    `request_meta` is intentionally open so DMCP-04 (transport) can attach
    correlation ids, MCP key references, and audit fields without amending
    DMCP-00 §3.
    """

    user: Any
    arguments: dict[str, Any]
    request_meta: dict[str, Any] = field(default_factory=dict)


Handler = Callable[[ToolCallContext], Awaitable[dict[str, Any]]]
AuthCheck = Callable[[ToolCallContext], PermissionOutcome]


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    """In-process record produced by a derivation rule (DMCP-00 §3).

    ``description`` is a short human-readable summary the rule supplies
    (per the 2026-05-23 §15 amendment). Carried verbatim into the MCP
    ``tools/list`` wire response per DMCP-04 §5.3.1; the transport layer
    falls back to a derived-from-``origin`` string when ``description`` is
    empty, so the field can be omitted by callers that have nothing to
    add. Field ORDER in the dataclass is implementation detail
    (constructors use kwargs); the amendment fixes only the field's
    existence and semantics.
    """

    tool_name: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    handler: Handler
    auth_check: AuthCheck
    origin: str
    description: str = ""


_VALID_FAMILIES: frozenset[str] = frozenset({"admin", "view", "model", "action", "rpc"})


class DerivationRule(ABC):
    """Abstract base for rules that turn Django registry entries into ToolDescriptors.

    Subclasses MUST set the class attribute `family` to one of the frozen
    rule-family values in DMCP-00 §5/§6.
    """

    family: ClassVar[str]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if ABC in cls.__bases__:
            return
        family = cls.__dict__.get("family")
        if family is None:
            raise TypeError(
                f"{cls.__name__} must set class attribute 'family' "
                f"(one of {sorted(_VALID_FAMILIES)})"
            )
        if family not in _VALID_FAMILIES:
            raise TypeError(
                f"{cls.__name__}.family={family!r} is not one of the DMCP-00 §5 "
                f"rule families {sorted(_VALID_FAMILIES)}"
            )

    @classmethod
    @abstractmethod
    def emit(cls, source: Any) -> Iterable[ToolDescriptor]:
        """Yield ToolDescriptors derived from `source`."""


# ---------------------------------------------------------------------------
# DMCP-03 §3 — Resource and Prompt descriptors.
# ---------------------------------------------------------------------------


ResourceReadHandler = Callable[[ToolCallContext], Awaitable[Any]]
PromptRenderHandler = Callable[[ToolCallContext], list[dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class ResourceDescriptor:
    """In-process record produced by a resource-derivation rule (DMCP-03 §3).

    ``uri`` carries either a concrete URI or a URI template (per the MCP
    2025-03-26 §"Resources" / §"Resources/Templates" grammar). ``is_template``
    distinguishes the two; consumers expand placeholder segments before
    invoking ``read_handler``. The ``read_handler`` returns either bytes (for
    binary content like file fields) or a JSON-serialisable Python object
    (for model representations); the transport layer in DMCP-04 owns the
    serialisation step.
    """

    uri: str
    name: str
    description: str
    mime_type: str
    is_template: bool
    read_handler: ResourceReadHandler
    auth_check: AuthCheck
    origin: str


@dataclass(frozen=True, slots=True)
class PromptArgument:
    """One argument of a PromptDescriptor (DMCP-03 §3)."""

    name: str
    description: str
    required: bool = True


@dataclass(frozen=True, slots=True)
class PromptDescriptor:
    """In-process record produced by a prompt-derivation rule (DMCP-03 §3).

    ``render_handler`` takes a ``ToolCallContext`` whose ``arguments`` are the
    caller-supplied bindings and returns the MCP prompt-message list. The
    DMCP-03 default rule yields a single-message list (§7.3); multi-message
    prompts are reserved for a future amendment.
    """

    name: str
    description: str
    arguments: tuple[PromptArgument, ...]
    render_handler: PromptRenderHandler
    auth_check: AuthCheck
    origin: str


class ResourceDerivationRule(ABC):
    """Abstract base for rules emitting ResourceDescriptors (DMCP-03 §6).

    Parallels :class:`DerivationRule` but specialised for the resource
    surface. Subclasses MUST set the class attribute ``host`` to one of the
    DMCP-03 §5.1 frozen-enum host values.
    """

    host: ClassVar[str]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if ABC in cls.__bases__:
            return
        host = cls.__dict__.get("host")
        if host is None:
            raise TypeError(
                f"{cls.__name__} must set class attribute 'host' (one of {sorted(_VALID_HOSTS)})"
            )
        if host not in _VALID_HOSTS:
            raise TypeError(
                f"{cls.__name__}.host={host!r} is not one of the DMCP-03 §5.1 "
                f"hosts {sorted(_VALID_HOSTS)}"
            )

    @classmethod
    @abstractmethod
    def emit(cls, source: Any) -> Iterable[ResourceDescriptor]:
        """Yield ResourceDescriptors derived from `source`."""


_VALID_HOSTS: frozenset[str] = frozenset({"model", "field", "admin", "meta", "static"})


class PromptDerivationRule(ABC):
    """Abstract base for rules emitting PromptDescriptors (DMCP-03 §6).

    Subclasses MUST set ``kind`` to one of the DMCP-03-frozen prompt families
    (``"admin"`` for derived-from-action prompts, ``"user"`` for
    DJANGO_MCP_PROMPTS-registered prompts).
    """

    kind: ClassVar[str]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if ABC in cls.__bases__:
            return
        kind = cls.__dict__.get("kind")
        if kind is None:
            raise TypeError(
                f"{cls.__name__} must set class attribute 'kind' "
                f"(one of {sorted(_VALID_PROMPT_KINDS)})"
            )
        if kind not in _VALID_PROMPT_KINDS:
            raise TypeError(
                f"{cls.__name__}.kind={kind!r} is not one of the DMCP-03 "
                f"prompt kinds {sorted(_VALID_PROMPT_KINDS)}"
            )

    @classmethod
    @abstractmethod
    def emit(cls, source: Any) -> Iterable[PromptDescriptor]:
        """Yield PromptDescriptors derived from `source`."""


_VALID_PROMPT_KINDS: frozenset[str] = frozenset({"admin", "user"})
