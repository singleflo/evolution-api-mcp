"""The public client-registration endpoint: validation, size caps, rate limits, table cap, retention, consent warning.

Registration is open by design (the MCP authorization specification requires it), so these tests pin what a stranger
cannot make this server do: store a `javascript:` redirect, a 100 KB name, thousands of URIs or an unbounded number of
rows. Most drive the REAL `build_app` over `/register`; the rate-limit tests use the middleware alone around a stub
app so 200 registrations stay fast. Each refusal is asserted twice: the RFC 7591 body AND that nothing was stored.

Needs the `remote` extra (`uv sync --extra remote`).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

pytest.importorskip("cryptography.fernet", reason="the store needs the [remote] extra: uv sync --extra remote")

from cryptography.fernet import Fernet  # noqa: E402
from mcp.shared.auth import OAuthClientInformationFull  # noqa: E402

from evolution_api_mcp import paths, registry, tenant  # noqa: E402
from evolution_api_mcp.remote import consent, registration  # noqa: E402
from evolution_api_mcp.remote.app import RemoteSettings, build_app  # noqa: E402
from evolution_api_mcp.remote.consent import ConsentDeps  # noqa: E402
from evolution_api_mcp.remote.registration import (  # noqa: E402
    RegistrationGuard,
    SlidingWindowLimiter,
    validate_registration,
)
from evolution_api_mcp.remote.store import (  # noqa: E402
    PENDING_TTL,
    AccessToken,
    AuthCode,
    PendingAuthz,
    RefreshToken,
    Store,
    hash_token,
)

PUBLIC_URL = "http://localhost:8000"
GOOD_URI = "https://claude.ai/api/mcp/auth_callback"
_ENV_VARS = (
    "EVOLUTION_API_URL",
    "EVOLUTION_INSTANCE_TOKEN",
    "EVOLUTION_MCP_TOOLSETS",
    "EVOLUTION_MCP_ALLOW",
    "EVOLUTION_MCP_DENY",
    "EVOLUTION_MCP_ALLOW_IRREVERSIBLE",
    "EVOLUTION_REMOTE_PUBLIC_URL",
    "EVOLUTION_REMOTE_SECRET_KEY",
    "EVOLUTION_MCP_DATA_DIR",
)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(paths, "_data_dir_override", None)
    monkeypatch.setattr(tenant, "_clients", {})
    monkeypatch.setattr(tenant, "_allow_private_targets", None)
    monkeypatch.setattr(tenant, "_public_url", None)


def _settings(tmp_path: Path) -> RemoteSettings:
    return RemoteSettings(
        public_url=PUBLIC_URL,
        secret_key=Fernet.generate_key().decode(),
        host="0.0.0.0",
        port=8000,
        openai_challenge=None,
        publisher="Persevida SL",
        support_email="https://github.com/singleflo/evolution-api-mcp/issues",
        data_dir=tmp_path,
        allow_private_targets=False,
        allowed_integrations=frozenset({registry.BUSINESS}),
    )


@pytest.fixture
def app_client(tmp_path):
    settings = _settings(tmp_path)
    with TestClient(build_app(settings), base_url=PUBLIC_URL, follow_redirects=False) as client:
        client.store = Store(tmp_path / "remote.db", settings.secret_key)  # type: ignore[attr-defined]
        yield client


def _payload(**over) -> dict:
    body = {
        "redirect_uris": [GOOD_URI],
        "client_name": "Claude",
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
    }
    body.update(over)
    return body


def _xff(address: str) -> dict[str, str]:
    return {"X-Forwarded-For": address}


# ------------------------------------------------------------ what is accepted
@pytest.mark.parametrize(
    "uri",
    [
        GOOD_URI,
        "http://localhost:43123/callback",
        "http://127.0.0.1:8080/cb",
        "http://[::1]:8080/cb",
        "cursor://anysphere.cursor-retrieval/oauth/callback",
        "vscode://ms-vscode.mcp/callback",
    ],
    ids=["https", "localhost", "127.0.0.1", "ipv6-loopback", "cursor", "vscode"],
)
def test_ordinary_registrations_are_accepted(app_client, uri):
    response = app_client.post("/register", json=_payload(redirect_uris=[uri]))

    assert response.status_code == 201, response.text
    assert app_client.store.client_count() == 1


def test_the_normal_claude_registration_still_works_end_to_end(app_client):
    response = app_client.post("/register", json=_payload(client_uri="https://claude.ai", logo_uri="https://x/l.png"))

    assert response.status_code == 201
    assert app_client.store.get_client(response.json()["client_id"]) is not None


# ------------------------------------------------------------ what is refused
@pytest.mark.parametrize(
    "uri",
    [
        "javascript:alert(1)",
        "JavaScript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "file:///etc/passwd",
        "vbscript:msgbox(1)",
        "about:blank",
        "blob:https://claude.ai/abc",
        "ftp://claude.ai/x",
        "ws://claude.ai/x",
        "wss://claude.ai/x",
        "http://evil.example/cb",
        "http://localhost.evil.example/cb",
        "https://claude.ai/cb#frag",
        "https://claude.ai/cb#",
        "https:///nohost",
        "/relative/path",
        "no-scheme.example/cb",
        "1bad://scheme/cb",
        "https://claude.ai/a b",
        "https://claude.ai:notaport/cb",
        "",
    ],
)
def test_a_dangerous_or_malformed_redirect_uri_is_refused_and_nothing_is_stored(app_client, uri):
    response = app_client.post("/register", json=_payload(redirect_uris=[uri]))

    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "invalid_redirect_uri"
    assert isinstance(body["error_description"], str) and body["error_description"].endswith(".")
    assert app_client.store.client_count() == 0


def test_one_bad_uri_among_good_ones_refuses_the_whole_registration(app_client):
    response = app_client.post("/register", json=_payload(redirect_uris=[GOOD_URI, "javascript:alert(1)"]))

    assert response.status_code == 400
    assert app_client.store.client_count() == 0


@pytest.mark.parametrize(
    "uris", [[], None, "https://claude.ai/cb", [GOOD_URI] * 6, [1]], ids=["empty", "none", "str", "six", "int"]
)
def test_the_number_and_type_of_redirect_uris_is_bounded(app_client, uris):
    body = _payload()
    body["redirect_uris"] = uris
    response = app_client.post("/register", json=body)

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_redirect_uri"
    assert app_client.store.client_count() == 0


def test_five_redirect_uris_are_allowed(app_client):
    uris = [f"https://claude.ai/cb{i}" for i in range(5)]

    assert app_client.post("/register", json=_payload(redirect_uris=uris)).status_code == 201


def test_a_redirect_uri_over_2048_characters_is_refused(app_client):
    long_uri = "https://claude.ai/" + "a" * 2040
    assert len(long_uri) > 2048

    response = app_client.post("/register", json=_payload(redirect_uris=[long_uri]))

    assert response.status_code == 400 and response.json()["error"] == "invalid_redirect_uri"
    assert app_client.store.client_count() == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("client_name", "n" * 201),
        ("client_name", 7),
        ("client_uri", "https://claude.ai/" + "a" * 2040),
        ("logo_uri", "https://claude.ai/" + "a" * 2040),
        ("tos_uri", "https://claude.ai/" + "a" * 2040),
        ("policy_uri", "https://claude.ai/" + "a" * 2040),
    ],
)
def test_oversized_metadata_fields_are_refused(app_client, field, value):
    response = app_client.post("/register", json=_payload(**{field: value}))

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_client_metadata"
    assert app_client.store.client_count() == 0


def test_a_name_of_exactly_200_characters_is_accepted(app_client):
    assert app_client.post("/register", json=_payload(client_name="n" * 200)).status_code == 201


def test_a_body_that_is_not_a_json_object_is_refused(app_client):
    for raw in (b"not json", b"[1, 2]", b"\xff\xfe"):
        response = app_client.post("/register", content=raw, headers={"Content-Type": "application/json"})
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_client_metadata"
    assert app_client.store.client_count() == 0


def test_a_body_over_16_kib_answers_413_by_content_length(app_client):
    response = app_client.post("/register", json=_payload(client_name="n" * 20_000))

    assert response.status_code == 413
    assert response.json()["error"] == "invalid_client_metadata"
    assert app_client.store.client_count() == 0


def test_a_streamed_body_over_16_kib_answers_413_without_a_content_length(app_client):
    def chunks():
        for _ in range(40):
            yield b" " * 1024

    response = app_client.post("/register", content=chunks(), headers={"Content-Type": "application/json"})

    assert response.status_code == 413
    assert app_client.store.client_count() == 0


def test_a_hundred_kilobyte_name_and_two_thousand_uris_cannot_get_through(app_client):
    assert app_client.post("/register", json=_payload(client_name="x" * 100_000)).status_code == 413
    assert app_client.post("/register", json=_payload(redirect_uris=[GOOD_URI] * 2000)).status_code == 413
    assert app_client.store.client_count() == 0


def test_refusals_carry_the_security_headers_of_the_rest_of_the_app(app_client):
    response = app_client.post("/register", json=_payload(redirect_uris=["javascript:alert(1)"]))

    assert response.headers["X-Frame-Options"] == "DENY"


def test_the_validator_is_pure_and_accepts_a_good_body():
    assert validate_registration(_payload()) is None
    assert validate_registration([]) == ("invalid_client_metadata", "The registration request must be a JSON object.")


# ------------------------------------------------------------------ rate limits
class _StubStore:
    """What the guard needs from the store, with the answers the test wants."""

    def __init__(self, clients: int = 0, pending: int = 0) -> None:
        self.clients = clients
        self.pending = pending
        self.purged = 0

    def make_client_room(self, max_clients: int) -> bool:
        return self.clients < max_clients

    def pending_room(self, max_pending: int) -> bool:
        return self.pending < max_pending

    def purge_expired_pending(self) -> int:
        self.purged += 1
        return 0


def _guarded(store: _StubStore | None = None, clock=None, handler=None) -> TestClient:
    """The middleware alone around a stub that answers 201, so only the guard's decisions are under test."""

    async def stub(request):
        return JSONResponse({"client_id": "stub"}, status_code=201)

    async def ok(request):
        return JSONResponse({"ok": True})

    app = Starlette(
        routes=[
            Route("/register", handler or stub, methods=["POST"]),
            Route("/other", stub, methods=["POST"]),
            Route("/authorize", ok, methods=["GET", "POST"]),
        ]
    )
    app.add_middleware(RegistrationGuard, store=store or _StubStore(), **({"clock": clock} if clock else {}))
    return TestClient(app)


