"""SSRF guard for the hosted server, enforced at connect time.

The consent page checks the URL a user submits, but a DNS answer can change between that check and
the real request (DNS rebinding) and Evolution servers may redirect. So the check that matters runs
where the socket is opened: `GuardedBackend.connect_tcp` resolves the host itself, refuses the
connection when ANY resolved address is not a public one, and then connects to the vetted address
(never to the hostname again). TLS still verifies the original hostname because httpcore passes
`server_hostname` from the request's origin, not from the address we dial.
"""

from __future__ import annotations

import ipaddress
import socket
import typing

import anyio
import httpcore2
import httpx2


def vet_addresses(host: str, addresses: typing.Iterable[str], *, allow_private: bool) -> list[str]:
    """Return `addresses` de-duplicated in order, or raise `httpcore2.ConnectError` for a non-public one.

    IPv4-mapped IPv6 addresses (`::ffff:127.0.0.1`) are judged by the IPv4 address they wrap. With
    `allow_private=True` nothing is refused.
    """
    vetted: list[str] = []
    for addr in addresses:
        if not allow_private:
            try:
                ip = ipaddress.ip_address(addr)
            except ValueError as exc:
                raise httpcore2.ConnectError(f"refused unparseable address {addr} for {host}") from exc
            if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
                ip = ip.ipv4_mapped
            if not ip.is_global:
                raise httpcore2.ConnectError(f"refused non-public address {addr} for {host}")
        if addr not in vetted:
            vetted.append(addr)
    return vetted


class GuardedBackend(httpcore2.AsyncNetworkBackend):
    """Network backend that resolves, vets and only then connects to the vetted IP."""

    def __init__(self, *, allow_private: bool) -> None:
        self._allow_private = allow_private
        self._inner = httpcore2.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109
        local_address: str | None = None,
        socket_options: typing.Iterable[typing.Any] | None = None,
    ) -> httpcore2.AsyncNetworkStream:
        try:
            with anyio.fail_after(timeout):
                infos = await anyio.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except TimeoutError as exc:
            raise httpcore2.ConnectTimeout(f"timed out resolving {host}") from exc
        except OSError as exc:
            raise httpcore2.ConnectError(f"cannot resolve {host}: {exc}") from exc
        addresses = vet_addresses(host, (str(info[4][0]) for info in infos), allow_private=self._allow_private)
        if not addresses:
            raise httpcore2.ConnectError(f"cannot resolve {host}: no addresses")
        last_error: Exception | None = None
        for addr in addresses:
            try:
                return await self._inner.connect_tcp(
                    addr, port, timeout=timeout, local_address=local_address, socket_options=socket_options
                )
            except (httpcore2.ConnectError, httpcore2.ConnectTimeout) as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,  # noqa: ASYNC109
        socket_options: typing.Iterable[typing.Any] | None = None,
    ) -> httpcore2.AsyncNetworkStream:
        raise httpcore2.ConnectError("refused unix socket connection")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


def guarded_transport(*, allow_private: bool) -> httpx2.AsyncHTTPTransport:
    """An httpx2 transport whose every TCP connection goes through `GuardedBackend`."""
    transport = httpx2.AsyncHTTPTransport()
    pool = transport._pool
    if not hasattr(pool, "_network_backend"):
        # An httpx2/httpcore2 internal changed. Failing loudly beats running without the guard.
        raise RuntimeError("httpcore2 connection pool has no _network_backend; the SSRF guard cannot be installed")
    pool._network_backend = GuardedBackend(allow_private=allow_private)
    return transport
