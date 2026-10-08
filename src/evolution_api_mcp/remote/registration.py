"""Guards for the public dynamic-client-registration endpoint, `POST /register`.

The MCP authorization specification requires registration to be open: Claude.ai and ChatGPT register themselves
without credentials. Open must not mean unbounded, so everything a stranger can make this server store is checked
before the SDK's handler sees the request:

* `validate_registration` is a pure function from the parsed body to a refusal (or None): redirect URI count, length
  and scheme, and the size of the free-text metadata fields.
* `RegistrationGuard` is the ASGI middleware around it. For `POST /register` only, it caps the body at 16 KiB, runs
  the validator, refuses when the client table is full, and rate limits the validated requests per client address and
  for the whole server. Every other request passes through untouched.

Refusals are RFC 7591 error bodies (`{"error", "error_description"}`); the rate limit and the table cap are answered
with `temporarily_unavailable`.
"""

import json
import math
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any, Protocol
from urllib.parse import urlsplit

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_BODY_BYTES = 16 * 1024
MAX_REDIRECT_URIS = 5
MAX_URI_CHARS = 2048
MAX_CLIENT_NAME_CHARS = 200
MAX_CLIENTS = 5000

PER_ADDRESS_LIMIT = 10
PER_ADDRESS_WINDOW = 60.0
GLOBAL_LIMIT = 200
GLOBAL_WINDOW = 3600.0

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_REFUSED_SCHEMES = frozenset({"javascript", "data", "file", "vbscript", "about", "blob", "ftp", "ws", "wss"})
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*")
_UNSAFE_CHARS = re.compile(r"[\x00-\x20\x7f]")
_URI_FIELDS = ("client_uri", "logo_uri", "tos_uri", "policy_uri")

Refusal = tuple[str, str]


def _bad_uri(description: str) -> Refusal:
    return "invalid_redirect_uri", description


def _bad_metadata(description: str) -> Refusal:
    return "invalid_client_metadata", description


def _redirect_uri_refusal(uri: Any) -> Refusal | None:
    if not isinstance(uri, str) or not uri:
        return _bad_uri("Each redirect URI must be a non-empty string.")
    if len(uri) > MAX_URI_CHARS:
        return _bad_uri(f"A redirect URI may be at most {MAX_URI_CHARS} characters.")
    if _UNSAFE_CHARS.search(uri):
        return _bad_uri("A redirect URI must not contain spaces or control characters.")
    if "#" in uri:
        return _bad_uri("A redirect URI must not contain a fragment.")
    try:
        parts = urlsplit(uri)
        host = parts.hostname
        _ = parts.port  # raises ValueError on a malformed port
    except ValueError:
        return _bad_uri("A redirect URI is not a valid URI.")
    scheme = parts.scheme.lower()
    if not scheme or not _SCHEME.fullmatch(scheme):
        return _bad_uri("A redirect URI must be absolute and start with a valid scheme.")
    if scheme in _REFUSED_SCHEMES:
        return _bad_uri(f"The redirect URI scheme '{scheme}' is not allowed.")
    if scheme == "https":
        return None if host else _bad_uri("An https redirect URI must name a host.")
    if scheme == "http":
        if host in _LOOPBACK_HOSTS:
            return None
        return _bad_uri("An http redirect URI is allowed only for localhost, 127.0.0.1 or [::1]; use https.")
    return None  # a custom application scheme such as cursor:// or vscode://


def validate_registration(payload: Any) -> Refusal | None:
    """None when the parsed registration body may reach the SDK's handler, else `(error, error_description)`."""
    if not isinstance(payload, dict):
        return _bad_metadata("The registration request must be a JSON object.")

    uris = payload.get("redirect_uris")
    if not isinstance(uris, list) or not 1 <= len(uris) <= MAX_REDIRECT_URIS:
        return _bad_uri(f"redirect_uris must list between 1 and {MAX_REDIRECT_URIS} URIs.")
    for uri in uris:
        refusal = _redirect_uri_refusal(uri)
        if refusal:
            return refusal

    name = payload.get("client_name")
    if name is not None:
        if not isinstance(name, str):
            return _bad_metadata("client_name must be a string.")
        if len(name) > MAX_CLIENT_NAME_CHARS:
            return _bad_metadata(f"client_name may be at most {MAX_CLIENT_NAME_CHARS} characters.")
    for field in _URI_FIELDS:
        value = payload.get(field)
        if value is None:
            continue
        if not isinstance(value, str):
            return _bad_metadata(f"{field} must be a string.")
        if len(value) > MAX_URI_CHARS:
            return _bad_metadata(f"{field} may be at most {MAX_URI_CHARS} characters.")
    return None


