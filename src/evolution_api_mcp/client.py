"""Async HTTP client for one Evolution API server.

Every failure is one of four types so callers can tell what is known about the
request: `EvolutionUnreachable` (nothing reached Evolution), `EvolutionUncertain`
(the request was sent and no answer came back), `EvolutionHTTPError` (Evolution
answered with a non-2xx status) and, for anything else, plain `EvolutionError`.
The instance token is sent only in the `apikey` header and never appears in an
exception text.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Literal
from urllib.parse import urlsplit

import httpx2

from evolution_api_mcp import __version__

MAX_MESSAGE_CHARS = 500
CONNECT_TIMEOUT = 10.0


class EvolutionError(Exception):
    """Base class of every failure of a call to Evolution."""


class EvolutionUnreachable(EvolutionError):
    """The request never reached Evolution (DNS, refused connection, connect timeout, bad URL)."""

    def __init__(self, host: str, reason: str) -> None:
        super().__init__(f"Could not reach {host}: {reason}")
        self.host = host
        self.reason = reason


class EvolutionUncertain(EvolutionError):
    """The request may have reached Evolution but no answer arrived (read timeout, dropped connection)."""

    def __init__(self, host: str, reason: str) -> None:
        super().__init__(f"No answer from {host}: {reason}")
        self.host = host
        self.reason = reason


class EvolutionHTTPError(EvolutionError):
    """Evolution answered with a non-2xx status."""

    def __init__(self, status: int, message: str, body: object) -> None:
        super().__init__(f"Evolution answered {status}: {message}")
        self.status = status
        self.message = message
        self.body = body


def _as_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "; ".join(_as_text(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def extract_message(body: object) -> str:
    """Pick the human-readable message out of an Evolution error body (at most 500 characters).

    Order: `response.message`, `message`, `error.message`, `error`, then `str(body)`. Lists are joined
    with "; ". An empty or missing body yields "no error details".
    """
    candidates: list[object] = []
    if isinstance(body, dict):
        response = body.get("response")
        if isinstance(response, dict):
            candidates.append(response.get("message"))
        candidates.append(body.get("message"))
        error = body.get("error")
        if isinstance(error, dict):
            candidates.append(error.get("message"))
        candidates.append(error)
    for candidate in candidates:
        if candidate not in (None, "", [], {}):
            text = _as_text(candidate)
            break
    else:
        text = "" if body is None else body if isinstance(body, str) else str(body)
    if not text.strip():
        text = "no error details"
    return text[:MAX_MESSAGE_CHARS]


def _display_host(base_url: str) -> str:
    """`host[:port]` of `base_url` without credentials, for messages."""
    try:
        parts = urlsplit(base_url)
        host = parts.hostname
        if host:
            if ":" in host:
                host = f"[{host}]"
            return f"{host}:{parts.port}" if parts.port else host
    except ValueError:
        pass
    return base_url


_UNREACHABLE = (httpx2.ConnectError, httpx2.ConnectTimeout, httpx2.PoolTimeout, httpx2.UnsupportedProtocol)


class EvolutionClient:
    """One Evolution server, authenticated with one instance token."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        timeout: float = 90.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.host = _display_host(self.base_url)
        self._http = httpx2.AsyncClient(
            transport=transport,
            timeout=httpx2.Timeout(timeout, connect=CONNECT_TIMEOUT),
            headers={
                "apikey": token,
                "Accept": "application/json",
                "User-Agent": f"evolution-api-mcp/{__version__}",
            },
            follow_redirects=False,
        )

    async def request(
        self,
        method: Literal["GET", "POST", "PUT", "DELETE"],
        path: str,
        *,
        json: object | None = None,
        params: Mapping[str, str] | None = None,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> object:
        """Call Evolution and return the parsed body (None for an empty body).

        Raises `EvolutionHTTPError` for non-2xx answers, `EvolutionUnreachable` when nothing reached
        the server and `EvolutionUncertain` when the request was sent but no answer arrived.
        """
        kwargs: dict[str, object] = {}
        if timeout is not None:
            kwargs["timeout"] = httpx2.Timeout(timeout, connect=CONNECT_TIMEOUT)
        try:
            response = await self._http.request(
                method, self.base_url + path, json=json, params=dict(params) if params else None, **kwargs
            )
        except httpx2.InvalidURL as exc:
            raise EvolutionUnreachable(self.host, f"invalid URL ({exc})") from exc
        except _UNREACHABLE as exc:
            raise EvolutionUnreachable(self.host, str(exc) or type(exc).__name__) from exc
        except httpx2.RequestError as exc:
            raise EvolutionUncertain(self.host, str(exc) or type(exc).__name__) from exc
        body = _parse_body(response)
        if response.is_success:
            return body
        raise EvolutionHTTPError(response.status_code, extract_message(body), body)

    async def aclose(self) -> None:
        await self._http.aclose()


def _parse_body(response: httpx2.Response) -> object:
    text = response.text
    if not text.strip():
        return None
    looks_json = "json" in response.headers.get("content-type", "").lower() or text.lstrip()[:1] in ("{", "[")
    if looks_json:
        try:
            return json.loads(text)
        except ValueError:
            return text
    return text
