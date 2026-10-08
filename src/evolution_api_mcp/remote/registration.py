"""Guards for the public OAuth endpoints a stranger can reach without credentials: `POST /register` and `/authorize`.

The MCP authorization specification requires dynamic client registration to be open: Claude.ai and ChatGPT register
themselves without credentials. Open must not mean unbounded, so everything a stranger can make this server store is
checked before the SDK's handlers see the request:

* `validate_registration` is a pure function from the parsed body to a refusal (or None): redirect URI count, length,
  scheme allowlist and host, and the size of the free-text metadata fields. Redirect URIs are judged in the form the
  SDK stores them (`pydantic.AnyUrl`), not in the form Python's `urlsplit` reads them.
* `RegistrationGuard` is the ASGI middleware around it. For `POST /register` it caps the body at 16 KiB, runs the
  validator, rate limits per client address (10 a minute, 30 an hour), keeps a server-wide safety valve that only
  registrations the SDK actually accepted (201) count against, and makes room in a full client table by evicting the
  oldest client nobody ever connected through. For `/authorize` it rate limits per address and per client_id and caps
  the pending-authorisation table. Every other request passes through untouched.

Refusals are RFC 7591 error bodies (`{"error", "error_description"}`); rate limits and caps are answered with
`temporarily_unavailable`. Refusals of `/register` carry the same permissive CORS header the SDK's handler adds, so
in-browser clients can read them.
"""

import json
import math
import re
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Sequence
from typing import Any, Protocol
from urllib.parse import parse_qs, urlsplit

from pydantic import AnyUrl
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_BODY_BYTES = 16 * 1024
MAX_REDIRECT_URIS = 5
MAX_URI_CHARS = 2048
MAX_CLIENT_NAME_CHARS = 200
MAX_CLIENTS = 5000
MAX_PENDING = 20_000

PER_ADDRESS_LIMIT = 10
PER_ADDRESS_WINDOW = 60.0
PER_ADDRESS_HOURLY_LIMIT = 30
PER_ADDRESS_HOURLY_WINDOW = 3600.0
GLOBAL_LIMIT = 1000
GLOBAL_WINDOW = 3600.0

AUTHORIZE_PER_ADDRESS_LIMIT = 60
AUTHORIZE_PER_CLIENT_LIMIT = 30
AUTHORIZE_WINDOW = 60.0
PENDING_PURGE_SECONDS = 60.0

_MAX_LIMITER_KEYS = 20_000
_MAX_CLIENT_ID_KEY_CHARS = 128

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
# Custom schemes without a dot are accepted only from this list of well-known desktop assistants and editors.
_NAMED_SCHEMES = frozenset({"cursor", "vscode", "vscode-insiders", "windsurf", "zed", "claude", "codex"})
_SCHEME = re.compile(r"[a-z][a-z0-9+.\-]*")
_UNSAFE_CHARS = re.compile(r"[\x00-\x20\x7f]")
_URI_FIELDS = ("client_uri", "logo_uri", "tos_uri", "policy_uri")

_CORS = {"access-control-allow-origin": "*"}

Refusal = tuple[str, str]


def _bad_uri(description: str) -> Refusal:
    return "invalid_redirect_uri", description


def _bad_metadata(description: str) -> Refusal:
    return "invalid_client_metadata", description


def _bare_host(host: str | None) -> str:
    return (host or "").strip("[]").lower()