def test_the_eleventh_registration_in_a_minute_from_one_address_gets_429_with_retry_after():
    client = _guarded()
    for _ in range(10):
        assert client.post("/register", json=_payload(), headers=_xff("203.0.113.5")).status_code == 201

    refused = client.post("/register", json=_payload(), headers=_xff("203.0.113.5"))

    assert refused.status_code == 429
    wait = int(refused.headers["Retry-After"])
    assert 1 <= wait <= 60
    assert refused.json() == {
        "error": "temporarily_unavailable",
        "error_description": f"Too many registrations. Try again in {wait} seconds.",
    }
    # another address is unaffected
    assert client.post("/register", json=_payload(), headers=_xff("203.0.113.6")).status_code == 201


def test_a_spoofed_leftmost_forwarded_for_does_not_change_the_address():
    client = _guarded()
    for i in range(10):
        response = client.post("/register", json=_payload(), headers=_xff(f"spoof-{i}, 203.0.113.5"))
        assert response.status_code == 201

    assert client.post("/register", json=_payload(), headers=_xff("spoof-new, 203.0.113.5")).status_code == 429
    assert client.post("/register", json=_payload(), headers=_xff("spoof-new, 203.0.113.9")).status_code == 201


def test_without_forwarded_for_the_peer_address_is_used():
    client = _guarded()
    for _ in range(10):
        assert client.post("/register", json=_payload()).status_code == 201

    assert client.post("/register", json=_payload()).status_code == 429


