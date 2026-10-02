"""End-to-end drives of the SDK's own OAuth routes over EvolutionAuthProvider.

The app is `create_auth_routes` (plus the protected-resource metadata route) on
a plain Starlette app — no /mcp, so the ASGI transport needs no lifespan —
driven through the real HTTP surface: metadata, DCR (public and confidential),
/authorize with PKCE S256, the consent hand-off, /token for both grants,
/revoke, and the adversarial cases (wrong verifier, redirect mismatch, code
replay, expired code, deleted tenant).

Needs the `remote` extra (`uv sync --extra remote`): cryptography lives there,
not in the base environment a stdio install resolves.
"""

import asyncio
import base64
import hashlib
import secrets
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx2
import pytest

pytest.importorskip("cryptography.fernet", reason="the auth provider needs the [remote] extra: uv sync --extra remote")

# The gate above must run before these: cryptography is remote-only.
from cryptography.fernet import Fernet  # noqa: E402
from mcp.server.auth.routes import create_auth_routes, create_protected_resource_routes  # noqa: E402
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions  # noqa: E402
from mcp.shared.auth import OAuthClientInformationFull  # noqa: E402
from pydantic import AnyHttpUrl  # noqa: E402
from starlette.applications import Starlette  # noqa: E402

from evolution_api_mcp import paths  # noqa: E402
from evolution_api_mcp.registry import BUSINESS  # noqa: E402
from evolution_api_mcp.remote.auth import EvolutionAuthProvider  # noqa: E402
from evolution_api_mcp.remote.store import AuthCode, Store  # noqa: E402
from evolution_api_mcp.tenant import Tenant  # noqa: E402

PUBLIC_URL = "http://localhost:8000"
SERVER = f"{PUBLIC_URL}/mcp"
REDIRECT_URI = "http://127.0.0.1:43123/callback"
SUBJECT = "subj-1"


def _build(tmp_path):
    """Store + provider + the SDK's route builders on a plain Starlette app."""
    st = Store(tmp_path / "remote.db", Fernet.generate_key().decode())
    st.init()
    provider = EvolutionAuthProvider(st, PUBLIC_URL)
    routes = create_auth_routes(
        provider,
        issuer_url=AnyHttpUrl(PUBLIC_URL),
        client_registration_options=ClientRegistrationOptions(
            enabled=True, valid_scopes=["evolution"], default_scopes=["evolution"]
        ),
        revocation_options=RevocationOptions(enabled=True),
    )
    routes += create_protected_resource_routes(
        resource_url=AnyHttpUrl(SERVER), authorization_servers=[AnyHttpUrl(PUBLIC_URL)]
    )
    return st, provider, Starlette(routes=routes)


def _tenant() -> Tenant:
    return Tenant(
        subject=SUBJECT,
        base_url="https://evo.example",
        token="the-instance-token",
        instance_name="shop",
        integration=BUSINESS,
        policy="read",
        toolsets=frozenset({"messaging", "chats"}),
    )


@asynccontextmanager
async def _server(tmp_path):
    """One store (with a consented tenant) behind the ASGI app over httpx2."""
    st, provider, app = _build(tmp_path)
    st.put_tenant(_tenant())
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=PUBLIC_URL) as http:
        yield http, provider, st


def _pkce():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


async def _register(http, **overrides):
    body = {
        "redirect_uris": [REDIRECT_URI],
        "client_name": "test host",
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
    } | overrides
    r = await http.post("/register", json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def _authorize(http, client_id, challenge, redirect_uri=REDIRECT_URI):
    return await http.get(
        "/authorize",
        params={
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "st-1",
            "scope": "evolution",
            "resource": SERVER,
        },
        follow_redirects=False,
    )


async def _consent_code(provider, redirect_response):
    """complete_consent on the 302's req id; returns the minted code."""
    consent = urlparse(redirect_response.headers["location"])
    assert consent.path == "/consent"
    req = parse_qs(consent.query)["req"][0]
    final = urlparse(provider.complete_consent(req, SUBJECT))
    query = parse_qs(final.query)
    assert query["state"] == ["st-1"]  # state carried from /authorize
    return query["code"][0]


async def _code_flow(http, provider):
    """register -> authorize -> consent; returns (client, verifier, code)."""
    client = await _register(http)
    verifier, challenge = _pkce()
    r = await _authorize(http, client["client_id"], challenge)
    assert r.status_code == 302, r.text
    return client, verifier, await _consent_code(provider, r)


async def _exchange_code(http, client_id, code, verifier, redirect_uri=REDIRECT_URI):
    return await http.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
        },
    )