def _redirect_uri_refusal(uri: Any) -> Refusal | None:
    if not isinstance(uri, str) or not uri:
        return _bad_uri("Each redirect URI must be a non-empty string.")
    if len(uri) > MAX_URI_CHARS:
        return _bad_uri(f"A redirect URI may be at most {MAX_URI_CHARS} characters.")
    if _UNSAFE_CHARS.search(uri):
        return _bad_uri("A redirect URI must not contain spaces or control characters.")
    if "\\" in uri:
        return _bad_uri("A redirect URI must not contain a backslash.")
    if "#" in uri:
        return _bad_uri("A redirect URI must not contain a fragment.")
    try:
        # AnyUrl is what OAuthClientMetadata parses redirect_uris with, so these are the scheme and host the SDK
        # stores and a browser later follows; urlsplit is only a cross-check on the string as submitted.
        stored = AnyUrl(uri)
        submitted = urlsplit(uri)
        submitted_host = _bare_host(submitted.hostname)
        _ = submitted.port  # raises ValueError on a malformed port
    except ValueError:
        return _bad_uri("A redirect URI is not a valid URI.")
    scheme = (stored.scheme or "").lower()
    if not scheme or not _SCHEME.fullmatch(scheme):
        return _bad_uri("A redirect URI must be absolute and start with a valid scheme.")
    host = _bare_host(stored.host)
    if scheme == "https":
        return None if host and submitted_host else _bad_uri("An https redirect URI must name a host.")
    if scheme == "http":
        if host in _LOOPBACK_HOSTS and host == submitted_host:
            return None
        return _bad_uri("An http redirect URI is allowed only for localhost, 127.0.0.1 or [::1]; use https.")
    if "." in scheme or scheme in _NAMED_SCHEMES:
        return None  # a reverse-DNS application scheme (RFC 8252) or one of the well-known editor schemes
    return _bad_uri(f"The redirect URI scheme '{scheme}' is not allowed.")


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
    """Per-key sliding-window limits over one monotonic clock; thread-safe.

    `rules` are `(limit, window_seconds)` pairs that must all hold. `acquire` is check-and-record in one step, so
    concurrent requests cannot jointly overshoot a limit; `check` and `record` are its two halves for callers that
    charge only once the outcome is known. A refused call records nothing and never creates a key.

    Memory is bounded twice over: each key keeps at most `max(limit)` timestamps, and the key table is swept of
    idle keys (nothing recorded within the longest window) and capped at `_MAX_LIMITER_KEYS`, least recently used
    first, so a caller rotating addresses cannot grow it without bound.
    """

    _SWEEP_ABOVE = 256

    def __init__(
        self,
        rules: Sequence[tuple[int, float]],
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = _MAX_LIMITER_KEYS,
    ) -> None:
        self._rules = tuple(rules)
        self._longest = max(window for _, window in self._rules)
        self._depth = max(limit for limit, _ in self._rules)
        self._clock = clock
        self._max_keys = max_keys
        self._lock = threading.Lock()
        self._by_key: OrderedDict[str, deque[float]] = OrderedDict()
        self._last_sweep = float("-inf")

    def _wait(self, stamps: deque[float] | None, now: float) -> int | None:
        if not stamps:
            return None
        waits = [
            stamps[-limit] + window - now
            for limit, window in self._rules
            if len(stamps) >= limit and now - stamps[-limit] < window
        ]
        return max(1, math.ceil(max(waits))) if waits else None

    def _sweep(self, now: float) -> None:
        if len(self._by_key) > self._SWEEP_ABOVE and now - self._last_sweep >= 1.0:
            self._last_sweep = now
            for stale in [k for k, stamps in self._by_key.items() if not stamps or now - stamps[-1] >= self._longest]:
                del self._by_key[stale]
        while len(self._by_key) >= self._max_keys:
            self._by_key.popitem(last=False)

    def check(self, key: str) -> int | None:
        """None when `key` may proceed; else the whole seconds to wait. Records nothing."""
        with self._lock:
            return self._wait(self._by_key.get(key), self._clock())

    def record(self, key: str) -> None:
        with self._lock:
            self._record(key, self._clock())

    def _record(self, key: str, now: float) -> None:
        stamps = self._by_key.get(key)
        if stamps is None:
            self._sweep(now)
            stamps = self._by_key[key] = deque(maxlen=self._depth)
        else:
            self._by_key.move_to_end(key)
        stamps.append(now)

    def acquire(self, key: str) -> int | None:
        """None when admitted (and recorded); else the whole seconds to wait before the earliest retry."""
        with self._lock:
            now = self._clock()
            wait = self._wait(self._by_key.get(key), now)
            if wait is None:
                self._record(key, now)
            return wait


class _GuardStore(Protocol):
    def make_client_room(self, max_clients: int) -> bool: ...

    def pending_room(self, max_pending: int) -> bool: ...

    def purge_expired_pending(self) -> int: ...