def test_requests_refused_for_validation_do_not_consume_the_quota():
    client = _guarded()
    for _ in range(30):
        assert client.post("/register", json=_payload(redirect_uris=["javascript:x"])).status_code == 400
    for _ in range(10):
        assert client.post("/register", json=_payload()).status_code == 201


def test_the_server_wide_limit_applies_across_addresses(monkeypatch):
    monkeypatch.setattr(registration, "GLOBAL_LIMIT", 25)
    client = _guarded()
    for i in range(25):
        assert client.post("/register", json=_payload(), headers=_xff(f"10.0.{i // 250}.{i % 250}")).status_code == 201

    refused = client.post("/register", json=_payload(), headers=_xff("198.51.100.1"))

    assert refused.status_code == 429
    assert 1 <= int(refused.headers["Retry-After"]) <= 3600
    assert refused.json()["error"] == "temporarily_unavailable"


def test_the_window_slides_so_old_registrations_stop_counting():
    now = [1000.0]
    limiter = SlidingWindowLimiter([(2, 60.0)], clock=lambda: now[0])

    assert limiter.acquire("a") is None
    assert limiter.acquire("a") is None
    assert limiter.acquire("a") == 60
    now[0] += 30
    assert limiter.acquire("a") == 30
    now[0] += 30
    assert limiter.acquire("a") is None


def test_the_table_cap_answers_503_before_inserting_and_allows_exactly_the_cap():
    refused = _guarded(_StubStore(clients=5000)).post("/register", json=_payload())

    assert refused.status_code == 503
    assert refused.json() == {
        "error": "temporarily_unavailable",
        "error_description": "Registration is full; try again later.",
    }
    assert _guarded(_StubStore(clients=4999)).post("/register", json=_payload()).status_code == 201


def test_a_full_table_in_the_real_app_refuses_to_insert_when_nothing_is_evictable(app_client, monkeypatch):
    monkeypatch.setattr(registration, "MAX_CLIENTS", 1)
    assert app_client.post("/register", json=_payload(), headers=_xff("1.1.1.1")).status_code == 201
    with sqlite3.connect(app_client.store._path) as db:
        db.execute("UPDATE oauth_clients SET last_used_at = ?", (datetime.now(timezone.utc).isoformat(),))

    refused = app_client.post("/register", json=_payload(), headers=_xff("1.1.1.3"))

    assert refused.status_code == 503
    assert app_client.store.client_count() == 1


def test_the_guard_touches_only_post_register_and_authorize():
    client = _guarded(_StubStore(clients=10**6, pending=10**6))

    assert client.post("/other", json={"anything": True}).status_code == 201


def _sdk_refusing_refresh_only(request_body: dict) -> int:
    return 400 if request_body.get("grant_types") == ["refresh_token"] else 201


def _refusing_handler():
    async def sdk(request):
        status = _sdk_refusing_refresh_only(await request.json())
        return JSONResponse({"client_id": "stub"} if status == 201 else {"error": "invalid_client_metadata"}, status)

    return sdk


