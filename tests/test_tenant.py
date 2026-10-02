"""The tenant seam: one authenticated caller decides the connection, the client and the gate's inputs.

`tenant.py` carries the hosted server's whole notion of "who is calling": a contextvar bound per JSON-RPC message, a
client cache keyed by everything that makes a connection distinct, and the two deployment settings the hosted app
fixes at startup. The isolation tests here are the adversarial core: a stale binding or a shared client is a
cross-tenant leak.
"""

from __future__ import annotations

import asyncio
import contextvars
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from evolution_api_mcp import context, registry, tenant
from evolution_api_mcp.client import EvolutionUnreachable
from evolution_api_mcp.toolsets import TOOLSET_ORDER


@pytest.fixture(autouse=True)
def _isolated_tenant_state(monkeypatch: pytest.MonkeyPatch):
    """Empty client cache, hosted settings configured for public targets only, nothing bound before or after."""
    monkeypatch.setattr(tenant, "_clients", {})
    monkeypatch.setattr(tenant, "_allow_private_targets", False)
    monkeypatch.setattr(tenant, "_public_url", "https://mcp.example.test")
    tenant._current.set(None)
    yield
    tenant._current.set(None)


def make_tenant(subject: str = "t_a", **overrides: object) -> tenant.Tenant:
    fields: dict[str, object] = {
        "subject": subject,
        "base_url": "https://evo-a.example.test",
        "token": "token-a",
        "instance_name": "inst-a",
        "integration": registry.BUSINESS,
        "policy": "standard",
        "toolsets": frozenset({"messaging", "chats"}),
    }
    fields.update(overrides)
    return tenant.Tenant(**fields)  # type: ignore[arg-type]


# ------------------------------------------------------------ construction
def test_a_policy_other_than_read_or_standard_is_refused() -> None:
    with pytest.raises(ValueError, match="'read' or 'standard'"):
        make_tenant(policy="readonly")


def test_an_unknown_toolset_is_refused_naming_it() -> None:
    with pytest.raises(ValueError, match="unknown.*flying"):
        make_tenant(toolsets=frozenset({"messaging", "flying"}))


def test_an_unknown_integration_is_refused() -> None:
    with pytest.raises(ValueError, match="integration must be one of"):
        make_tenant(integration="WHATSAPP-WEB")


def test_every_documented_integration_and_toolset_is_accepted() -> None:
    for integration in registry.ALL:
        accepted = make_tenant(integration=integration, toolsets=frozenset(TOOLSET_ORDER))
        assert accepted.integration == integration


# ------------------------------------------------------- bind, reset, current
def test_current_is_none_until_bound_and_again_after_reset() -> None:
    assert tenant.current() is None
    bound = make_tenant()
    token = tenant.bind(bound)
    assert tenant.current() is bound
    tenant.reset(token)
    assert tenant.current() is None


def test_a_nested_bind_is_undone_one_level_at_a_time() -> None:
    outer, inner = make_tenant("t_outer"), make_tenant("t_inner")
    outer_token = tenant.bind(outer)
    inner_token = tenant.bind(inner)
    assert tenant.current() is inner
    tenant.reset(inner_token)
    assert tenant.current() is outer
    tenant.reset(outer_token)
    assert tenant.current() is None


@pytest.mark.anyio
async def test_concurrent_requests_each_see_only_their_own_tenant() -> None:
    """Two tasks bind different tenants and interleave at await points: each keeps its own binding."""
    seen: dict[str, str | None] = {}
    both_bound = asyncio.Event()
    bound_count = 0

    async def request(name: str) -> None:
        nonlocal bound_count
        token = tenant.bind(make_tenant(name))
        try:
            bound_count += 1
            if bound_count == 2:
                both_bound.set()
            await both_bound.wait()
            current = tenant.current()
            seen[name] = current.subject if current else None
        finally:
            tenant.reset(token)

    await asyncio.gather(
        asyncio.create_task(request("t_alice")),
        asyncio.create_task(request("t_bob")),
    )

    assert seen == {"t_alice": "t_alice", "t_bob": "t_bob"}
    assert tenant.current() is None


def test_a_binding_does_not_leak_into_another_thread() -> None:
    token = tenant.bind(make_tenant())
    try:
        seen: list[tenant.Tenant | None] = []
        thread = threading.Thread(target=lambda: seen.append(tenant.current()))
        thread.start()
        thread.join()
    finally:
        tenant.reset(token)
    assert seen == [None]


def test_a_thread_started_with_a_copied_context_sees_the_binding() -> None:
    """The SDK copies the request context into worker threads; a tenant bound before must travel with it."""
    bound = make_tenant()
    token = tenant.bind(bound)
    try:
        seen: list[tenant.Tenant | None] = []
        ctx = contextvars.copy_context()
        thread = threading.Thread(target=lambda: seen.append(ctx.run(tenant.current)))
        thread.start()
        thread.join()
    finally:
        tenant.reset(token)
    assert seen == [bound]


# -------------------------------------------------------------- configure
def test_public_url_drops_the_trailing_slash() -> None:
    tenant.configure(allow_private_targets=False, public_url="https://evolution-mcp.example.test/")
    assert tenant.public_url() == "https://evolution-mcp.example.test"


def test_public_url_before_configure_says_what_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tenant, "_public_url", None)
    with pytest.raises(RuntimeError, match="tenant.configure"):
        tenant.public_url()


def test_client_for_before_configure_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tenant, "_allow_private_targets", None)
    with pytest.raises(RuntimeError, match="tenant.configure"):
        tenant.client_for(make_tenant())