def client_address(scope: Scope) -> str:
    """The address the rate limits are keyed on.

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


def _refusal_response(
    status: int, error: str, description: str, headers: dict[str, str] | None = None, *, cors: bool = False
) -> JSONResponse:
    merged = {**(_CORS if cors else {}), **(headers or {})}
    if cors and "Retry-After" in merged:
        merged["access-control-expose-headers"] = "Retry-After"
    return JSONResponse({"error": error, "error_description": description}, status_code=status, headers=merged)


class RegistrationGuard:
    """ASGI middleware for `POST /register` and `/authorize` (GET, POST); every other request passes through."""

    def __init__(self, app: ASGIApp, store: _GuardStore, clock: Callable[[], float] = time.monotonic) -> None:
        self._app = app
        self._store = store
        self._clock = clock
        self._register_by_address = SlidingWindowLimiter(
            [(PER_ADDRESS_LIMIT, PER_ADDRESS_WINDOW), (PER_ADDRESS_HOURLY_LIMIT, PER_ADDRESS_HOURLY_WINDOW)],
            clock=clock,
        )
        self._register_overall = SlidingWindowLimiter([(GLOBAL_LIMIT, GLOBAL_WINDOW)], clock=clock)
        self._authorize_by_address = SlidingWindowLimiter(
            [(AUTHORIZE_PER_ADDRESS_LIMIT, AUTHORIZE_WINDOW)], clock=clock
        )
        self._authorize_by_client = SlidingWindowLimiter([(AUTHORIZE_PER_CLIENT_LIMIT, AUTHORIZE_WINDOW)], clock=clock)
        self._last_pending_purge = float("-inf")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            if scope["method"] == "POST" and scope["path"] == "/register":
                await self._register(scope, receive, send)
                return
            if scope["method"] in ("GET", "POST") and scope["path"] == "/authorize":
                if await self._authorize_refused(scope, receive, send):
                    return
        await self._app(scope, receive, send)

    async def _authorize_refused(self, scope: Scope, receive: Receive, send: Send) -> bool:
        """Answer and return True when this `/authorize` request must not reach the SDK."""

        def too_many(wait: int) -> JSONResponse:
            return _refusal_response(
                429,
                "temporarily_unavailable",
                f"Too many authorization requests. Try again in {wait} seconds.",
                {"Retry-After": str(wait)},
            )

        wait = self._authorize_by_address.acquire(client_address(scope))
        if wait is None:
            query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
            client_id = (query.get("client_id") or [""])[0][:_MAX_CLIENT_ID_KEY_CHARS]
            if client_id:
                wait = self._authorize_by_client.acquire(client_id)
        if wait is not None:
            await too_many(wait)(scope, receive, send)
            return True

        now = self._clock()
        if now - self._last_pending_purge >= PENDING_PURGE_SECONDS:
            self._last_pending_purge = now
            self._store.purge_expired_pending()
        if not self._store.pending_room(MAX_PENDING):
            await _refusal_response(503, "temporarily_unavailable", "The server is busy; try again later.")(
                scope, receive, send
            )
            return True
        return False

    async def _register(self, scope: Scope, receive: Receive, send: Send) -> None:
        too_large = _refusal_response(
            413,
            "invalid_client_metadata",
            f"The registration request may be at most {MAX_BODY_BYTES // 1024} KiB.",
            cors=True,
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
            await _refusal_response(400, *refusal, cors=True)(scope, receive, send)
            return

        def too_many(wait: int) -> JSONResponse:
            return _refusal_response(
                429,
                "temporarily_unavailable",
                f"Too many registrations. Try again in {wait} seconds.",
                {"Retry-After": str(wait)},
                cors=True,
            )

        wait = self._register_overall.check("*") or self._register_by_address.acquire(client_address(scope))
        if wait is not None:
            await too_many(wait)(scope, receive, send)
            return

        if not self._store.make_client_room(MAX_CLIENTS):
            await _refusal_response(
                503, "temporarily_unavailable", "Registration is full; try again later.", cors=True
            )(scope, receive, send)
            return

        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        async def counting_send(message: Message) -> None:
            # The server-wide valve counts only what the SDK actually registered; refused requests cost nothing.
            if message["type"] == "http.response.start" and message["status"] == 201:
                self._register_overall.record("*")
            await send(message)

        await self._app(scope, replay, counting_send)