async def _refresh(http, client_id, refresh_token):
    return await http.post(
        "/token", data={"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id}
    )


async def _pair(http, provider):
    client, verifier, code = await _code_flow(http, provider)
    r = await _exchange_code(http, client["client_id"], code, verifier)
    assert r.status_code == 200, r.text
    return client, r.json()


# ---------------------------------------------------------------- (1) metadata
def test_metadata_advertises_s256_pkce_and_a_registration_endpoint(tmp_path):
    async def scenario():
        async with _server(tmp_path) as (http, _, _):
            return await http.get("/.well-known/oauth-authorization-server")

    r = asyncio.run(scenario())
    assert r.status_code == 200
    doc = r.json()
    assert doc["code_challenge_methods_supported"] == ["S256"]
    assert doc["registration_endpoint"]
    assert doc["grant_types_supported"] == ["authorization_code", "refresh_token"]


# --------------------------------------------------------------- (2) discovery
def test_register_accepts_loopback_http_public_and_confidential(tmp_path):
    async def scenario():
        async with _server(tmp_path) as (http, _, _):
            return (
                await _register(http),
                await _register(http, token_endpoint_auth_method="client_secret_post"),
            )

    public, confidential = asyncio.run(scenario())
    assert public["client_id"]  # Claude Code's loopback http:43123 accepted
    assert "client_secret" not in public  # a public client gets none
    assert confidential["client_secret"]


# -------------------------------------------------------------- (3) /authorize
def test_authorize_redirects_to_consent_with_a_pending_request(tmp_path):
    async def scenario():
        async with _server(tmp_path) as (http, _, _):
            client = await _register(http)
            _, challenge = _pkce()
            return await _authorize(http, client["client_id"], challenge)

    r = asyncio.run(scenario())
    assert r.status_code == 302
    consent = urlparse(r.headers["location"])
    assert consent.path == "/consent"
    assert parse_qs(consent.query)["req"]  # pending id for the consent page


# -------------------------------------------- (4) consent + authorization_code
def test_consent_then_code_exchange_returns_a_usable_pair(tmp_path):
    async def scenario():
        async with _server(tmp_path) as (http, provider, _):
            _, pair = await _pair(http, provider)
            loaded = await provider.load_access_token(pair["access_token"])
            return pair, loaded

    pair, loaded = asyncio.run(scenario())
    assert pair["access_token"] and pair["refresh_token"]
    assert pair["expires_in"] == 3600
    assert pair["scope"] == "evolution"
    assert loaded is not None
    assert loaded.subject == SUBJECT  # the consent-assigned tenant subject
    assert loaded.scopes == ["evolution"]


def test_a_loaded_access_token_is_bound_to_the_mcp_resource(tmp_path):
    """AuthSettings(validate_token_resource=True) refuses a token whose
    `resource` is not the MCP endpoint, so the provider must report it."""

    async def scenario():
        async with _server(tmp_path) as (http, provider, _):
            _, pair = await _pair(http, provider)
            return await provider.load_access_token(pair["access_token"])

    assert asyncio.run(scenario()).resource == SERVER


def test_an_unknown_access_token_loads_none(tmp_path):
    async def scenario():
        async with _server(tmp_path) as (_, provider, _):
            return await provider.load_access_token("never-minted")

    assert asyncio.run(scenario()) is None


def test_only_the_registering_client_can_load_its_refresh_token(tmp_path):
    async def scenario():
        async with _server(tmp_path) as (http, provider, _):
            client, pair = await _pair(http, provider)
            mine = OAuthClientInformationFull.model_validate(client)
            other = OAuthClientInformationFull.model_validate(
                client | {"client_id": "someone-else", "client_secret": None}
            )
            return (
                await provider.load_refresh_token(mine, pair["refresh_token"]),
                await provider.load_refresh_token(other, pair["refresh_token"]),
            )

    own, foreign = asyncio.run(scenario())
    assert own is not None
    assert foreign is None


# ------------------------------------------------------- (5) refresh rotation
def test_refresh_rotates_and_the_old_refresh_dies(tmp_path):
    async def scenario():
        async with _server(tmp_path) as (http, provider, _):
            client, first = await _pair(http, provider)
            rotated = await _refresh(http, client["client_id"], first["refresh_token"])
            replay = await _refresh(http, client["client_id"], first["refresh_token"])
            return first, rotated, replay

    first, rotated, replay = asyncio.run(scenario())
    assert rotated.status_code == 200
    assert rotated.json()["access_token"] != first["access_token"]
    assert rotated.json()["refresh_token"] != first["refresh_token"]
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"


def test_rotation_kills_the_previous_access_token_and_keeps_the_new_one_alive(tmp_path):
    async def scenario():
        async with _server(tmp_path) as (http, provider, _):
            client, first = await _pair(http, provider)
            rotated = (await _refresh(http, client["client_id"], first["refresh_token"])).json()
            return (
                await provider.load_access_token(first["access_token"]),
                await provider.load_access_token(rotated["access_token"]),
            )

    old, new = asyncio.run(scenario())
    assert old is None
    assert new is not None


def test_presenting_a_rotated_out_refresh_token_does_not_revoke_the_live_family_but_yields_nothing(tmp_path):
    """The replay is refused; the replacement pair stays usable to its holder."""

    async def scenario():
        async with _server(tmp_path) as (http, provider, _):
            client, first = await _pair(http, provider)
            rotated = (await _refresh(http, client["client_id"], first["refresh_token"])).json()
            replay = await _refresh(http, client["client_id"], first["refresh_token"])
            again = await _refresh(http, client["client_id"], rotated["refresh_token"])
            return replay, again

    replay, again = asyncio.run(scenario())
    assert replay.status_code == 400
    assert again.status_code == 200


# ------------------------------------- (6) revoke access -> family + tenant die
def test_revoking_the_access_token_kills_the_family_and_the_tenant(tmp_path):
    async def scenario():
        async with _server(tmp_path) as (http, provider, st):
            client, pair = await _pair(http, provider)
            revoked = await http.post(
                "/revoke",
                data={
                    "token": pair["access_token"],
                    "client_id": client["client_id"],
                    # the SDK's revocation model requires the key even for a
                    # public client, which presents it empty
                    "client_secret": "",
                },
            )
            refresh_after = await _refresh(http, client["client_id"], pair["refresh_token"])
            return revoked, refresh_after, st.get_tenant(SUBJECT)

    revoked, refresh_after, tenant = asyncio.run(scenario())
    assert revoked.status_code == 200
    assert refresh_after.status_code == 400
    assert refresh_after.json()["error"] == "invalid_grant"
    assert tenant is None  # no other family alive -> the stored token is erased


def test_revoking_the_refresh_token_also_erases_the_tenant(tmp_path):
    async def scenario():
        async with _server(tmp_path) as (http, provider, st):
            client, pair = await _pair(http, provider)
            revoked = await http.post(
                "/revoke",
                data={"token": pair["refresh_token"], "client_id": client["client_id"], "client_secret": ""},
            )
            return revoked, await provider.load_access_token(pair["access_token"]), st.get_tenant(SUBJECT)

    revoked, access_after, tenant = asyncio.run(scenario())
    assert revoked.status_code == 200
    assert access_after is None
    assert tenant is None


def test_revoking_one_of_two_connections_keeps_the_tenant_for_the_other(tmp_path):
    """A tenant shared by two hosts survives until its last family is revoked."""

    async def scenario():
        async with _server(tmp_path) as (http, provider, st):
            client_a, pair_a = await _pair(http, provider)
            client_b, pair_b = await _pair(http, provider)
            await http.post(
                "/revoke",
                data={"token": pair_a["access_token"], "client_id": client_a["client_id"], "client_secret": ""},
            )
            survivor = await provider.load_access_token(pair_b["access_token"])
            return survivor, st.get_tenant(SUBJECT)

    survivor, tenant = asyncio.run(scenario())
    assert survivor is not None
    assert tenant == _tenant()


def test_revoking_the_last_token_erases_the_tenants_published_files(tmp_path):
    data = tmp_path / "data"
    artifact = data / "files" / SUBJECT / "tok" / "photo.jpg"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"jpeg")
    paths.set_data_dir_override(data)
    try:

        async def scenario():
            async with _server(tmp_path) as (http, provider, _):
                client, pair = await _pair(http, provider)
                await http.post(
                    "/revoke",
                    data={"token": pair["access_token"], "client_id": client["client_id"], "client_secret": ""},
                )

        asyncio.run(scenario())
    finally:
        paths.set_data_dir_override(None)
    assert not (data / "files" / SUBJECT).exists()


