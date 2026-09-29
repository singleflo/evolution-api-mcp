"""The one seam that makes the server multi-tenant.

A request arriving over streamable HTTP carries an authenticated subject; the hosted app's `BindTenant` middleware
binds a `Tenant` for that message and everything that depends on the caller reads it back through `current()`
(`context.resolve` builds the connection and the client from it). Two things follow the tenant instead of the
process environment:

  * the Evolution client: `client_for()` mints one client per (subject, URL, token) for the process' lifetime,
    over a transport that refuses non-public addresses at connect time (the SSRF guard);
  * the gate's inputs: policy and enabled toolsets come from the tenant's consent.

`policy` mirrors the two postures the consent page offers: `read` is the local `EVOLUTION_MCP_ALLOW=none`,
`standard` is the package defaults (`*` with DEFAULT_DENY). Irreversible and local-only tools are never part of
either hosted posture.
"""

import asyncio
import hashlib
import threading
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Literal

from evolution_api_mcp import netguard
from evolution_api_mcp.client import EvolutionClient


@dataclass(frozen=True)
class Tenant:
    """One authenticated caller's connection to one Evolution instance."""

    subject: str
    base_url: str
    token: str
    instance_name: str
    integration: str
    policy: Literal["read", "standard"]
    toolsets: frozenset[str]

    def __post_init__(self) -> None:
        # Imported here: registry imports context, which imports this module.
        from evolution_api_mcp import registry
        from evolution_api_mcp.toolsets import TOOLSET_ORDER

        if self.policy not in ("read", "standard"):
            raise ValueError(f"Tenant policy must be 'read' or 'standard', got {self.policy!r}.")
        unknown = sorted(set(self.toolsets) - set(TOOLSET_ORDER))
        if unknown:
            raise ValueError(f"Tenant toolsets must be among {', '.join(TOOLSET_ORDER)}; got unknown {unknown}.")
        if self.integration not in registry.ALL:
            valid = ", ".join(sorted(registry.ALL))
            raise ValueError(f"Tenant integration must be one of {valid}, got {self.integration!r}.")


_current: ContextVar[Tenant | None] = ContextVar("evolution_tenant", default=None)


def current() -> Tenant | None:
    """The tenant bound for this request: None on the local (stdio) path."""
    return _current.get()


def bind(tenant: Tenant) -> Token:
    """Bind for the current request context; hand back the token for `reset`."""
    return _current.set(tenant)


def reset(token: Token) -> None:
    """Undo one `bind`: the request's context, not the process, forgets it."""
    _current.reset(token)


_allow_private_targets: bool | None = None
_public_url: str | None = None


def configure(*, allow_private_targets: bool, public_url: str) -> None:
    """Fix the hosted deployment's settings once at startup: the SSRF stance and the public origin."""
    global _allow_private_targets, _public_url
    _allow_private_targets = allow_private_targets
    _public_url = public_url.rstrip("/")


def public_url() -> str:
    """The hosted public URL (no trailing slash). Raises when the hosted app has not configured it."""
    if _public_url is None:
        raise RuntimeError("The hosted public URL is not configured; call tenant.configure first.")
    return _public_url


_clients: dict[tuple[str, str, str], EvolutionClient] = {}
_clients_lock = threading.Lock()
_closing: set[asyncio.Task] = set()


def client_for(tenant: Tenant) -> EvolutionClient:
    """The one client for this subject, created on first use.

    The base URL and a hash of the token are part of the identity, so a changed consent cannot reuse a live
    connection to the old server or token. The read outside the lock keeps the common call lock-free.
    """
    key = (tenant.subject, tenant.base_url, hashlib.sha256(tenant.token.encode()).hexdigest())
    existing = _clients.get(key)
    if existing is not None:
        return existing
    if _allow_private_targets is None:
        raise RuntimeError("Hosted settings are not configured; call tenant.configure first.")
    with _clients_lock:
        if key not in _clients:
            _clients[key] = EvolutionClient(
                tenant.base_url,
                tenant.token,
                transport=netguard.guarded_transport(allow_private=_allow_private_targets),
            )
        return _clients[key]


def forget(subject: str) -> None:
    """Drop every client cached for one tenant subject and schedule closing their connections."""
    with _clients_lock:
        dropped = [_clients.pop(key) for key in [key for key in _clients if key[0] == subject]]
    for client in dropped:
        try:
            task = asyncio.get_running_loop().create_task(client.aclose())
        except RuntimeError:
            asyncio.run(client.aclose())
        else:
            _closing.add(task)
            task.add_done_callback(_closing.discard)
