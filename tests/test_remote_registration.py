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
class _CountingStore:
    def __init__(self, count: int = 0) -> None:
        self.count = count

    def client_count(self) -> int:
        return self.count


def _guarded(store: _CountingStore | None = None) -> TestClient:
    """The middleware alone around a stub that answers 201, so only the guard's decisions are under test."""

    async def stub(request):
        return JSONResponse({"client_id": "stub"}, status_code=201)

    app = Starlette(routes=[Route("/register", stub, methods=["POST"]), Route("/other", stub, methods=["POST"])])
    app.add_middleware(RegistrationGuard, store=store or _CountingStore())
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


def test_the_server_wide_hourly_limit_applies_across_addresses():
    client = _guarded()
    for i in range(200):
        assert client.post("/register", json=_payload(), headers=_xff(f"10.0.{i // 250}.{i % 250}")).status_code == 201

    refused = client.post("/register", json=_payload(), headers=_xff("198.51.100.1"))

    assert refused.status_code == 429
    assert 1 <= int(refused.headers["Retry-After"]) <= 3600
    assert refused.json()["error"] == "temporarily_unavailable"


def test_the_window_slides_so_old_registrations_stop_counting():
    now = [1000.0]
    limiter = SlidingWindowLimiter(2, 60.0, 200, 3600.0, clock=lambda: now[0])

    assert limiter.acquire("a") is None
    assert limiter.acquire("a") is None
    assert limiter.acquire("a") == 60
    now[0] += 30
    assert limiter.acquire("a") == 30
    now[0] += 30
    assert limiter.acquire("a") is None


def test_the_table_cap_answers_503_before_inserting(monkeypatch):
    client = _guarded(_CountingStore(count=5001))

    refused = client.post("/register", json=_payload())

    assert refused.status_code == 503
    assert refused.json() == {
        "error": "temporarily_unavailable",
        "error_description": "Registration is full; try again later.",
    }
    assert _guarded(_CountingStore(count=5000)).post("/register", json=_payload()).status_code == 201


def test_a_full_table_in_the_real_app_refuses_to_insert(app_client, monkeypatch):
    monkeypatch.setattr(registration, "MAX_CLIENTS", 1)
    assert app_client.post("/register", json=_payload(), headers=_xff("1.1.1.1")).status_code == 201
    assert app_client.post("/register", json=_payload(), headers=_xff("1.1.1.2")).status_code == 201

    refused = app_client.post("/register", json=_payload(), headers=_xff("1.1.1.3"))

    assert refused.status_code == 503
    assert app_client.store.client_count() == 2


def test_the_guard_touches_only_post_register():
    client = _guarded(_CountingStore(count=10**6))

    assert client.post("/other", json={"anything": True}).status_code == 201


# -------------------------------------------------------------------- retention
def _age(db_path: Path, client_id: str, days: int) -> None:
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

    assert store.purge_unused_clients() == 2

    remaining = {
        cid
        for cid in ("old-unused", "old-unused-2", "young", "old-pending", "old-code", "old-access", "old-refresh")
        if store.get_client(cid)
    }
    assert remaining == {"young", "old-pending", "old-code", "old-access", "old-refresh"}
    assert store.client_count() == 5
    assert store.purge_unused_clients() == 0


def test_purge_unused_clients_honours_the_days_argument(tmp_path):
    db_path = tmp_path / "remote.db"
    store = Store(db_path, Fernet.generate_key().decode())
    store.init()
    _registered(store, "c")
    _age(db_path, "c", 10)

    assert store.purge_unused_clients(days=30) == 0
    assert store.purge_unused_clients(days=7) == 1


def test_the_hourly_sweep_removes_unused_registrations(tmp_path):
    from evolution_api_mcp.remote import app as remote_app

    db_path = tmp_path / "remote.db"
    store = Store(db_path, Fernet.generate_key().decode())
    store.init()
    _registered(store, "stale")
    _age(db_path, "stale", 31)

    remote_app._sweep_once(store)

    assert store.get_client("stale") is None


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
        "cursor://anysphere.cursor-retrieval/oauth/callback",
        "vscode://x/cb",
        "vscode-insiders://x/cb",
        "windsurf://x/cb",
        "zed://x/cb",
        "claude://x/cb",
        "codex://x/cb",
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
    assert "<strong>Short Name</strong>" in _consent_page(tmp_path, "https://claude.ai/cb", name="Short Name")