def test_requests_the_sdk_refuses_never_consume_the_server_wide_quota(monkeypatch):
    monkeypatch.setattr(registration, "GLOBAL_LIMIT", 5)
    client = _guarded(handler=_refusing_handler())
    for i in range(30):
        refused = client.post("/register", json=_payload(grant_types=["refresh_token"]), headers=_xff(f"10.0.0.{i}"))
        assert refused.status_code == 400

    for i in range(5):
        assert client.post("/register", json=_payload(), headers=_xff(f"10.1.0.{i}")).status_code == 201
    assert client.post("/register", json=_payload(), headers=_xff("10.2.0.1")).status_code == 429


def test_a_refresh_token_only_flood_leaves_a_claude_registration_admitted_in_the_real_app(tmp_path, monkeypatch):
    monkeypatch.setattr(registration, "GLOBAL_LIMIT", 5)  # read when the app (its guard) is built
    with TestClient(build_app(_settings(tmp_path)), base_url=PUBLIC_URL, follow_redirects=False) as app_client:
        for i in range(12):
            flood = app_client.post(
                "/register", json=_payload(grant_types=["refresh_token"]), headers=_xff(f"10.0.0.{i}")
            )
            assert flood.status_code == 400

        claude = app_client.post("/register", json=_payload(), headers=_xff("10.9.9.9"))

        assert claude.status_code == 201, claude.text


def test_the_published_limits_are_the_documented_ones():
    assert (registration.PER_ADDRESS_LIMIT, registration.PER_ADDRESS_WINDOW) == (10, 60.0)
    assert (registration.PER_ADDRESS_HOURLY_LIMIT, registration.PER_ADDRESS_HOURLY_WINDOW) == (30, 3600.0)
    assert (registration.GLOBAL_LIMIT, registration.GLOBAL_WINDOW) == (1000, 3600.0)
    assert registration.MAX_CLIENTS == 5000 and registration.MAX_PENDING == 20_000


def test_the_hourly_per_address_limit_holds_even_when_the_minute_limit_resets():
    now = [1000.0]
    client = _guarded(clock=lambda: now[0])
    for _ in range(3):
        for _ in range(10):
            assert client.post("/register", json=_payload(), headers=_xff("203.0.113.5")).status_code == 201
        now[0] += 61

    refused = client.post("/register", json=_payload(), headers=_xff("203.0.113.5"))

    assert refused.status_code == 429
    assert 60 < int(refused.headers["Retry-After"]) <= 3600
    assert client.post("/register", json=_payload(), headers=_xff("203.0.113.6")).status_code == 201


def test_guard_refusals_carry_the_cors_header_the_sdk_handler_adds():
    full = _guarded(_StubStore(clients=5000))
    limited = _guarded()
    for _ in range(10):
        limited.post("/register", json=_payload())

    invalid = limited.post("/register", json=_payload(redirect_uris=["javascript:x"]), headers=_xff("1.1.1.1"))
    too_large = limited.post("/register", json=_payload(client_name="n" * 20_000), headers=_xff("1.1.1.1"))
    throttled = limited.post("/register", json=_payload())
    unavailable = full.post("/register", json=_payload())

    assert (invalid.status_code, too_large.status_code, throttled.status_code, unavailable.status_code) == (
        400,
        413,
        429,
        503,
    )
    for response in (invalid, too_large, throttled, unavailable):
        assert response.headers["access-control-allow-origin"] == "*"
    assert throttled.headers["access-control-expose-headers"] == "Retry-After"


def test_a_full_table_evicts_the_oldest_never_used_client_and_never_exceeds_the_cap(app_client, monkeypatch):
    monkeypatch.setattr(registration, "MAX_CLIENTS", 2)
    ids = []
    for i in range(3):
        response = app_client.post("/register", json=_payload(), headers=_xff(f"1.1.1.{i}"))
        assert response.status_code == 201
        ids.append(response.json()["client_id"])
        _age(app_client.store._path, ids[0], 3)  # the first registration is the oldest from here on

    assert app_client.store.client_count() == 2
    assert app_client.store.get_client(ids[0]) is None
    assert app_client.store.get_client(ids[1]) is not None and app_client.store.get_client(ids[2]) is not None


def test_a_full_table_of_used_clients_answers_503(app_client, monkeypatch):
    monkeypatch.setattr(registration, "MAX_CLIENTS", 2)
    for i in range(2):
        assert app_client.post("/register", json=_payload(), headers=_xff(f"1.1.1.{i}")).status_code == 201
    with sqlite3.connect(app_client.store._path) as db:
        db.execute("UPDATE oauth_clients SET last_used_at = ?", (datetime.now(timezone.utc).isoformat(),))

    refused = app_client.post("/register", json=_payload(), headers=_xff("1.1.1.9"))

    assert refused.status_code == 503
    assert refused.json()["error"] == "temporarily_unavailable"
    assert app_client.store.client_count() == 2


# ------------------------------------------------------------- redirect rules
@pytest.mark.parametrize(
    "uri",
    [
        "http://evil.example\\@localhost/cb",
        "http://evil.example\\@127.0.0.1:8080/cb",
        "http://evil.example\\@[::1]/cb",
        "https://claude.ai\\@evil.example/cb",
        "http://localhost\\.evil.example/cb",
        "https://claude.ai/cb\\x",
    ],
)
def test_a_backslash_in_a_redirect_uri_cannot_smuggle_a_host_past_the_loopback_rule(app_client, uri):
    response = app_client.post("/register", json=_payload(redirect_uris=[uri]))

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_redirect_uri"
    assert app_client.store.client_count() == 0