def test_revoking_an_unknown_token_changes_nothing(tmp_path):
    async def scenario():
        async with _server(tmp_path) as (http, provider, st):
            client, pair = await _pair(http, provider)
            revoked = await http.post(
                "/revoke", data={"token": "never-minted", "client_id": client["client_id"], "client_secret": ""}
            )
            return revoked, await provider.load_access_token(pair["access_token"]), st.get_tenant(SUBJECT)

    revoked, access, tenant = asyncio.run(scenario())
    assert revoked.status_code == 200  # RFC 7009: unknown tokens are not an error
    assert access is not None
    assert tenant is not None


# ------------------------------------------------------ (7) tenant row deleted
def test_a_deleted_tenant_makes_its_live_access_token_load_none(tmp_path):
    async def scenario():
        async with _server(tmp_path) as (http, provider, st):
            _, pair = await _pair(http, provider)
            before = await provider.load_access_token(pair["access_token"])
            st.delete_tenant(SUBJECT)
            after = await provider.load_access_token(pair["access_token"])
            return before, after

    before, after = asyncio.run(scenario())
    assert before is not None
    assert after is None  # BearerAuthBackend yields no user -> 401 upstream


def test_an_idle_tenant_purge_makes_a_still_valid_token_unusable_only_when_it_holds_no_token(tmp_path):
    """The sweep never takes a tenant with a live token; validating a token
    refreshes `last_used_at`, so an actively used connection is never idle."""

    async def scenario():
        async with _server(tmp_path) as (http, provider, st):
            _, pair = await _pair(http, provider)
            old = (datetime.now(timezone.utc) - timedelta(days=91)).isoformat()
            with sqlite3.connect(tmp_path / "remote.db") as db:
                db.execute("UPDATE tenants SET last_used_at = ? WHERE subject = ?", (old, SUBJECT))
            removed_while_tokens_live = st.purge_idle_tenants(days=90)
            await provider.load_access_token(pair["access_token"])  # a use restarts the clock
            with sqlite3.connect(tmp_path / "remote.db") as db:
                touched = db.execute("SELECT last_used_at FROM tenants WHERE subject = ?", (SUBJECT,)).fetchone()[0]
            return removed_while_tokens_live, touched, old

    removed, touched, old = asyncio.run(scenario())
    assert removed == []
    assert touched > old


