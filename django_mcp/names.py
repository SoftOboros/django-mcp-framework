"""Tool-name grammar parser and formatter, frozen in DMCP-00 §5."""

from __future__ import annotations

import enum
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final


class RuleFamily(enum.StrEnum):
    ADMIN = "admin"
    VIEW = "view"
    MODEL = "model"
    ACTION = "action"
    RPC = "rpc"


class Verb(enum.StrEnum):
    LIST = "list"
    RETRIEVE = "retrieve"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    SEARCH = "search"
    INVOKE = "invoke"
    ACTION = "action"


class ToolNameError(ValueError):
    def __init__(self, name: str, reason: str) -> None:
        super().__init__(f"{reason}: {name!r}")
        self.name = name
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ToolName:
    family: RuleFamily
    verb: Verb
    target: tuple[str, ...]

    def __str__(self) -> str:
        return f"{self.family.value}.{self.verb.value}:{'.'.join(self.target)}"


_ALPHA: Final = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
_DIGIT: Final = frozenset("0123456789")
_ID_START: Final = _ALPHA | frozenset("_")  # DMCP-00 §5 (2026-05-23 amendment)
_ID_CONTINUE: Final = _ALPHA | _DIGIT | frozenset("_")
_LEAF_CHARS: Final = _ID_CONTINUE
_PREFIX_TAIL_CHARS: Final = _ID_CONTINUE


def _is_ascii(value: str) -> bool:
    return all(ord(ch) < 128 for ch in value)


def _validate_prefix_component(component: str, *, position: int) -> None:
    if component == "":
        raise ToolNameError(
            component,
            f"empty target component at position {position}",
        )
    if not _is_ascii(component):
        raise ToolNameError(component, "non-ASCII character in target component")
    first = component[0]
    if first not in _ID_START:
        raise ToolNameError(
            component,
            f"prefix component must start with ALPHA or '_' (offset 0 of component {position})",
        )
    for offset, ch in enumerate(component[1:], start=1):
        if ch not in _PREFIX_TAIL_CHARS:
            raise ToolNameError(
                component,
                f"component contains illegal character at offset {offset}",
            )


def _validate_leaf_component(component: str) -> None:
    if component == "":
        raise ToolNameError(component, "empty target_leaf")
    if not _is_ascii(component):
        raise ToolNameError(component, "non-ASCII character in target_leaf")
    for offset, ch in enumerate(component):
        if ch not in _LEAF_CHARS:
            raise ToolNameError(
                component,
                f"target_leaf contains illegal character at offset {offset}",
            )


def _has_whitespace(value: str) -> bool:
    return any(ch.isspace() for ch in value)


def parse(name: str) -> ToolName:
    if not isinstance(name, str):
        raise ToolNameError(repr(name), "tool name must be a string")
    if name == "":
        raise ToolNameError(name, "empty tool name")
    if not _is_ascii(name):
        raise ToolNameError(name, "non-ASCII character in tool name")
    if _has_whitespace(name):
        raise ToolNameError(name, "whitespace in tool name")

    colon_index = name.find(":")
    if colon_index < 0:
        raise ToolNameError(name, "missing ':' separating prefix from dotted_target")
    if name.count(":") > 1:
        raise ToolNameError(name, "more than one ':' in tool name")

    prefix = name[:colon_index]
    target_str = name[colon_index + 1 :]

    if prefix.count(".") != 1:
        raise ToolNameError(name, "prefix must be '<rule_family>.<verb>'")
    family_str, verb_str = prefix.split(".", 1)

    try:
        family = RuleFamily(family_str)
    except ValueError as exc:
        raise ToolNameError(name, f"unknown rule family {family_str!r}") from exc
    try:
        verb = Verb(verb_str)
    except ValueError as exc:
        raise ToolNameError(name, f"unknown verb {verb_str!r}") from exc

    if target_str == "":
        raise ToolNameError(name, "empty dotted_target")
    components = target_str.split(".")
    if len(components) < 2:
        raise ToolNameError(name, "dotted_target must have at least two components")

    *prefix_components, leaf = components
    for position, component in enumerate(prefix_components):
        _validate_prefix_component(component, position=position)
    _validate_leaf_component(leaf)

    return ToolName(family=family, verb=verb, target=tuple(components))


def is_valid(name: str) -> bool:
    try:
        parse(name)
    except ToolNameError:
        return False
    return True


def format(family: RuleFamily | str, verb: Verb | str, target: Iterable[str]) -> str:
    try:
        family_enum = RuleFamily(family)
    except ValueError as exc:
        raise ToolNameError(str(family), f"unknown rule family {family!r}") from exc
    try:
        verb_enum = Verb(verb)
    except ValueError as exc:
        raise ToolNameError(str(verb), f"unknown verb {verb!r}") from exc

    components = tuple(target)
    if len(components) < 2:
        raise ToolNameError(
            ".".join(components),
            "dotted_target must have at least two components",
        )
    *prefix_components, leaf = components
    for position, component in enumerate(prefix_components):
        _validate_prefix_component(component, position=position)
    _validate_leaf_component(leaf)

    return f"{family_enum.value}.{verb_enum.value}:{'.'.join(components)}"