@pytest.mark.parametrize(
    "uri",
    [
        "http://localhost:6274/oauth/callback",
        "http://127.0.0.1:43123/callback",
        "http://[::1]:5000/cb",
        "http://LOCALHOST:6274/cb",
    ],
)
def test_loopback_http_redirects_are_still_accepted(app_client, uri):
    assert app_client.post("/register", json=_payload(redirect_uris=[uri])).status_code == 201


@pytest.mark.parametrize(
    "uri",
    [
        "itms-services://?action=download-manifest&url=https://evil.example/m.plist",
        "ms-msdt:/id%20PCWDiagnostic",
        "search-ms:query=x",
        "mailto:victim@example.com",
        "mailto:",
        "tel:+15551234",
        "chrome-extension://abc/cb",
        "view-source:https://x",
        "myapp://callback",
        "x://cb",
        "cursor-evil://cb",
    ],
)
def test_a_custom_scheme_outside_the_allowlist_is_refused(app_client, uri):
    response = app_client.post("/register", json=_payload(redirect_uris=[uri]))

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_redirect_uri"
    assert app_client.store.client_count() == 0


@pytest.mark.parametrize(
    "uri",
    [
        "cursor://anysphere.cursor-retrieval/oauth/callback",
        "vscode://ms-vscode.mcp/callback",
        "vscode-insiders://x/cb",
        "windsurf://x/cb",
        "zed://x/cb",
        "claude://x/cb",
        "codex://x/cb",
        "com.example.app://cb",
        "com.example.app:/oauth2redirect",
    ],
)
def test_reverse_dns_and_named_editor_schemes_are_accepted(app_client, uri):
    assert app_client.post("/register", json=_payload(redirect_uris=[uri])).status_code == 201


# ------------------------------------------------------------------ /authorize
def _authorize(client: TestClient, address: str, client_id: str = "cid", method: str = "GET"):
    return client.request(method, f"/authorize?client_id={client_id}", headers=_xff(address))


def test_authorize_is_limited_to_sixty_a_minute_per_address_with_retry_after():
    client = _guarded()
    for i in range(60):  # a distinct client_id each time: one id would hit its own limit first
        assert _authorize(client, "203.0.113.5", client_id=f"c{i}").status_code == 200
    refused = _authorize(client, "203.0.113.5", client_id="rotating")

    assert refused.status_code == 429
    assert 1 <= int(refused.headers["Retry-After"]) <= 60
    assert refused.json()["error"] == "temporarily_unavailable"
    assert _authorize(client, "203.0.113.6", client_id="rotating-2").status_code == 200


def test_authorize_is_limited_to_thirty_a_minute_per_client_id_across_addresses():
    client = _guarded()
    for i in range(30):
        assert _authorize(client, f"10.0.0.{i}", client_id="shared").status_code == 200

    refused = _authorize(client, "10.0.1.1", client_id="shared")

    assert refused.status_code == 429 and "Retry-After" in refused.headers
    assert _authorize(client, "10.0.1.1", client_id="another").status_code == 200


def test_authorize_by_post_is_limited_per_address_too():
    client = _guarded()
    for i in range(60):
        assert _authorize(client, "203.0.113.5", client_id=f"c{i}", method="POST").status_code == 200

    assert _authorize(client, "203.0.113.5", client_id="c-last", method="POST").status_code == 429


def test_authorize_limits_slide_with_the_clock():
    now = [1000.0]
    client = _guarded(clock=lambda: now[0])
    for i in range(60):
        assert _authorize(client, "203.0.113.5", client_id=f"c{i}").status_code == 200
    assert _authorize(client, "203.0.113.5", client_id="x").status_code == 429

    now[0] += 61

    assert _authorize(client, "203.0.113.5", client_id="x").status_code == 200


def test_authorize_answers_503_when_the_pending_table_is_full():
    client = _guarded(_StubStore(pending=registration.MAX_PENDING))

    refused = _authorize(client, "203.0.113.5")

    assert refused.status_code == 503
    assert refused.json()["error"] == "temporarily_unavailable"
    assert _authorize(_guarded(_StubStore(pending=registration.MAX_PENDING - 1)), "203.0.113.5").status_code == 200


def test_expired_pending_rows_are_purged_from_the_guard_at_most_once_a_minute():
    now = [1000.0]
    store = _StubStore()
    client = _guarded(store, clock=lambda: now[0])

    for i in range(5):
        assert _authorize(client, "203.0.113.5", client_id=f"c{i}").status_code == 200
    assert store.purged == 1

    now[0] += 61
    assert _authorize(client, "203.0.113.5", client_id="later").status_code == 200
    assert store.purged == 2


def test_the_real_app_limits_authorize_per_address(app_client):
    statuses = [
        app_client.get(f"/authorize?client_id=c{i}", headers=_xff("203.0.113.5")).status_code for i in range(61)
    ]

    assert statuses[:60] == [400] * 60  # the SDK refuses the unknown client
    assert statuses[60] == 429