class SlidingWindowLimiter:
    """Per-key and global sliding-window limits over one monotonic clock; thread-safe.

    `acquire` is check-and-record in one step, so concurrent requests cannot jointly overshoot a limit. A refused
    call records nothing. Memory is bounded: every recorded entry leaves its window within an hour, and only
    addresses that were actually admitted ever get a deque.
    """

    _SWEEP_ABOVE = 256

    def __init__(
        self,
        per_key: int,
        per_key_window: float,
        overall: int,
        overall_window: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._per_key = per_key
        self._per_key_window = per_key_window
        self._overall = overall
        self._overall_window = overall_window
        self._clock = clock
        self._lock = threading.Lock()
        self._by_key: dict[str, deque[float]] = {}
        self._all: deque[float] = deque()

    @staticmethod
    def _expire(stamps: deque[float], now: float, window: float) -> None:
        while stamps and now - stamps[0] >= window:
            stamps.popleft()

    def acquire(self, key: str) -> int | None:
        """None when admitted (and recorded); else the whole seconds to wait before the earliest retry."""
        with self._lock:
            now = self._clock()
            if len(self._by_key) > self._SWEEP_ABOVE:
                window = self._per_key_window
                for stale in [k for k, stamps in self._by_key.items() if not stamps or now - stamps[-1] >= window]:
                    del self._by_key[stale]
            mine = self._by_key.setdefault(key, deque())
            self._expire(mine, now, self._per_key_window)
            self._expire(self._all, now, self._overall_window)

            waits = []
            if len(mine) >= self._per_key:
                waits.append(mine[0] + self._per_key_window - now)
            if len(self._all) >= self._overall:
                waits.append(self._all[0] + self._overall_window - now)
            if waits:
                if not mine:
                    del self._by_key[key]
                return max(1, math.ceil(max(waits)))
            mine.append(now)
            self._all.append(now)
            return None


class _ClientCounter(Protocol):
    def client_count(self) -> int: ...


def client_address(scope: Scope) -> str:
    """The address the rate limit is keyed on.

    The server runs behind exactly one proxy (Traefik), which appends the peer address it saw to X-Forwarded-For. The
    rightmost entry is therefore the only one a caller cannot choose; everything to its left is caller-supplied.
    Without the header the ASGI peer address is used.
    """
    forwarded = [value.decode("latin-1") for name, value in scope.get("headers", ()) if name == b"x-forwarded-for"]
    if forwarded:
        entries = [entry.strip() for entry in forwarded[-1].split(",") if entry.strip()]
        if entries:
            return entries[-1]
    client = scope.get("client")
    return client[0] if client else "unknown"


def _refusal_response(status: int, error: str, description: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"error": error, "error_description": description}, status_code=status, headers=headers)


class RegistrationGuard:
    """ASGI middleware: validation, size cap, table cap and rate limits for `POST /register`, nothing else."""

    def __init__(self, app: ASGIApp, store: _ClientCounter) -> None:
        self._app = app
        self._store = store
        self._limiter = SlidingWindowLimiter(PER_ADDRESS_LIMIT, PER_ADDRESS_WINDOW, GLOBAL_LIMIT, GLOBAL_WINDOW)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != "/register":
            await self._app(scope, receive, send)
            return

        too_large = _refusal_response(
            413, "invalid_client_metadata", f"The registration request may be at most {MAX_BODY_BYTES // 1024} KiB."
        )
        declared = next((v for n, v in scope.get("headers", ()) if n == b"content-length"), b"")
        if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
            await too_large(scope, receive, send)
            return

        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > MAX_BODY_BYTES:
                await too_large(scope, receive, send)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)

        try:
            refusal = validate_registration(json.loads(body))
        except ValueError:  # includes UnicodeDecodeError and JSONDecodeError
            refusal = _bad_metadata("The registration request must be valid JSON.")
        if refusal:
            await _refusal_response(400, *refusal)(scope, receive, send)
            return

        if self._store.client_count() > MAX_CLIENTS:
            await _refusal_response(503, "temporarily_unavailable", "Registration is full; try again later.")(
                scope, receive, send
            )
            return

        wait = self._limiter.acquire(client_address(scope))
        if wait is not None:
            await _refusal_response(
                429,
                "temporarily_unavailable",
                f"Too many registrations. Try again in {wait} seconds.",
                {"Retry-After": str(wait)},
            )(scope, receive, send)
            return

        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self._app(scope, replay, send)