# ------------------------------------------------------------- (8) code replay
def test_a_second_exchange_of_the_same_code_is_invalid_grant(tmp_path):
    async def scenario():
        async with _server(tmp_path) as (http, provider, _):
            client, verifier, code = await _code_flow(http, provider)
            first = await _exchange_code(http, client["client_id"], code, verifier)
            second = await _exchange_code(http, client["client_id"], code, verifier)
            return first, second

    first, second = asyncio.run(scenario())
    assert first.status_code == 200
    assert second.status_code == 400
    assert second.json()["error"] == "invalid_grant"


# ----------------------------------------------------- adversarial: bad PKCE
def test_a_wrong_code_verifier_is_invalid_grant(tmp_path):
    async def scenario():
        async with _server(tmp_path) as (http, provider, _):
            client, verifier, code = await _code_flow(http, provider)
            return await _exchange_code(http, client["client_id"], code, verifier + "x")

    r = asyncio.run(scenario())
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_grant"


# ------------------------------------------- adversarial: redirect_uri mismatch
def test_a_redirect_uri_mismatch_at_token_is_rejected(tmp_path):
    async def scenario():
        async with _server(tmp_path) as (http, provider, _):
            client, verifier, code = await _code_flow(http, provider)
            return await _exchange_code(
                http, client["client_id"], code, verifier, redirect_uri="http://127.0.0.1:49999/callback"
            )

    r = asyncio.run(scenario())
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_request"