def test_the_real_app_caps_pending_authorizations(app_client, monkeypatch):
    monkeypatch.setattr(registration, "MAX_PENDING", 1)
    registered = app_client.post("/register", json=_payload(redirect_uris=["http://localhost:9/cb"]))
    cid = registered.json()["client_id"]
    params = {
        "client_id": cid,
        "redirect_uri": "http://localhost:9/cb",
        "response_type": "code",
        "code_challenge": "x" * 43,
        "code_challenge_method": "S256",
    }

    first = app_client.get("/authorize", params=params, headers=_xff("203.0.113.5"))
    second = app_client.get("/authorize", params=params, headers=_xff("203.0.113.6"))

    assert first.status_code == 302
    assert second.status_code == 503


# ----------------------------------------------------------- limiter memory
def test_the_limiter_forgets_idle_keys_and_caps_the_key_table():
    now = [0.0]
    limiter = SlidingWindowLimiter([(5, 60.0)], clock=lambda: now[0])
    for i in range(400):
        assert limiter.acquire(f"k{i}") is None
    assert len(limiter._by_key) == 400

    now[0] += 61
    limiter.acquire("fresh")

    assert len(limiter._by_key) == 1

    capped = SlidingWindowLimiter([(5, 60.0)], clock=lambda: now[0], max_keys=50)
    for i in range(500):
        capped.acquire(f"k{i}")
    assert len(capped._by_key) <= 50


def test_a_limiter_key_keeps_at_most_its_largest_limit_of_timestamps():
    limiter = SlidingWindowLimiter([(3, 60.0), (5, 3600.0)])
    for _ in range(50):
        limiter.acquire("a")

    assert len(limiter._by_key["a"]) <= 5


# -------------------------------------------------------------------- retention
def _age(db_path: Path, client_id: str, days: float) -> None:
    stamp = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE oauth_clients SET created_at = ? WHERE client_id = ?", (stamp, client_id))


def _registered(store: Store, client_id: str) -> None:
    store.put_client(
        OAuthClientInformationFull.model_validate({"client_id": client_id, "redirect_uris": ["http://localhost:9/cb"]})
    )


def test_purge_unused_clients_keeps_referenced_and_young_clients_and_deletes_old_unreferenced_ones(tmp_path):
    db_path = tmp_path / "remote.db"
    store = Store(db_path, Fernet.generate_key().decode())
    store.init()
    live = datetime.now(timezone.utc) + timedelta(hours=1)
    for cid in ("old-unused", "old-unused-2", "young", "old-pending", "old-code", "old-access", "old-refresh"):
        _registered(store, cid)
        if cid != "young":
            _age(db_path, cid, 45)
    store.put_pending(
        PendingAuthz("p1", "old-pending", "http://localhost:9/cb", True, "evolution", "c", None, None, live)
    )
    store.put_code(AuthCode("code", "old-code", "s", "evolution", "c", "http://localhost:9/cb", True, None, live))
    store.put_access(AccessToken(hash_token("a"), "fam", "old-access", "s", "evolution", live))
    store.put_refresh(RefreshToken(hash_token("r"), "fam", "old-refresh", "s", "evolution", live))

    assert store.purge_unused_clients() == 3  # a bare pending authorisation does not keep a client alive

    remaining = {
        cid
        for cid in ("old-unused", "old-unused-2", "young", "old-pending", "old-code", "old-access", "old-refresh")
        if store.get_client(cid)
    }
    assert remaining == {"young", "old-code", "old-access", "old-refresh"}
    assert store.client_count() == 4
    assert store.purge_unused_clients() == 0


def test_purge_unused_clients_honours_its_arguments(tmp_path):
    db_path = tmp_path / "remote.db"
    store = Store(db_path, Fernet.generate_key().decode())
    store.init()
    _registered(store, "c")
    _age(db_path, "c", 10)

    assert store.purge_unused_clients(unused_after=timedelta(days=30)) == 0
    assert store.purge_unused_clients(unused_after=timedelta(days=7)) == 1


def test_the_hourly_sweep_removes_unused_registrations(tmp_path):
    from evolution_api_mcp.remote import app as remote_app

    db_path = tmp_path / "remote.db"
    store = Store(db_path, Fernet.generate_key().decode())
    store.init()
    _registered(store, "stale")
    _age(db_path, "stale", 31)

    remote_app._sweep_once(store)

    assert store.get_client("stale") is None


def _last_used(db_path: Path, client_id: str) -> str | None:
    with sqlite3.connect(db_path) as db:
        return db.execute("SELECT last_used_at FROM oauth_clients WHERE client_id = ?", (client_id,)).fetchone()[0]


def _set_last_used(db_path: Path, client_id: str, days: float) -> None:
    stamp = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE oauth_clients SET last_used_at = ? WHERE client_id = ?", (stamp, client_id))


def _fresh_store(tmp_path: Path) -> tuple[Store, Path]:
    db_path = tmp_path / "remote.db"
    store = Store(db_path, Fernet.generate_key().decode())
    store.init()
    return store, db_path