# ------------------------------------------------------ one client per identity
def test_client_for_is_cached_per_subject_url_and_token() -> None:
    first = tenant.client_for(make_tenant())
    assert tenant.client_for(make_tenant()) is first  # an equal tenant rebuilt from the store
    assert first.base_url == "https://evo-a.example.test"
    assert first.token == "token-a"


@pytest.mark.parametrize(
    "overrides",
    [
        {"subject": "t_other"},
        {"base_url": "https://evo-b.example.test"},
        {"token": "token-b"},
    ],
    ids=["other subject", "other server", "other token"],
)
def test_any_change_of_subject_server_or_token_gets_a_new_client(overrides: dict[str, str]) -> None:
    """A re-consent with another URL or token must never reuse a live connection to the old one."""
    first = tenant.client_for(make_tenant())
    second = tenant.client_for(make_tenant(**overrides))
    assert second is not first
    assert (second.base_url, second.token) == (
        overrides.get("base_url", first.base_url),
        overrides.get("token", first.token),
    )


def test_a_changed_policy_or_toolset_choice_keeps_the_same_connection() -> None:
    """Policy and toolsets steer the gate, not the wire: the client identity ignores them."""
    first = tenant.client_for(make_tenant(policy="standard"))
    assert tenant.client_for(make_tenant(policy="read", toolsets=frozenset({"instance"}))) is first


def test_the_cache_key_holds_a_hash_of_the_token_not_the_token() -> None:
    tenant.client_for(make_tenant(token="a-very-secret-instance-token"))
    keys = list(tenant._clients)
    assert len(keys) == 1
    assert "a-very-secret-instance-token" not in "".join(keys[0])


def test_two_threads_asking_for_the_same_tenant_share_one_client() -> None:
    results: list[object] = []
    barrier = threading.Barrier(8)

    def ask() -> None:
        barrier.wait()
        results.append(tenant.client_for(make_tenant()))

    threads = [threading.Thread(target=ask) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 8
    assert len({id(client) for client in results}) == 1
    assert len(tenant._clients) == 1


# ------------------------------------------------------------------ forget
@pytest.mark.anyio
async def test_forget_drops_only_that_subjects_clients_and_closes_them() -> None:
    kept = tenant.client_for(make_tenant("t_keep"))
    dropped_a = tenant.client_for(make_tenant("t_drop"))
    dropped_b = tenant.client_for(make_tenant("t_drop", token="token-rotated"))

    tenant.forget("t_drop")
    await asyncio.gather(*list(tenant._closing))

    assert tenant.client_for(make_tenant("t_keep")) is kept
    assert tenant.client_for(make_tenant("t_drop")) is not dropped_a
    assert dropped_a._http.is_closed
    assert dropped_b._http.is_closed
    assert not kept._http.is_closed


def test_forget_outside_an_event_loop_still_closes_the_clients() -> None:
    dropped = tenant.client_for(make_tenant("t_sync"))

    tenant.forget("t_sync")

    assert dropped._http.is_closed
    assert tenant._clients == {}


def test_forget_of_an_unknown_subject_is_a_no_op() -> None:
    kept = tenant.client_for(make_tenant())
    tenant.forget("t_nobody")
    assert tenant.client_for(make_tenant()) is kept


def test_store_delete_tenant_also_forgets_its_live_client(tmp_path) -> None:
    pytest.importorskip("cryptography.fernet")
    from cryptography.fernet import Fernet

    from evolution_api_mcp.remote.store import Store

    connected = make_tenant("t_delete")
    store = Store(tmp_path / "remote.db", Fernet.generate_key().decode())
    store.init()
    store.put_tenant(connected)
    first = tenant.client_for(connected)

    store.delete_tenant("t_delete")

    assert tenant.client_for(connected) is not first
    assert first._http.is_closed


# ------------------------------------------- the seam the tools actually read
def test_context_builds_the_hosted_connection_from_the_bound_tenant() -> None:
    """Policy, toolsets and identity come from consent; deny, irreversible grant, pacing are the hosted constants."""
    bound = make_tenant(policy="read", toolsets=frozenset({"chats"}), integration=registry.BUSINESS)
    token = tenant.bind(bound)
    try:
        conn = asyncio.run(context.connection_for_listing())
    finally:
        tenant.reset(token)

    assert conn.mode == "hosted"
    assert conn.policy == "read"
    assert conn.toolsets == frozenset({"chats"})
    assert conn.identity == context.InstanceIdentity("inst-a", registry.BUSINESS)
    assert conn.subject == "t_a"
    assert conn.irreversible_granted is False
    assert (conn.default_delay_ms, conn.max_writes_per_minute, conn.max_reads_per_minute) == (1200, 30, 120)


@asynccontextmanager
async def _http_listener() -> AsyncIterator[int]:
    """A real server on 127.0.0.1 answering every request with `{}`; yields its port."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: 2\r\n\r\n{}"
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        yield server.sockets[0].getsockname()[1]
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.anyio
async def test_the_hosted_client_refuses_a_private_target_unless_the_deployment_allows_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`configure(allow_private_targets=...)` is the deployment's SSRF stance; a tenant cannot override it."""
    async with _http_listener() as port:
        target = make_tenant(base_url=f"http://127.0.0.1:{port}")

        with pytest.raises(EvolutionUnreachable, match="non-public"):
            await tenant.client_for(target).request("GET", "/")

        monkeypatch.setattr(tenant, "_clients", {})
        tenant.configure(allow_private_targets=True, public_url="https://mcp.example.test")
        assert await tenant.client_for(target).request("GET", "/") == {}
