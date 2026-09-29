"""The connection behind every call: local discovery and caching, listing, the hosted tenant, the test override."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import anyio
import httpx2
import pytest

from evolution_api_mcp import context, policy, tenant
from evolution_api_mcp.client import EvolutionClient
from evolution_api_mcp.config import LocalConfig
from evolution_api_mcp.errors import ToolExecutionError
from evolution_api_mcp.toolsets import DEFAULT_TOOLSETS
from tests.conftest import BASE, INSTANCE, TOKEN
from tests.fakes import FakeEvolution

ROOT = {"status": 200, "message": "Welcome to the Evolution API, it is working!", "version": "2.3.7"}
ROW = {"name": INSTANCE, "integration": "WHATSAPP-BUSINESS", "token": TOKEN}


@pytest.fixture(autouse=True)
def _no_local_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(context, "_local", None)


def program_instance(evo: FakeEvolution) -> None:
    evo.on("GET", "/", json=ROOT)
    evo.on("POST", "/verify-creds", status=401, json={"status": 401, "error": "Unauthorized"})
    evo.on("GET", "/instance/fetchInstances", json=[ROW])


def local_config(**overrides: object) -> LocalConfig:
    fields: dict[str, object] = {
        "base_url": BASE,
        "token": TOKEN,
        "toolsets": DEFAULT_TOOLSETS,
        "read_only": False,
        "allow": None,
        "deny": policy.DEFAULT_DENY,
        "deny_is_default": True,
        "irreversible_granted": False,
        "default_delay_ms": 1200,
        "max_writes_per_minute": 30,
        "file_roots": (),
        "download_dir": Path("/tmp/evolution-downloads"),
    }
    return LocalConfig(**{**fields, **overrides})  # type: ignore[arg-type]


def configure(evo: httpx2.AsyncBaseTransport, **overrides: object) -> list[EvolutionClient]:
    """Configure the local side with a client factory that talks to `evo`; returns the clients it built."""
    built: list[EvolutionClient] = []

    def factory(base_url: str, token: str) -> EvolutionClient:
        built.append(EvolutionClient(base_url, token, transport=evo))
        return built[-1]

    context.configure_local(local_config(**overrides), client_factory=factory)
    return built


def discovery_calls(evo: FakeEvolution) -> int:
    return sum(1 for r in evo.requests if r.method == "GET" and r.path == "/")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("overrides", "named"),
    [
        ({"base_url": None}, "EVOLUTION_API_URL"),
        ({"token": None}, "EVOLUTION_INSTANCE_TOKEN"),
    ],
)
async def test_missing_credentials_name_only_the_missing_variable(
    evo: FakeEvolution, overrides: dict[str, object], named: str
) -> None:
    configure(evo, **overrides)

    with pytest.raises(ToolExecutionError) as info:
        await context.resolve()

    assert str(info.value) == (
        f"Missing Evolution credentials: {named}. Set them in this server's environment. Nothing was sent."
    )
    assert evo.requests == []


@pytest.mark.anyio
async def test_both_credentials_missing_are_named_together(evo: FakeEvolution) -> None:
    configure(evo, base_url=None, token=None)

    with pytest.raises(ToolExecutionError) as info:
        await context.resolve()

    assert str(info.value) == (
        "Missing Evolution credentials: EVOLUTION_API_URL, EVOLUTION_INSTANCE_TOKEN. "
        "Set them in this server's environment. Nothing was sent."
    )


@pytest.mark.anyio
async def test_resolve_discovers_once_and_builds_the_local_connection(evo: FakeEvolution) -> None:
    program_instance(evo)
    built = configure(evo, allow=frozenset({"set_presence"}), default_delay_ms=800, max_writes_per_minute=7)

    conn, client = await context.resolve()
    again, same_client = await context.resolve()

    assert conn == again
    assert client is same_client is built[0]
    assert len(built) == 1
    assert discovery_calls(evo) == 1
    assert conn.mode == "local"
    assert conn.policy == "standard"
    assert conn.identity == context.InstanceIdentity(INSTANCE, "WHATSAPP-BUSINESS")
    assert conn.subject is None
    assert conn.toolsets == DEFAULT_TOOLSETS
    assert conn.allow == frozenset({"set_presence"})
    assert conn.deny == policy.DEFAULT_DENY
    assert conn.deny_is_default is True
    assert conn.irreversible_granted is False
    assert (conn.default_delay_ms, conn.max_writes_per_minute, conn.max_reads_per_minute) == (800, 7, 0)


@pytest.mark.anyio
async def test_read_only_configuration_yields_the_read_policy(evo: FakeEvolution) -> None:
    program_instance(evo)
    configure(evo, read_only=True)

    conn, _ = await context.resolve()

    assert conn.policy == "read"


@pytest.mark.anyio
async def test_concurrent_first_calls_share_one_discovery(evo: FakeEvolution) -> None:
    program_instance(evo)
    configure(evo)
    results: list[context.Connection] = []

    async def call() -> None:
        results.append((await context.resolve())[0])

    async with anyio.create_task_group() as group:
        for _ in range(4):
            group.start_soon(call)

    assert len(results) == 4
    assert discovery_calls(evo) == 1


@pytest.mark.anyio
async def test_a_refusal_is_cached_and_reraised_with_nothing_sent(evo: FakeEvolution) -> None:
    evo.on("GET", "/", json=ROOT)
    evo.on("POST", "/verify-creds", json={"status": 200})
    configure(evo)
    expected = (
        "This is the Evolution server's global AUTHENTICATION_API_KEY, which controls every instance on the server. "
        "Use the instance's own token instead. Nothing was sent."
    )

    for _ in range(2):
        with pytest.raises(ToolExecutionError) as info:
            await context.resolve()
        assert str(info.value) == expected

    assert discovery_calls(evo) == 1


@pytest.mark.anyio
async def test_an_unreachable_server_is_not_cached(evo: FakeEvolution) -> None:
    evo.fail("GET", "/", httpx2.ConnectError("connection refused"))
    configure(evo)

    with pytest.raises(ToolExecutionError) as info:
        await context.resolve()
    assert "Could not reach" in str(info.value)
    assert not context.listing_settled()

    program_instance(evo)
    conn, _ = await context.resolve()

    assert conn.identity is not None
    assert discovery_calls(evo) == 2


@pytest.mark.anyio
async def test_a_server_error_is_not_cached(evo: FakeEvolution) -> None:
    evo.on("GET", "/", status=502, json={"message": "bad gateway"})
    configure(evo)

    with pytest.raises(ToolExecutionError):
        await context.resolve()

    program_instance(evo)
    conn, _ = await context.resolve()

    assert conn.identity is not None


@pytest.mark.anyio
async def test_listing_without_credentials_answers_at_once_without_identity(evo: FakeEvolution) -> None:
    configure(evo, token=None)

    conn = await context.connection_for_listing()

    assert conn.identity is None
    assert conn.toolsets == DEFAULT_TOOLSETS
    assert context.listing_settled()
    assert evo.requests == []


@pytest.mark.anyio
async def test_listing_discovers_the_identity_and_settles(evo: FakeEvolution) -> None:
    program_instance(evo)
    configure(evo)
    assert not context.listing_settled()

    conn = await context.connection_for_listing()

    assert conn.identity == context.InstanceIdentity(INSTANCE, "WHATSAPP-BUSINESS")
    assert context.listing_settled()
    assert (await context.resolve())[0].identity == conn.identity
    assert discovery_calls(evo) == 1


@pytest.mark.anyio
async def test_listing_survives_a_refusal_and_settles(evo: FakeEvolution) -> None:
    evo.on("GET", "/", json=ROOT)
    evo.on("POST", "/verify-creds", json={"status": 200})
    configure(evo)

    conn = await context.connection_for_listing()

    assert conn.identity is None
    assert context.listing_settled()


@pytest.mark.anyio
async def test_listing_survives_an_unreachable_server_and_stays_unsettled(evo: FakeEvolution) -> None:
    evo.fail("GET", "/", httpx2.ConnectError("connection refused"))
    configure(evo)

    conn = await context.connection_for_listing()

    assert conn.identity is None
    assert not context.listing_settled()


class GatedTransport(httpx2.AsyncBaseTransport):
    """Holds every request until `gate` is set, then lets the fake Evolution answer it."""

    def __init__(self, inner: FakeEvolution) -> None:
        self.inner = inner
        self.gate = asyncio.Event()

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        await self.gate.wait()
        return await self.inner.handle_async_request(request)


@pytest.mark.anyio
async def test_listing_returns_without_identity_when_discovery_is_slow_and_picks_it_up_later(
    evo: FakeEvolution,
) -> None:
    program_instance(evo)
    slow = GatedTransport(evo)
    configure(slow)

    early = await context.connection_for_listing(wait_seconds=0.05)

    assert early.identity is None
    assert not context.listing_settled()

    slow.gate.set()
    late = await context.connection_for_listing(wait_seconds=5)

    assert late.identity == context.InstanceIdentity(INSTANCE, "WHATSAPP-BUSINESS")
    assert discovery_calls(evo) == 1


def test_local_config_requires_configuration() -> None:
    with pytest.raises(RuntimeError):
        context.local_config()

    config = local_config()
    context.configure_local(config)

    assert context.local_config() is config


def test_instance_path_quotes_every_dynamic_part() -> None:
    identity = context.InstanceIdentity("my shop/1", "WHATSAPP-BAILEYS")

    assert context.instance_path(identity, "message/sendText") == "/message/sendText/my%20shop%2F1"
    assert context.instance_path(identity, "typebot/fetch", "bot 1/x") == "/typebot/fetch/bot%201%2Fx/my%20shop%2F1"


@pytest.mark.anyio
async def test_override_pins_the_connection_only_inside_the_block(
    evo: FakeEvolution, make_connection: Callable[..., context.Connection]
) -> None:
    conn = make_connection(policy="read")
    client = EvolutionClient(BASE, TOKEN, transport=evo)

    with context.override_for_tests(conn, client):
        assert await context.resolve() == (conn, client)
        assert await context.connection_for_listing() is conn
        assert context.listing_settled()

    with pytest.raises(RuntimeError):
        await context.resolve()


@pytest.mark.anyio
async def test_hosted_connection_follows_the_bound_tenant(monkeypatch: pytest.MonkeyPatch, evo: FakeEvolution) -> None:
    bound = tenant.Tenant(
        subject="t_abc",
        base_url=BASE,
        token=TOKEN,
        instance_name="shop",
        integration="WHATSAPP-BUSINESS",
        policy="read",
        toolsets=frozenset({"chats", "templates"}),
    )
    client = EvolutionClient(BASE, TOKEN, transport=evo)
    monkeypatch.setattr(tenant, "client_for", lambda t: client if t is bound else None)
    token = tenant.bind(bound)
    try:
        conn, resolved_client = await context.resolve()
        listing = await context.connection_for_listing()
        settled = context.listing_settled()
    finally:
        tenant.reset(token)

    assert resolved_client is client
    assert listing == conn
    assert settled
    assert conn == context.Connection(
        mode="hosted",
        policy="read",
        toolsets=frozenset({"chats", "templates"}),
        allow=None,
        deny=policy.DEFAULT_DENY,
        deny_is_default=True,
        irreversible_granted=False,
        identity=context.InstanceIdentity("shop", "WHATSAPP-BUSINESS"),
        subject="t_abc",
        default_delay_ms=1200,
        max_writes_per_minute=30,
        max_reads_per_minute=120,
    )