def test_a_bare_pending_authorization_does_not_pin_an_unused_client_against_the_purge(tmp_path):
    store, db_path = _fresh_store(tmp_path)
    _registered(store, "pinned")
    _age(db_path, "pinned", 3)
    store.put_pending(
        PendingAuthz(
            "p",
            "pinned",
            "http://localhost:9/cb",
            True,
            "evolution",
            "c",
            None,
            None,
            datetime.now(timezone.utc) + PENDING_TTL,
        )
    )

    assert store.purge_unused_clients() == 1
    assert store.get_client("pinned") is None


def test_an_unused_client_is_kept_for_48_hours_and_then_purged(tmp_path):
    store, db_path = _fresh_store(tmp_path)
    _registered(store, "young")
    _registered(store, "old")
    _age(db_path, "young", 1.9)
    _age(db_path, "old", 2.1)

    assert store.purge_unused_clients() == 1
    assert store.get_client("young") is not None and store.get_client("old") is None


def test_a_used_client_survives_48_hours_but_not_90_days_without_use(tmp_path):
    store, db_path = _fresh_store(tmp_path)
    for cid in ("recent", "idle"):
        _registered(store, cid)
        _age(db_path, cid, 200)
    _set_last_used(db_path, "recent", 89)
    _set_last_used(db_path, "idle", 91)

    assert store.purge_unused_clients() == 1
    assert store.get_client("recent") is not None and store.get_client("idle") is None


def test_a_used_client_with_a_live_token_is_never_purged(tmp_path):
    store, db_path = _fresh_store(tmp_path)
    _registered(store, "c")
    _age(db_path, "c", 200)
    _set_last_used(db_path, "c", 120)
    store.put_refresh(
        RefreshToken(hash_token("r"), "fam", "c", "s", "evolution", datetime.now(timezone.utc) + timedelta(days=1))
    )

    assert store.purge_unused_clients() == 0