# ---------------------------------------------------------------------------
# DMCP-03 §5.1 — resource URI grammar.
# ---------------------------------------------------------------------------


_RESOURCE_SCHEME: Final = "django-mcp"
_RESOURCE_HOSTS: Final = frozenset({"model", "field", "admin", "meta", "static"})


class ResourceURIError(ValueError):
    """Raised when a string fails the DMCP-03 §5.1 resource URI grammar."""

    def __init__(self, uri: str, reason: str) -> None:
        super().__init__(f"{reason}: {uri!r}")
        self.uri = uri
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ResourceURI:
    """Parsed DMCP-03 §5.1 URI.

    ``segments`` carries the post-host path split on ``/``. ``placeholders``
    is the tuple of ``{name}`` placeholders found in path segments (in order
    of appearance) — non-empty implies ``is_template`` is True.
    """

    host: str
    target: tuple[str, ...]
    segments: tuple[str, ...]
    placeholders: tuple[str, ...]

    @property
    def is_template(self) -> bool:
        return bool(self.placeholders)

    def __str__(self) -> str:
        rendered_target = ".".join(self.target)
        tail = ("/" + "/".join(self.segments)) if self.segments else ""
        return f"{_RESOURCE_SCHEME}://{self.host}/{rendered_target}{tail}"


def parse_resource_uri(uri: str) -> ResourceURI:
    """Parse a DMCP-03 §5.1 resource URI; raise ResourceURIError on violations."""
    if not isinstance(uri, str):
        raise ResourceURIError(repr(uri), "resource URI must be a string")
    if not uri.startswith(f"{_RESOURCE_SCHEME}://"):
        raise ResourceURIError(uri, f"resource URI must start with {_RESOURCE_SCHEME}://")
    if not all(ord(ch) < 128 for ch in uri):
        raise ResourceURIError(uri, "resource URI contains non-ASCII characters")

    rest = uri[len(_RESOURCE_SCHEME) + 3 :]  # strip "django-mcp://"
    if "/" not in rest:
        raise ResourceURIError(uri, "resource URI is missing a target after the host")
    host, _, after_host = rest.partition("/")
    if host not in _RESOURCE_HOSTS:
        raise ResourceURIError(
            uri, f"unknown resource host {host!r} (must be one of {sorted(_RESOURCE_HOSTS)})"
        )

    target_str, slash, path_tail = after_host.partition("/")
    if not target_str:
        raise ResourceURIError(uri, "resource URI is missing a dotted target")
    target_components = tuple(target_str.split("."))
    if len(target_components) < 2:
        raise ResourceURIError(uri, "resource URI target must have at least two dotted components")
    for position, component in enumerate(target_components):
        _validate_prefix_component(component, position=position)

    segments: tuple[str, ...] = ()
    if slash:
        if not path_tail:
            raise ResourceURIError(uri, "trailing slash with no path tail")
        raw_segments = path_tail.split("/")
        if any(not seg for seg in raw_segments):
            raise ResourceURIError(uri, "empty path segment in resource URI")
        segments = tuple(raw_segments)

    placeholders = tuple(_extract_placeholders(uri, segments))
    return ResourceURI(
        host=host,
        target=target_components,
        segments=segments,
        placeholders=placeholders,
    )


def _extract_placeholders(uri: str, segments: tuple[str, ...]) -> Iterable[str]:
    for segment in segments:
        if segment.startswith("{") and segment.endswith("}"):
            inner = segment[1:-1]
            if not inner:
                raise ResourceURIError(uri, "empty placeholder name")
            for ch in inner:
                if ch not in _LEAF_CHARS:
                    raise ResourceURIError(
                        uri,
                        f"placeholder {segment!r} contains illegal character "
                        f"(allowed: ALPHA / DIGIT / '_')",
                    )
            yield inner


def is_valid_resource_uri(uri: str) -> bool:
    try:
        parse_resource_uri(uri)
    except ResourceURIError:
        return False
    return True


def format_resource_uri(
    host: str,
    target: Iterable[str],
    segments: Iterable[str] = (),
) -> str:
    """Build a DMCP-03 §5.1 URI from parts, validating each component."""
    if host not in _RESOURCE_HOSTS:
        raise ResourceURIError(
            host, f"unknown resource host {host!r} (must be one of {sorted(_RESOURCE_HOSTS)})"
        )
    target_components = tuple(target)
    if len(target_components) < 2:
        raise ResourceURIError(
            ".".join(target_components),
            "resource URI target must have at least two dotted components",
        )
    for position, component in enumerate(target_components):
        _validate_prefix_component(component, position=position)

    segs = tuple(segments)
    for seg in segs:
        if not seg:
            raise ResourceURIError(seg, "empty path segment")
        if seg.startswith("{") and seg.endswith("}"):
            inner = seg[1:-1]
            if not inner or any(ch not in _LEAF_CHARS for ch in inner):
                raise ResourceURIError(
                    seg,
                    "placeholder must be {name} with name composed of ALPHA / DIGIT / '_'",
                )
    rendered = f"{_RESOURCE_SCHEME}://{host}/{'.'.join(target_components)}"
    if segs:
        rendered = rendered + "/" + "/".join(segs)
    return rendered
