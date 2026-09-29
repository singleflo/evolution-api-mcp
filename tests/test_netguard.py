"""The connect-time SSRF guard: which addresses are refused, and that the refusal happens on a real socket path."""

from __future__ import annotations

import asyncio
import re
import socket
import types
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anyio
import httpcore2
import pytest

from evolution_api_mcp import netguard
from evolution_api_mcp.client import EvolutionClient, EvolutionUnreachable
from evolution_api_mcp.netguard import guarded_transport, vet_addresses
from tests.conftest import TOKEN


@asynccontextmanager
async def http_server() -> AsyncIterator[tuple[int, list[int]]]:
    """A real HTTP server on 127.0.0.1 answering every request with {"ok": true}; yields (port, connections)."""
    connections: list[int] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        connections.append(1)
        await reader.readuntil(b"\r\n\r\n")
        body = b'{"ok": true}'
        head = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n"
        writer.write(head + b"Content-Length: %d\r\n\r\n" % len(body) + body)
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        yield server.sockets[0].getsockname()[1], connections
    finally:
        server.close()
        await server.wait_closed()


def resolving_to(monkeypatch: pytest.MonkeyPatch, port: int, *addresses: str) -> None:
    """Make the guard's DNS lookup answer `addresses` for any host (the lookup, not the HTTP path, is faked)."""

    async def getaddrinfo(host: str, _port: object, **_: object) -> list[tuple[object, ...]]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, port)) for addr in addresses]

    monkeypatch.setattr(netguard, "anyio", types.SimpleNamespace(getaddrinfo=getaddrinfo, fail_after=anyio.fail_after))


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "::1",
        "::ffff:127.0.0.1",
        "::ffff:169.254.169.254",
        "169.254.169.254",
        "10.0.0.5",
        "172.16.0.1",
        "192.168.1.1",
        "100.64.0.1",
        "0.0.0.0",
        "fe80::1",
        "fd00::1",
    ],
)
def test_non_public_addresses_are_refused(address: str) -> None:
    message = re.escape(f"refused non-public address {address} for evo.example.com")
    with pytest.raises(httpcore2.ConnectError, match=message):
        vet_addresses("evo.example.com", [address], allow_private=False)


@pytest.mark.parametrize("address", ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946", "::ffff:93.184.216.34"])
def test_public_addresses_are_accepted(address: str) -> None:
    assert vet_addresses("evo.example.com", [address], allow_private=False) == [address]


def test_one_private_answer_among_public_ones_refuses_the_whole_host() -> None:
    with pytest.raises(httpcore2.ConnectError, match="refused non-public address 10.0.0.5"):
        vet_addresses("evo.example.com", ["93.184.216.34", "10.0.0.5"], allow_private=False)


def test_allow_private_accepts_everything_and_answers_are_deduplicated() -> None:
    assert vet_addresses("evo.local", ["127.0.0.1", "10.0.0.5", "127.0.0.1"], allow_private=True) == [
        "127.0.0.1",
        "10.0.0.5",
    ]


@pytest.mark.anyio
async def test_loopback_server_is_refused_without_a_single_connection() -> None:
    async with http_server() as (port, connections):
        client = EvolutionClient(f"http://127.0.0.1:{port}", TOKEN, transport=guarded_transport(allow_private=False))

        with pytest.raises(EvolutionUnreachable) as caught:
            await client.request("GET", "/")

        assert "refused non-public address 127.0.0.1" in caught.value.reason
        assert connections == []
        await client.aclose()


@pytest.mark.anyio
async def test_loopback_server_is_reached_when_private_targets_are_allowed() -> None:
    async with http_server() as (port, connections):
        client = EvolutionClient(f"http://127.0.0.1:{port}", TOKEN, transport=guarded_transport(allow_private=True))

        assert await client.request("GET", "/") == {"ok": True}

        assert connections == [1]
        await client.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize("host", ["[::ffff:127.0.0.1]", "169.254.169.254", "localhost"])
async def test_private_hosts_are_refused_end_to_end(host: str) -> None:
    async with http_server() as (port, connections):
        client = EvolutionClient(f"http://{host}:{port}", TOKEN, transport=guarded_transport(allow_private=False))

        with pytest.raises(EvolutionUnreachable) as caught:
            await client.request("GET", "/")

        assert "refused non-public address" in caught.value.reason
        assert connections == []
        await client.aclose()


@pytest.mark.anyio
async def test_a_name_that_resolves_to_a_private_address_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    async with http_server() as (port, connections):
        resolving_to(monkeypatch, port, "93.184.216.34", "127.0.0.1")
        transport = guarded_transport(allow_private=False)
        client = EvolutionClient(f"http://rebind.example:{port}", TOKEN, transport=transport)

        with pytest.raises(EvolutionUnreachable) as caught:
            await client.request("GET", "/")

        assert "refused non-public address 127.0.0.1 for rebind.example" in caught.value.reason
        assert connections == []
        await client.aclose()


@pytest.mark.anyio
async def test_the_connection_goes_to_the_vetted_address_not_to_a_second_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with http_server() as (port, connections):
        resolving_to(monkeypatch, port, "127.0.0.1")
        transport = guarded_transport(allow_private=True)
        client = EvolutionClient(f"http://only-in-our-lookup.example:{port}", TOKEN, transport=transport)

        assert await client.request("GET", "/") == {"ok": True}

        assert connections == [1]
        await client.aclose()


@pytest.mark.anyio
async def test_an_unresolvable_name_is_unreachable() -> None:
    client = EvolutionClient("http://no-such-host.invalid", TOKEN, transport=guarded_transport(allow_private=False))

    with pytest.raises(EvolutionUnreachable):
        await client.request("GET", "/")
    await client.aclose()
