"""The tool registry: one `ToolSpec` per tool, hints derived from the tool's kind, one gate in front of every call.

Tool modules decorate their functions with `@tool(...)`. The decorator records the spec and returns a wrapper that
resolves the connection, applies the safety policy and the rate limit, then runs the function. `register_all` mounts
the wrappers on an `MCPServer` with annotations derived from the specs, never hand-set.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, TypeVar

from mcp_types import ToolAnnotations

from evolution_api_mcp.errors import ToolExecutionError
from evolution_api_mcp.toolsets import TOOLSET_ORDER

if TYPE_CHECKING:
    from mcp.server import MCPServer

Kind = Literal["read", "write", "destructive", "irreversible"]

BAILEYS = "WHATSAPP-BAILEYS"
BUSINESS = "WHATSAPP-BUSINESS"
EVOLUTION = "EVOLUTION"
ALL = frozenset({BAILEYS, BUSINESS, EVOLUTION})

_KINDS = ("read", "write", "destructive", "irreversible")

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


@dataclass(frozen=True)
class ToolSpec:
    name: str
    title: str
    toolset: str
    kind: Kind
    idempotent: bool
    integrations: frozenset[str] = ALL
    local_only: bool = False
    universal: bool = False


@dataclass(frozen=True)
class _Entry:
    spec: ToolSpec
    wrapper: Callable[..., Awaitable[Any]]
    fn: Callable[..., Awaitable[Any]]


_REGISTRY: dict[str, _Entry] = {}


def tool(
    *,
    title: str,
    toolset: str,
    kind: Kind,
    idempotent: bool,
    integrations: frozenset[str] = ALL,
    local_only: bool = False,
    universal: bool = False,
) -> Callable[[F], F]:
    """Register an async tool function under its own name and return the gated wrapper."""
    if toolset not in TOOLSET_ORDER:
        raise ValueError(f"Unknown toolset '{toolset}'. Valid: {', '.join(TOOLSET_ORDER)}.")
    if kind not in _KINDS:
        raise ValueError(f"Unknown tool kind '{kind}'. Valid: {', '.join(_KINDS)}.")
    if not integrations or not integrations <= ALL:
        raise ValueError(f"integrations must be a non-empty subset of {sorted(ALL)}.")

    def decorate(fn: F) -> F:
        spec = ToolSpec(
            name=fn.__name__,
            title=title,
            toolset=toolset,
            kind=kind,
            idempotent=idempotent,
            integrations=integrations,
            local_only=local_only,
            universal=universal,
        )
        if spec.name in _REGISTRY:
            raise ValueError(f"Tool '{spec.name}' is already registered.")

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Imported here: context and policy import this module.
            from evolution_api_mcp import clock, context, policy, ratelimit

            conn, _ = await context.resolve()
            reason = policy.refusal(spec, conn)
            if reason:
                raise ToolExecutionError(reason)
            ratelimit.check(spec, conn)
            with clock.displayed_in(conn.timezone):
                return await fn(*args, **kwargs)

        try:
            # The SDK derives the input schema from `inspect.signature(func, eval_str=True)`, which follows
            # `__wrapped__` and resolves string annotations against the tool's own module. A pre-set signature is
            # returned as is, so it is only pinned when its annotations resolve now (forward references to models
            # defined below the tool fall back to the lazy `__wrapped__` path).
            wrapper.__signature__ = inspect.signature(fn, eval_str=True)  # type: ignore[attr-defined]
        except NameError:
            pass

        _REGISTRY[spec.name] = _Entry(spec, wrapper, fn)
        return wrapper  # type: ignore[return-value]

    return decorate


def specs() -> list[ToolSpec]:
    """Every registered spec, ordered by toolset (TOOLSET_ORDER) then name, so tool order is deterministic."""
    return sorted(
        (entry.spec for entry in _REGISTRY.values()),
        key=lambda spec: (TOOLSET_ORDER.index(spec.toolset), spec.name),
    )


def get(name: str) -> ToolSpec:
    """The spec of a registered tool; `KeyError` for an unknown name."""
    return _REGISTRY[name].spec


def annotations_for(spec: ToolSpec) -> ToolAnnotations:
    """MCP tool hints derived from the kind. Every tool acts on an external server, so openWorldHint is always true."""
    return ToolAnnotations(
        title=spec.title,
        read_only_hint=spec.kind == "read",
        destructive_hint=spec.kind in ("destructive", "irreversible"),
        idempotent_hint=spec.idempotent,
        open_world_hint=True,
    )


def register_all(mcp: MCPServer, *, mode: Literal["local", "hosted"]) -> list[str]:
    """Mount every tool on `mcp` in deterministic order (skipping local-only tools when hosted); return the names."""
    from evolution_api_mcp import tools

    tools.load_all()
    registered: list[str] = []
    for spec in specs():
        if spec.local_only and mode == "hosted":
            continue
        entry = _REGISTRY[spec.name]
        mcp.add_tool(
            entry.wrapper,
            name=spec.name,
            title=spec.title,
            description=inspect.getdoc(entry.fn),
            annotations=annotations_for(spec),
            structured_output=False,
        )
        registered.append(spec.name)
    return registered