def test_a_code_exchange_and_a_refresh_rotation_set_last_used_but_authorize_does_not(tmp_path):
    store, db_path = _fresh_store(tmp_path)
    live = datetime.now(timezone.utc) + timedelta(hours=1)
    _registered(store, "c")
    store.put_pending(PendingAuthz("p", "c", "http://localhost:9/cb", True, "evolution", "c", None, None, live))
    assert _last_used(db_path, "c") is None

    store.put_code(AuthCode("code", "c", "s", "evolution", "c", "http://localhost:9/cb", True, None, live))
    access = AccessToken(hash_token("a1"), "fam1", "c", "s", "evolution", live)
    refresh = RefreshToken(hash_token("r1"), "fam1", "c", "s", "evolution", live)
    assert store.exchange_code_pair("code", access, refresh) is not None
    first = _last_used(db_path, "c")
    assert first is not None

    _set_last_used(db_path, "c", 10)
    access2 = AccessToken(hash_token("a2"), "fam2", "c", "s", "evolution", live)
    refresh2 = RefreshToken(hash_token("r2"), "fam2", "c", "s", "evolution", live)
    assert store.rotate_refresh_pair(hash_token("r1"), access2, refresh2) is not None
    assert _last_used(db_path, "c") > (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()  # type: ignore[operator]


def test_a_failed_exchange_does_not_mark_the_client_used(tmp_path):
    store, db_path = _fresh_store(tmp_path)
    live = datetime.now(timezone.utc) + timedelta(hours=1)
    _registered(store, "c")
    access = AccessToken(hash_token("a"), "fam", "c", "s", "evolution", live)
    refresh = RefreshToken(hash_token("r"), "fam", "c", "s", "evolution", live)

    assert store.exchange_code_pair("no-such-code", access, refresh) is None
    assert _last_used(db_path, "c") is None


def test_init_adds_last_used_at_to_a_database_created_before_the_column_existed(tmp_path):
    db_path = tmp_path / "remote.db"
    key = Fernet.generate_key().decode()
    store = Store(db_path, key)
    client = OAuthClientInformationFull.model_validate({"client_id": "old", "redirect_uris": ["http://localhost:9/cb"]})
    with sqlite3.connect(db_path) as db:
        db.execute(
            "CREATE TABLE oauth_clients"
            " (client_id TEXT PRIMARY KEY, client_json TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        db.execute(
            "INSERT INTO oauth_clients VALUES (?, ?, ?)",
            ("old", store._fernet.encrypt(client.model_dump_json().encode()), datetime.now(timezone.utc).isoformat()),
        )

    store.init()
    store.init()  # idempotent

    with sqlite3.connect(db_path) as db:
        assert "last_used_at" in {row[1] for row in db.execute("PRAGMA table_info(oauth_clients)")}
    assert store.get_client("old") is not None
    assert _last_used(db_path, "old") is None
    assert store.client_count() == 1


def test_make_client_room_evicts_the_oldest_never_used_clients_first(tmp_path):
    store, db_path = _fresh_store(tmp_path)
    for age, cid in ((3, "oldest"), (2, "middle"), (1, "newest")):
        _registered(store, cid)
        _age(db_path, cid, age)

    assert store.make_client_room(4) is True and store.client_count() == 3  # room already
    assert store.make_client_room(3) is True

    assert store.get_client("oldest") is None
    assert {cid for cid in ("middle", "newest") if store.get_client(cid)} == {"middle", "newest"}
    assert store.client_count() == 2  # one free slot: at most the cap after the insert


def test_make_client_room_refuses_only_when_nothing_is_evictable(tmp_path):
    store, db_path = _fresh_store(tmp_path)
    live = datetime.now(timezone.utc) + timedelta(hours=1)
    for cid in ("used", "holds-token"):
        _registered(store, cid)
    _set_last_used(db_path, "used", 1)
    store.put_refresh(RefreshToken(hash_token("r"), "fam", "holds-token", "s", "evolution", live))

    assert store.make_client_room(2) is False
    assert store.client_count() == 2

    _registered(store, "never-used")
    assert store.make_client_room(3) is True
    assert store.get_client("never-used") is None
    assert store.client_count() == 2


def test_pending_room_drops_expired_rows_first_and_refuses_when_the_live_ones_fill_the_cap(tmp_path):
    store, _ = _fresh_store(tmp_path)
    now = datetime.now(timezone.utc)
    for i, expires in enumerate((now - timedelta(minutes=1), now - timedelta(minutes=2))):
        store.put_pending(
            PendingAuthz(f"e{i}", "c", "http://localhost:9/cb", True, "evolution", "c", None, None, expires)
        )

    assert store.pending_room(2) is True  # the two expired rows were dropped
    for i in range(2):
        store.put_pending(
            PendingAuthz(f"l{i}", "c", "http://localhost:9/cb", True, "evolution", "c", None, None, now + PENDING_TTL)
        )
    assert store.pending_room(2) is False
    assert store.load_pending("l0") is not None
    assert store.purge_expired_pending() == 0


# ---------------------------------------------------------------------- consent
def _consent_client(store: Store) -> TestClient:
    app = Starlette(routes=[Route("/consent", consent.consent_form, methods=["GET"])])
    app.state.consent_deps = ConsentDeps(
        store=store,
        provider=None,  # type: ignore[arg-type]  # the GET form never calls it
        public_url="https://mcp.example.test",
        allow_private_targets=False,
        allowed_integrations=frozenset({registry.BUSINESS}),
        publisher="Persevida SL",
    )
    return TestClient(app)


def _consent_page(tmp_path: Path, redirect_uri: str, name: str = "Some App") -> str:
    store = Store(tmp_path / "remote.db", Fernet.generate_key().decode())
    store.init()
    store.put_client(
        OAuthClientInformationFull.model_validate(
            {"client_id": "cid", "client_name": name, "redirect_uris": ["http://localhost:9/cb"]}
        )
    )
    store.put_pending(
        PendingAuthz(
            "pend", "cid", redirect_uri, True, "evolution", "c", None, None, datetime.now(timezone.utc) + PENDING_TTL
        )
    )
    return _consent_client(store).get("/consent?req=pend").text


WARNING = (
    "This application is not one of the assistants this server knows. "
    "Only continue if you started this connection yourself."
)


@pytest.mark.parametrize(
    "uri",
    [
        "https://evil.example/cb",
        "http://evil.example/cb",
        "https://claude.ai.evil.example/cb",
        "https://www.claude.ai/cb",
        "myapp://callback",
        "cursor://anysphere.cursor-retrieval/oauth/callback",
        "vscode://evil.publisher/cb",
        "vscode-insiders://x/cb",
        "windsurf://x/cb",
        "zed://x/cb",
        "claude://x/cb",
        "codex://x/cb",
        "com.example.app://cb",
    ],
)
def test_consent_warns_for_an_unknown_client(tmp_path, uri):
    assert WARNING in _consent_page(tmp_path, uri)


@pytest.mark.parametrize(
    "uri",
    [
        "https://claude.ai/api/mcp/auth_callback",
        "https://claude.com/cb",
        "https://chatgpt.com/connector_platform_oauth_redirect",
        "http://localhost:43123/callback",
        "http://127.0.0.1:43123/callback",
        "http://[::1]:43123/callback",
    ],
)
def test_consent_does_not_warn_for_a_known_client(tmp_path, uri):
    assert WARNING not in _consent_page(tmp_path, uri)


def test_consent_truncates_a_long_client_name_to_80_characters_and_still_escapes_it(tmp_path):
    page = _consent_page(tmp_path, "https://claude.ai/cb", name="<b>" + "n" * 496)

    assert "&lt;b&gt;" + "n" * 77 + "…" in page
    assert "n" * 78 not in page
    assert "<b>" not in page


def test_consent_shows_a_short_client_name_unchanged(tmp_path):
    page = _consent_page(tmp_path, "https://claude.ai/cb", name="Short Name")

    assert "<strong><bdi>Short Name</bdi></strong>" in page


def test_consent_strips_bidi_and_zero_width_characters_from_the_displayed_name(tmp_path):
    page = _consent_page(tmp_path, "https://claude.ai/cb", name="\u202eevil\u200b na\u2066me\x07\n")

    assert "<strong><bdi>evil name</bdi></strong>" in page
    assert "<dd><bdi>evil name</bdi></dd>" in page
    for hidden in ("\u202e", "\u200b", "\u2066", "\x07"):
        assert hidden not in page