# --------------------------------------------------- adversarial: expired code
def test_an_expired_code_is_invalid_grant(tmp_path):
    async def scenario():
        async with _server(tmp_path) as (http, provider, st):
            client = await _register(http)
            verifier, challenge = _pkce()
            st.put_code(
                AuthCode(
                    code="expired-code",
                    client_id=client["client_id"],
                    subject=SUBJECT,
                    scopes="evolution",
                    code_challenge=challenge,
                    redirect_uri=REDIRECT_URI,
                    redirect_uri_explicit=True,
                    resource=None,
                    expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
                )
            )
            return await _exchange_code(http, client["client_id"], "expired-code", verifier)

    r = asyncio.run(scenario())
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_grant"


# ------------------------------------------- adversarial: consent hand-off edge
def test_complete_consent_refuses_an_unknown_request(tmp_path):
    async def scenario():
        async with _server(tmp_path) as (_, provider, _):
            return provider.complete_consent("no-such-req", SUBJECT)

    with pytest.raises(ValueError):
        asyncio.run(scenario())


def test_a_consented_request_cannot_be_completed_twice(tmp_path):
    async def scenario():
        async with _server(tmp_path) as (http, provider, _):
            client = await _register(http)
            _, challenge = _pkce()
            r = await _authorize(http, client["client_id"], challenge)
            req = parse_qs(urlparse(r.headers["location"]).query)["req"][0]
            provider.complete_consent(req, SUBJECT)
            return provider.complete_consent(req, SUBJECT)

    with pytest.raises(ValueError):
        asyncio.run(scenario())


def test_refuse_consent_sends_access_denied_and_spends_the_request(tmp_path):
    """Given a parked authorisation, When the user refuses on the consent
    page, Then the client gets `access_denied` with its own state back, and
    the request is spent: refusing twice cannot be replayed into a code."""

    async def scenario():
        async with _server(tmp_path) as (http, provider, st):
            client = await _register(http)
            _, challenge = _pkce()
            r = await _authorize(http, client["client_id"], challenge)
            req = parse_qs(urlparse(r.headers["location"]).query)["req"][0]
            first = provider.refuse_consent(req)
            return req, first, st

    req, first, st = asyncio.run(scenario())
    query = parse_qs(urlparse(first).query)
    assert first.startswith(REDIRECT_URI)
    assert query["error"] == ["access_denied"]
    assert query["state"] == ["st-1"]  # carried from /authorize
    assert st.load_pending(req) is None


def test_refusing_an_unknown_request_discloses_nothing(tmp_path):
    """An expired or invented id has no redirect to trust, so the answer is
    this server's own address rather than a redirect an attacker chose."""

    async def scenario():
        async with _server(tmp_path) as (_, provider, _):
            return provider.refuse_consent("no-such-req")

    assert asyncio.run(scenario()) == PUBLIC_URL


# ------------------------------------------------------------ protected resource
def test_protected_resource_metadata_names_the_authorization_server(tmp_path):
    async def scenario():
        async with _server(tmp_path) as (http, _, _):
            return await http.get("/.well-known/oauth-protected-resource/mcp")

    r = asyncio.run(scenario())
    assert r.status_code == 200
    doc = r.json()
    assert doc["resource"] == SERVER  # the explicit /mcp path is kept as is
    assert doc["authorization_servers"] == [f"{PUBLIC_URL}/"]
