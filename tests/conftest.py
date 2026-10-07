"""Shared fixtures: the fake Evolution transport, connections and the bound-client context.

Only Evolution's HTTP answers are faked (`tests/fakes.py`). Everything else (client, context, tools, policy) is real.

Usage:

    async def test_status(evo, bound):
        evo.on("GET", f"/instance/connectionState/{INSTANCE}", json={"instance": {"state": "open"}})
        with bound(evo) as client:
            ...
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import pytest

from evolution_api_mcp import client as evolution_client
from evolution_api_mcp import context, directory, ratelimit, registry, tools, toolsets
from tests.fakes import FakeEvolution

Connection = context.Connection
InstanceIdentity = context.InstanceIdentity
EvolutionClient = evolution_client.EvolutionClient

BASE = "http://evo.test"
INSTANCE = "inst"
TOKEN = "test-instance-token"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_runtime() -> Iterator[None]:
    """Every test starts and ends with empty rate-limit windows and name directory and every tool module imported."""
    tools.load_all()
    ratelimit.reset()
    directory.reset()
    yield
    ratelimit.reset()
    directory.reset()


@pytest.fixture
def evo() -> FakeEvolution:
    return FakeEvolution()


@pytest.fixture
def make_identity() -> Callable[..., InstanceIdentity]:
    def factory(integration: str = registry.BAILEYS) -> InstanceIdentity:
        return InstanceIdentity(name=INSTANCE, integration=integration)

    return factory


@pytest.fixture
def make_connection(make_identity: Callable[..., InstanceIdentity]) -> Callable[..., Connection]:
    """Build a Connection: local, standard, every toolset, no allow/deny limits, Baileys, no delay, no rate limits.

    Keyword overrides replace any `Connection` field, e.g. `make_connection(mode="hosted", policy="read")`.
    """

    def factory(**overrides: object) -> Connection:
        defaults: dict[str, object] = {
            "mode": "local",
            "policy": "standard",
            "toolsets": frozenset(toolsets.TOOLSET_ORDER),
            "allow": None,
            "deny": frozenset(),
            "deny_is_default": False,
            "irreversible_granted": True,
            "identity": make_identity(),
            "subject": None,
            "default_delay_ms": 0,
            "max_writes_per_minute": 0,
            "max_reads_per_minute": 0,
            "timezone": "UTC",
        }
        unknown = set(overrides) - set(defaults)
        if unknown:
            raise TypeError(f"unknown Connection field(s): {', '.join(sorted(unknown))}")
        return Connection(**{**defaults, **overrides})  # type: ignore[arg-type]

    return factory


@pytest.fixture
def bound(make_connection: Callable[..., Connection]) -> Callable[..., object]:
    """`bound(evo, connection=None, **overrides)`: context manager that makes `context.resolve()` return the
    connection and an `EvolutionClient` talking to `evo`; yields that client."""

    @contextmanager
    def binder(
        evo: FakeEvolution, connection: Connection | None = None, **overrides: object
    ) -> Iterator[EvolutionClient]:
        if connection is not None and overrides:
            connection = dataclasses.replace(connection, **overrides)  # type: ignore[arg-type]
        conn = connection if connection is not None else make_connection(**overrides)
        client = EvolutionClient(BASE, TOKEN, transport=evo)
        with context.override_for_tests(conn, client):
            yield client

    return binder
