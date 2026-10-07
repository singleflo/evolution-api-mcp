"""The connection every tool call runs against: who is calling, which instance, which gate settings, which client.

Two sources, one shape (`Connection`):

  * local (stdio): the process configuration (`configure_local`) plus one lazily created `EvolutionClient`. The
    instance identity is discovered from the token on first use (`discovery.discover`) and cached, both when it
    succeeds and when discovery REFUSES the credentials (a refusal is a fact about the token, not a transient
    failure). Transport failures are never cached.
  * hosted: the `Tenant` the remote middleware bound for this message (`tenant.current()`); identity comes from
    the consent verification stored with it, the client from `tenant.client_for`.

Test seams:

  * `override_for_tests(connection, client)` pins both for the current context; `resolve()` and
    `connection_for_listing()` consult it first.
  * `configure_local(config, client_factory=...)` replaces the way the local client is built. The factory gets
    `(base_url, token)` and returns an `EvolutionClient`; tests pass one that injects a fake `httpx2` transport.

Import notes: this module is imported by `registry` and `policy`, so it imports `config` only for typing and
`discovery`, `policy` lazily inside functions.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal
from urllib.parse import quote

import anyio

from evolution_api_mcp import tenant
from evolution_api_mcp.client import EvolutionClient, EvolutionError
from evolution_api_mcp.errors import ToolExecutionError, raise_evolution_failure

if TYPE_CHECKING:
    from evolution_api_mcp.config import LocalConfig
    from evolution_api_mcp.discovery import DiscoveryRefused

logger = logging.getLogger(__name__)

HOSTED_DEFAULT_DELAY_MS = 1200
HOSTED_MAX_WRITES_PER_MINUTE = 30
HOSTED_MAX_READS_PER_MINUTE = 120


@dataclass(frozen=True)
class InstanceIdentity:
    name: str
    integration: str


@dataclass(frozen=True)
class Connection:
    mode: Literal["local", "hosted"]
    policy: Literal["read", "standard"]
    toolsets: frozenset[str]
    allow: frozenset[str] | None
    deny: frozenset[str]
    deny_is_default: bool
    irreversible_granted: bool
    identity: InstanceIdentity | None
    subject: str | None
    default_delay_ms: int
    max_writes_per_minute: int
    max_reads_per_minute: int
    timezone: str = "UTC"


ClientFactory = Callable[[str, str], EvolutionClient]


def _default_client_factory(base_url: str, token: str) -> EvolutionClient:
    return EvolutionClient(base_url, token)


@dataclass
class _LocalState:
    config: LocalConfig
    client_factory: ClientFactory
    lock: anyio.Lock = field(default_factory=anyio.Lock)
    client: EvolutionClient | None = None
    identity: InstanceIdentity | None = None
    refusal: DiscoveryRefused | None = None
    listing_task: asyncio.Task | None = None


_local: _LocalState | None = None
_override: ContextVar[tuple[Connection, object] | None] = ContextVar("evolution_connection_override", default=None)


def configure_local(config: LocalConfig, *, client_factory: ClientFactory | None = None) -> None:
    """Install the local configuration and reset everything cached from a previous one."""
    global _local
    _local = _LocalState(config=config, client_factory=client_factory or _default_client_factory)


def local_config() -> LocalConfig:
    """The installed local configuration. Raises RuntimeError before `configure_local`."""
    if _local is None:
        raise RuntimeError("Local configuration is not loaded; call context.configure_local first.")
    return _local.config


@contextmanager
def override_for_tests(connection: Connection, client: object) -> Iterator[None]:
    """Pin `connection` and `client` as the answer of `resolve()` within this block."""
    token = _override.set((connection, client))
    try:
        yield
    finally:
        _override.reset(token)


def instance_path(identity: InstanceIdentity, endpoint: str, *segments: str) -> str:
    """`/<endpoint>/<segment>.../<instance>` with every dynamic part URL-quoted."""
    parts = [endpoint.strip("/"), *(quote(segment, safe="") for segment in segments), quote(identity.name, safe="")]
    return "/" + "/".join(parts)


def _local_state() -> _LocalState:
    if _local is None:
        raise RuntimeError("Local configuration is not loaded; call context.configure_local first.")
    return _local


def _missing_credentials(config: LocalConfig) -> list[str]:
    missing = []
    if not config.base_url:
        missing.append("EVOLUTION_API_URL")
    if not config.token:
        missing.append("EVOLUTION_INSTANCE_TOKEN")
    return missing


def _local_connection(state: _LocalState) -> Connection:
    config = state.config
    return Connection(
        mode="local",
        policy="read" if config.read_only else "standard",
        toolsets=config.toolsets,
        allow=config.allow,
        deny=config.deny,
        deny_is_default=config.deny_is_default,
        irreversible_granted=config.irreversible_granted,
        identity=state.identity,
        subject=None,
        default_delay_ms=config.default_delay_ms,
        max_writes_per_minute=config.max_writes_per_minute,
        max_reads_per_minute=0,
        timezone=config.timezone,
    )


def _hosted_connection(bound: tenant.Tenant) -> Connection:
    from evolution_api_mcp import policy

    return Connection(
        mode="hosted",
        policy=bound.policy,
        toolsets=bound.toolsets,
        allow=None,
        deny=policy.DEFAULT_DENY,
        deny_is_default=True,
        irreversible_granted=False,
        identity=InstanceIdentity(bound.instance_name, bound.integration),
        subject=bound.subject,
        default_delay_ms=HOSTED_DEFAULT_DELAY_MS,
        max_writes_per_minute=HOSTED_MAX_WRITES_PER_MINUTE,
        max_reads_per_minute=HOSTED_MAX_READS_PER_MINUTE,
    )


def _refusal_error(refusal: DiscoveryRefused) -> ToolExecutionError:
    return ToolExecutionError(f"{refusal.message} Nothing was sent.")


def _local_client(state: _LocalState) -> EvolutionClient:
    if state.client is None:
        state.client = state.client_factory(state.config.base_url or "", state.config.token or "")
    return state.client


async def _ensure_identity(state: _LocalState) -> EvolutionClient:
    """Make the local identity known (discovering it once under the lock) and return the client.

    Raises `ToolExecutionError` for missing credentials, a cached or fresh refusal, and transient failures.
    """
    from evolution_api_mcp import discovery

    missing = _missing_credentials(state.config)
    if missing:
        raise ToolExecutionError(
            f"Missing Evolution credentials: {', '.join(missing)}. Set them in this server's environment. "
            "Nothing was sent."
        )
    if state.identity is not None:
        return _local_client(state)
    if state.refusal is not None:
        raise _refusal_error(state.refusal)
    async with state.lock:
        if state.identity is not None:
            return _local_client(state)
        if state.refusal is not None:
            raise _refusal_error(state.refusal)
        client = _local_client(state)
        try:
            state.identity = await discovery.discover(client)
        except discovery.DiscoveryRefused as refusal:
            state.refusal = refusal
            raise _refusal_error(refusal) from refusal
        except EvolutionError as exc:
            await raise_evolution_failure(exc, phase="before_mutation")
        return client


async def resolve() -> tuple[Connection, EvolutionClient]:
    """The connection and client for this call; the identity is known on return.

    Raises `ToolExecutionError` when it cannot be established (missing credentials, refused credentials,
    unreachable server).
    """
    pinned = _override.get()
    if pinned is not None:
        return pinned[0], pinned[1]  # type: ignore[return-value]
    bound = tenant.current()
    if bound is not None:
        return _hosted_connection(bound), tenant.client_for(bound)
    state = _local_state()
    client = await _ensure_identity(state)
    return _local_connection(state), client


async def _settle_quietly(state: _LocalState) -> None:
    try:
        await _ensure_identity(state)
    except ToolExecutionError as exc:
        logger.debug("Instance discovery for the tool listing did not settle: %s", exc)
    except Exception:
        logger.exception("Instance discovery for the tool listing failed unexpectedly")


async def connection_for_listing(wait_seconds: float = 3.0) -> Connection:
    """The connection for `tools/list`; never raises for credential or discovery problems.

    `identity` is None when credentials are missing, discovery is refused or fails, or it takes longer than
    `wait_seconds`. Discovery keeps running in the background after a timeout, so a later listing sees the result.
    """
    pinned = _override.get()
    if pinned is not None:
        return pinned[0]
    bound = tenant.current()
    if bound is not None:
        return _hosted_connection(bound)
    state = _local_state()
    if state.identity is None and state.refusal is None and not _missing_credentials(state.config):
        task = state.listing_task
        if task is None or task.done():
            task = state.listing_task = asyncio.ensure_future(_settle_quietly(state))
        await asyncio.wait({task}, timeout=wait_seconds)
    return _local_connection(state)


def listing_settled() -> bool:
    """True once the tool listing no longer depends on discovery: identity known, credentials missing or a
    refusal cached (or a connection is pinned or bound)."""
    if _override.get() is not None or tenant.current() is not None:
        return True
    state = _local_state()
    return state.identity is not None or state.refusal is not None or bool(_missing_credentials(state.config))
