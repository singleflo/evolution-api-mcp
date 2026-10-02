"""The hosted server end to end: settings, routes, OAuth and tenant binding over /mcp.

Everything drives the REAL `build_app` surface through `TestClient` used as a context manager: the session manager
only starts in the lifespan. The consent page's own behaviour is covered in test_remote_consent.py; here a tenant
is stored and the authorization request is completed through the provider, which is exactly the step the consent
page performs after it verified the instance. The only double is Evolution's HTTP side (`FakeEvolution`), injected
where the hosted server resolves a tenant's client (`tenant.client_for`).

The adversarial core is the two-tenant interleave: token A reaches Evolution A, token B reaches Evolution B, on one
shared app instance: proof that no cross-binding survives stateless HTTP.

Needs the `remote` extra (`uv sync --extra remote`).
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import sqlite3
import sys
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import anyio
import pytest
from starlette.testclient import TestClient

pytest.importorskip("cryptography.fernet", reason="the store needs the [remote] extra: uv sync --extra remote")

from cryptography.fernet import Fernet  # noqa: E402
from mcp.server.auth.provider import AccessToken  # noqa: E402

import evolution_api_mcp  # noqa: E402
from evolution_api_mcp import paths, policy, registry, tenant  # noqa: E402
from evolution_api_mcp.client import EvolutionClient  # noqa: E402
from evolution_api_mcp.remote import app as remote_app  # noqa: E402
from evolution_api_mcp.remote import files  # noqa: E402
from evolution_api_mcp.remote.app import RemoteSettings, build_app  # noqa: E402
from evolution_api_mcp.remote.auth import EvolutionAuthProvider  # noqa: E402
from evolution_api_mcp.remote.store import Store, key_hash  # noqa: E402
from evolution_api_mcp.toolsets import TOOLSET_ORDER  # noqa: E402
from tests.fakes import FakeEvolution  # noqa: E402

PUBLIC_URL = "http://localhost:8000"
MCP_URL = f"{PUBLIC_URL}/mcp"
REDIRECT_URI = "http://localhost:1/callback"
EVO_A = "https://evo-a.example.test"
EVO_B = "https://evo-b.example.test"
TOKEN_A = "instance-token-a"
TOKEN_B = "instance-token-b"

_SINGLE_TENANT_ENV_VARS = (
    "EVOLUTION_API_URL",
    "EVOLUTION_INSTANCE_TOKEN",
    "EVOLUTION_MCP_TOOLSETS",
    "EVOLUTION_MCP_ALLOW",
    "EVOLUTION_MCP_DENY",
    "EVOLUTION_MCP_ALLOW_IRREVERSIBLE",
)
_REMOTE_ENV_VARS = (
    "EVOLUTION_REMOTE_PUBLIC_URL",
    "EVOLUTION_REMOTE_SECRET_KEY",
    "EVOLUTION_REMOTE_HOST",
    "EVOLUTION_REMOTE_OPENAI_CHALLENGE",
    "EVOLUTION_REMOTE_PUBLISHER",
    "EVOLUTION_REMOTE_SUPPORT_EMAIL",
    "EVOLUTION_REMOTE_ALLOW_PRIVATE_TARGETS",
    "EVOLUTION_REMOTE_ALLOWED_INTEGRATIONS",
    "EVOLUTION_MCP_DATA_DIR",
    "PORT",
)
_AUTH_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """The hosted server refuses to start with single-tenant variables present, and every test states its own
    EVOLUTION_REMOTE_* values; a developer shell exporting any of them must not change a result."""
    for name in (*_SINGLE_TENANT_ENV_VARS, *_REMOTE_ENV_VARS):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _isolated_process_state(monkeypatch):
    """build_app pins process-wide state (data root, hosted settings); none of it may leak into the next test."""
    monkeypatch.setattr(paths, "_data_dir_override", None)
    monkeypatch.setattr(tenant, "_clients", {})
    monkeypatch.setattr(tenant, "_allow_private_targets", None)
    monkeypatch.setattr(tenant, "_public_url", None)


def make_settings(tmp_path: Path, **overrides) -> RemoteSettings:
    base = RemoteSettings(
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
    return replace(base, **overrides)


def make_client(tmp_path, **overrides) -> TestClient:
    return TestClient(build_app(make_settings(tmp_path, **overrides)), base_url=PUBLIC_URL, follow_redirects=False)


def make_tenant(
    subject: str,
    *,
    base_url: str = EVO_A,
    token: str = TOKEN_A,
    instance: str = "inst-a",
    integration: str = registry.BUSINESS,
    policy_name: str = "standard",
    toolsets=frozenset(TOOLSET_ORDER),
) -> tenant.Tenant:
    return tenant.Tenant(
        subject=subject,
        base_url=base_url,
        token=token,
        instance_name=instance,
        integration=integration,
        policy=policy_name,
        toolsets=frozenset(toolsets),
    )


def _pkce():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def _rpc_body(method, params=None, id=1):
    body = {"jsonrpc": "2.0", "id": id, "method": method}
    if params is not None:
        body["params"] = params
    return body


def _rpc_result(response):
    """The JSON-RPC payload of a /mcp answer (SSE-framed or plain JSON)."""
    if "text/event-stream" in response.headers.get("content-type", ""):
        for line in response.text.splitlines():
            if line.startswith("data: "):
                return json.loads(line[len("data: ") :])
        pytest.fail(f"no SSE data frame in: {response.text[:200]!r}")
    return response.json()


def _open_store(settings: RemoteSettings) -> Store:
    return Store(settings.data_dir / "remote.db", settings.secret_key)


def _full_token(client: TestClient, settings: RemoteSettings, connected: tenant.Tenant) -> str:
    """register -> authorize -> (consent's job: store the tenant, complete the request) -> token.

    Returns the access token. The consent step is done through the provider exactly as consent.py does after it
    verified the instance, so this covers the OAuth surface of the app, not the consent form.
    """
    registered = client.post(
        "/register",
        json={
            "redirect_uris": [REDIRECT_URI],
            "client_name": "test host",
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        },
    )
    assert registered.status_code == 201, registered.text
    client_id = registered.json()["client_id"]

    verifier, challenge = _pkce()
    asked = client.get(
        "/authorize",
        params={
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "st-1",
            "scope": "evolution",
            "resource": MCP_URL,
        },
    )
    assert asked.status_code == 302, asked.text
    assert urlparse(asked.headers["location"]).path == "/consent"
    pending = parse_qs(urlparse(asked.headers["location"]).query)["req"][0]

    store = _open_store(settings)
    store.put_tenant(connected)
    redirect = EvolutionAuthProvider(store, PUBLIC_URL).complete_consent(pending, connected.subject)
    code = parse_qs(urlparse(redirect).query)["code"][0]

    exchanged = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "code_verifier": verifier,
            "redirect_uri": REDIRECT_URI,
        },
    )
    assert exchanged.status_code == 200, exchanged.text
    return exchanged.json()["access_token"]


def _mcp(client: TestClient, token: str, method: str, params: dict | None = None, id: int = 2):
    response = client.post(
        "/mcp",
        headers=_AUTH_HEADERS | {"Authorization": f"Bearer {token}"},
        json=_rpc_body(method, params, id=id),
    )
    assert response.status_code == 200, response.text
    return _rpc_result(response)


def _listed(client: TestClient, token: str) -> set[str]:
    return {t["name"] for t in _mcp(client, token, "tools/list", {})["result"]["tools"]}


@pytest.fixture
def evolutions(monkeypatch):
    """Two Evolution servers behind `tenant.client_for`: each tenant's base URL reaches ITS fake."""

    def fake_for(base: str, name: str, state: str) -> FakeEvolution:
        evo = FakeEvolution()
        evo.on("GET", "/", json={"status": 200, "version": "2.3.7"})
        evo.on(
            "GET",
            "/instance/fetchInstances",
            json=[{"name": name, "ownerJid": "393331234567@s.whatsapp.net", "profileName": f"profile of {name}"}],
        )
        evo.on("GET", f"/instance/connectionState/{name}", json={"instance": {"instanceName": name, "state": state}})
        return evo

    fakes = {EVO_A: fake_for(EVO_A, "inst-a", "open"), EVO_B: fake_for(EVO_B, "inst-b", "close")}
    clients: dict[tuple[str, str], EvolutionClient] = {}

    def client_for(t: tenant.Tenant) -> EvolutionClient:
        key = (t.base_url, t.token)
        if key not in clients:
            clients[key] = EvolutionClient(t.base_url, t.token, transport=fakes[t.base_url])
        return clients[key]

    monkeypatch.setattr(tenant, "client_for", client_for)
    return fakes


# --------------------------------------------------------- settings from env
def _set_required_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EVOLUTION_MCP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EVOLUTION_REMOTE_PUBLIC_URL", "https://evolution-mcp.example.test")
    monkeypatch.setenv("EVOLUTION_REMOTE_SECRET_KEY", Fernet.generate_key().decode())


def test_settings_from_env_parses_the_documented_variables(tmp_path, monkeypatch):
    monkeypatch.setenv("EVOLUTION_MCP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EVOLUTION_REMOTE_PUBLIC_URL", "http://localhost:8111/")
    monkeypatch.setenv("EVOLUTION_REMOTE_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("EVOLUTION_REMOTE_OPENAI_CHALLENGE", "abc")
    monkeypatch.setenv("EVOLUTION_REMOTE_ALLOW_PRIVATE_TARGETS", "1")
    monkeypatch.setenv("EVOLUTION_REMOTE_HOST", "127.0.0.1")
    monkeypatch.setenv("EVOLUTION_REMOTE_PUBLISHER", "Acme Ltd")
    monkeypatch.setenv("EVOLUTION_REMOTE_SUPPORT_EMAIL", "help@example.com")

    settings = RemoteSettings.from_env()

    assert settings.public_url == "http://localhost:8111"  # no trailing slash
    assert settings.port == 8111  # the public URL's own port by default
    assert settings.host == "127.0.0.1"
    assert settings.openai_challenge == "abc"
    assert settings.publisher == "Acme Ltd"
    assert settings.support_email == "help@example.com"
    assert settings.data_dir == tmp_path
    assert settings.allow_private_targets is True
    monkeypatch.setenv("PORT", "9000")
    assert RemoteSettings.from_env().port == 9000  # PORT still overrides


def test_settings_defaults_are_the_published_operator_and_business_only(tmp_path, monkeypatch):
    _set_required_env(monkeypatch, tmp_path)

    settings = RemoteSettings.from_env()

    assert settings.publisher == "Persevida SL"
    assert settings.support_email == "https://github.com/singleflo/evolution-api-mcp/issues"
    assert settings.allowed_integrations == frozenset({registry.BUSINESS})
    assert settings.allow_private_targets is False
    assert settings.openai_challenge is None
    assert settings.host == "0.0.0.0"
    assert settings.port == 8000  # no port in the URL and no PORT: the image's own port


@pytest.mark.parametrize(
    "value",
    ["true", "0", "yes", ""],
    ids=["true", "zero", "yes", "blank"],
)
def test_private_targets_are_allowed_only_by_the_exact_string_one(tmp_path, monkeypatch, value):
    _set_required_env(monkeypatch, tmp_path)
    monkeypatch.setenv("EVOLUTION_REMOTE_ALLOW_PRIVATE_TARGETS", value)
    assert RemoteSettings.from_env().allow_private_targets is False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("*", registry.ALL),
        ("WHATSAPP-BUSINESS", frozenset({registry.BUSINESS})),
        ("WHATSAPP-BUSINESS, WHATSAPP-BAILEYS", frozenset({registry.BUSINESS, registry.BAILEYS})),
        ("WHATSAPP-BAILEYS,WHATSAPP-BUSINESS,EVOLUTION", registry.ALL),
    ],
)
def test_allowed_integrations_accepts_a_star_or_a_comma_list(tmp_path, monkeypatch, raw, expected):
    _set_required_env(monkeypatch, tmp_path)
    monkeypatch.setenv("EVOLUTION_REMOTE_ALLOWED_INTEGRATIONS", raw)
    assert RemoteSettings.from_env().allowed_integrations == expected


@pytest.mark.parametrize("raw", ["WHATSAPP-WEB", "WHATSAPP-BUSINESS,nope", ",", "baileys"])
def test_allowed_integrations_outside_the_universe_refuse_startup_naming_the_variable(tmp_path, monkeypatch, raw):
    """A typo must not silently narrow or widen who may connect to a public deployment."""
    _set_required_env(monkeypatch, tmp_path)
    monkeypatch.setenv("EVOLUTION_REMOTE_ALLOWED_INTEGRATIONS", raw)
    with pytest.raises(RuntimeError, match="EVOLUTION_REMOTE_ALLOWED_INTEGRATIONS.*WHATSAPP-BUSINESS"):
        RemoteSettings.from_env()


@pytest.mark.parametrize("url", ["http://evil.example", "ftp://mcp.example.test", "mcp.example.test"])
def test_public_url_must_be_https_or_localhost(tmp_path, monkeypatch, url):
    _set_required_env(monkeypatch, tmp_path)
    monkeypatch.setenv("EVOLUTION_REMOTE_PUBLIC_URL", url)
    with pytest.raises(RuntimeError, match="EVOLUTION_REMOTE_PUBLIC_URL"):
        RemoteSettings.from_env()


def test_the_public_url_and_the_secret_key_are_required(tmp_path, monkeypatch):
    _set_required_env(monkeypatch, tmp_path)
    monkeypatch.delenv("EVOLUTION_REMOTE_SECRET_KEY")
    with pytest.raises(RuntimeError, match="EVOLUTION_REMOTE_SECRET_KEY"):
        RemoteSettings.from_env()

    _set_required_env(monkeypatch, tmp_path)
    monkeypatch.delenv("EVOLUTION_REMOTE_PUBLIC_URL")
    with pytest.raises(RuntimeError, match="EVOLUTION_REMOTE_PUBLIC_URL"):
        RemoteSettings.from_env()


def test_a_blank_optional_variable_means_unset(tmp_path, monkeypatch):
    """The compose file spells every optional variable `${VAR:-}`, so an operator who leaves a field blank hands the
    container a variable that EXISTS and is empty. A `get(name, default)` would push that emptiness into the legal
    pages both directories read and answer the OpenAI challenge with an empty 200 where a 404 belongs."""
    _set_required_env(monkeypatch, tmp_path)
    for blank in (
        "EVOLUTION_REMOTE_OPENAI_CHALLENGE",
        "EVOLUTION_REMOTE_PUBLISHER",
        "EVOLUTION_REMOTE_SUPPORT_EMAIL",
        "EVOLUTION_REMOTE_ALLOWED_INTEGRATIONS",
        "EVOLUTION_REMOTE_HOST",
        "PORT",
    ):
        monkeypatch.setenv(blank, "")

    settings = RemoteSettings.from_env()

    assert settings.openai_challenge is None  # None, so the route still 404s
    assert settings.publisher == "Persevida SL"
    assert settings.support_email.endswith("/issues")
    assert settings.allowed_integrations == frozenset({registry.BUSINESS})
    assert settings.host == "0.0.0.0"


def test_main_prints_help_without_requiring_environment(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["evolution-api-mcp-remote", "--help"])

    remote_app.main()

    assert "usage: evolution-api-mcp-remote" in capsys.readouterr().out


def test_main_exits_with_the_settings_error_instead_of_a_traceback(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["evolution-api-mcp-remote"])
    with pytest.raises(SystemExit) as exit_info:
        remote_app.main()
    assert "EVOLUTION_REMOTE_PUBLIC_URL" in str(exit_info.value)


# ------------------------------------------------------------ startup refusals
@pytest.mark.parametrize("name", _SINGLE_TENANT_ENV_VARS)
def test_a_single_tenant_env_var_refuses_startup_naming_it(tmp_path, monkeypatch, name):
    monkeypatch.setenv(name, "x")
    with pytest.raises(RuntimeError, match=name):
        build_app(make_settings(tmp_path))


def test_a_blank_single_tenant_env_var_does_not_refuse_startup(tmp_path, monkeypatch):
    monkeypatch.setenv("EVOLUTION_API_URL", "")
    build_app(make_settings(tmp_path))


def test_build_app_uses_a_stateless_session_manager(tmp_path):
    app = build_app(make_settings(tmp_path))

    assert app.state.session_manager.stateless is True


def test_build_app_pins_one_data_root_and_the_hosted_settings(tmp_path):
    root = tmp_path / "chosen-root"
    build_app(make_settings(root, allow_private_targets=True))

    assert paths.data_dir() == root
    assert tenant.public_url() == PUBLIC_URL
    assert tenant._allow_private_targets is True
    payload = root / "seed.txt"
    payload.write_bytes(b"seed")
    link = files.publish(payload, "t_oneroot", public_url=PUBLIC_URL)
    token = link["url"].rsplit("/", 1)[1]
    assert (root / "files" / "t_oneroot" / token / "seed.txt").exists()


# ---------------------------------------------------------------- plain routes
def test_health_reports_the_server_version(tmp_path):
    with make_client(tmp_path) as client:
        r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "version": evolution_api_mcp.__version__}


def test_the_challenge_route_echoes_the_configured_value_as_plain_text_or_404s(tmp_path):
    with make_client(tmp_path, openai_challenge="abc") as client:
        answered = client.get("/.well-known/openai-apps-challenge")
    assert answered.status_code == 200
    assert answered.text == "abc"
    assert answered.headers["content-type"].startswith("text/plain")
    with make_client(tmp_path) as client:
        missing = client.get("/.well-known/openai-apps-challenge")
    assert missing.status_code == 404


def test_privacy_is_html_with_the_substituted_publisher(tmp_path):
    with make_client(tmp_path, publisher="Acme Ltd") as client:
        r = client.get("/privacy")
        landing = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Acme Ltd" in r.text
    assert "{{PUBLISHER}}" not in r.text and "{{SUPPORT_EMAIL}}" not in r.text
    assert landing.status_code == 200
    assert "/mcp" in landing.text and "/privacy" in landing.text


def test_the_landing_body_names_its_independence_before_the_footer(tmp_path):
    """The footer carries the sentence too, so the slice matters: asserting on the whole page would pass vacuously.
    Both directories reject anything implying endorsement by a third party, and a reviewer reads the headline."""
    with make_client(tmp_path) as client:
        page = client.get("/")

    body = page.text.split("<footer")[0]
    assert "Not affiliated with" in body
    for third_party in ("Meta", "WhatsApp", "Evolution API project"):
        assert third_party in body


def test_the_landing_promises_exactly_the_tools_a_business_connection_gets(tmp_path):
    with make_client(tmp_path) as client:
        page = client.get("/")

    assert "Up to 38 tools" in page.text
    assert "WHATSAPP-BUSINESS" in page.text
    assert "uvx evolution-api-mcp" in page.text
    # A toolset the Business integration has no tool for is not advertised.
    assert "<strong>groups</strong>" not in page.text
    assert "<strong>templates</strong>" in page.text


def test_a_widened_deployment_advertises_the_wider_surface(tmp_path):
    with make_client(tmp_path, allowed_integrations=registry.ALL) as client:
        page = client.get("/")

    assert "<strong>groups</strong>" in page.text
    assert "Up to 38 tools" not in page.text


def test_every_page_is_built_for_a_phone_and_refuses_framing(tmp_path):
    """Without the viewport the pages render zoomed out in the in-app browsers of Claude and ChatGPT, which is where
    the consent form is actually opened; without `frame-ancestors` a consent page can be framed and clicked
    through, which the MCP specification's consent-UI rules require refusing."""
    with make_client(tmp_path) as client:
        pages = {path: client.get(path) for path in ("/", "/privacy", "/terms", "/support")}

    for path, page in pages.items():
        assert page.status_code == 200, path
        assert 'name="viewport"' in page.text, path
        assert page.headers["x-frame-options"] == "DENY", path
        assert page.headers["referrer-policy"] == "no-referrer", path
        assert page.headers["x-content-type-options"] == "nosniff", path
        csp = page.headers["content-security-policy"]
        assert "frame-ancestors 'none'" in csp, path
        assert "default-src 'none'" in csp, path


def test_the_policy_never_constrains_form_action(tmp_path):
    """Chrome and Safari check `form-action` against the whole redirect chain a form starts, and the consent POST
    answers 302 to the client's callback: that redirect IS the authorization response. Callbacks cannot be listed
    ahead of time under dynamic client registration, so the directive can only be absent."""
    with make_client(tmp_path) as client:
        gone = client.get("/consent")  # no req: the error page, still HTML

    policy_header = gone.headers["content-security-policy"]
    assert "form-action" not in policy_header
    assert "frame-ancestors 'none'" in policy_header


def test_the_inline_script_is_admitted_by_its_own_hash(tmp_path):
    """Hashing what the page actually served is the point: an edit to the script that forgets the policy would leave
    a page whose own browser refuses to run it."""
    with make_client(tmp_path) as client:
        page = client.get("/")

    script = page.text.split("<script>")[1].split("</script>")[0]
    digest = base64.b64encode(hashlib.sha256(script.encode("utf-8")).digest()).decode()
    assert f"script-src 'sha256-{digest}'" in page.headers["content-security-policy"]
    assert page.text.count("<script") == 1  # nothing else to admit


def test_the_stylesheet_serves_itself_and_is_cached_by_version(tmp_path):
    with make_client(tmp_path) as client:
        css = client.get("/style.css")
        landing = client.get("/")

    assert css.status_code == 200
    assert css.headers["content-type"].startswith("text/css")
    assert "immutable" in css.headers["cache-control"]
    assert f'href="/style.css?v={evolution_api_mcp.__version__}"' in landing.text


def test_the_privacy_table_reaches_the_browser_as_a_table(tmp_path):
    with make_client(tmp_path) as client:
        privacy = client.get("/privacy")

    assert "<table>" in privacy.text
    assert "<p>|" not in privacy.text


def test_policy_pages_render_inline_code_without_literal_backticks(tmp_path):
    with make_client(tmp_path) as client:
        pages = {path: client.get(path) for path in ("/privacy", "/terms", "/support")}

    for path, page in pages.items():
        assert page.status_code == 200, path
        assert "`" not in page.text, path
    assert any("<code>" in page.text for page in pages.values())


def test_inline_md_escapes_link_targets_for_attribute_context():
    """Every interpolated value is escaped: a CSP that blocks execution does not make an injected attribute
    acceptable. A URL carrying a query string must not be double-escaped."""
    result = remote_app._inline_md('[x](https://safe.example"onmouseover=alert(1))')
    assert "&quot;" in result
    assert not re.search(r"\sonmouseover", result)

    query_result = remote_app._inline_md("[x](https://example.com/a?b=1&c=2)")
    assert "b=1&amp;c=2" in query_result
    assert "&amp;amp;" not in query_result


def test_support_destination_renders_as_link(tmp_path):
    """The destination is operator-supplied and may be an address or a URL: the shape decides the link scheme."""
    with make_client(tmp_path) as client:
        pages = [client.get(path) for path in ("/support", "/privacy", "/terms")]
    for page in pages:
        assert '<a href="https://github.com/singleflo/evolution-api-mcp/issues"' in page.text

    with make_client(tmp_path, support_email="help@example.com") as client:
        pages = [client.get(path) for path in ("/support", "/privacy", "/terms")]
    for page in pages:
        assert '<a href="mailto:help@example.com"' in page.text


# ----------------------------------------------------------------- OAuth / MCP
def test_no_bearer_on_mcp_is_a_401_naming_the_resource_metadata(tmp_path):
    with make_client(tmp_path) as client:
        r = client.post("/mcp", json=_rpc_body("initialize", {}), headers=_AUTH_HEADERS)
    assert r.status_code == 401
    assert "resource_metadata=" in r.headers["www-authenticate"]
    assert "oauth-protected-resource/mcp" in r.headers["www-authenticate"]


def test_protected_resource_metadata_names_this_server_as_its_authority(tmp_path):
    with make_client(tmp_path) as client:
        r = client.get("/.well-known/oauth-protected-resource/mcp")
    assert r.status_code == 200
    doc = r.json()
    assert [u.rstrip("/") for u in doc["authorization_servers"]] == [PUBLIC_URL]
    assert doc["resource"].rstrip("/") == MCP_URL
    assert doc["scopes_supported"] == ["evolution"]


def test_the_authorization_server_advertises_pkce_and_registration(tmp_path):
    with make_client(tmp_path) as client:
        doc = client.get("/.well-known/oauth-authorization-server").json()
    assert doc["code_challenge_methods_supported"] == ["S256"]
    assert doc["registration_endpoint"].endswith("/register")
    assert doc["revocation_endpoint"].endswith("/revoke")
    assert doc["scopes_supported"] == ["evolution"]


def test_the_authorize_redirect_lands_on_the_consent_page(tmp_path):
    """/authorize parks the request and sends the browser to /consent?req=..., where the app's consent handlers
    answer (a request id they do not know is a page, not a crash)."""
    settings = make_settings(tmp_path)
    with TestClient(build_app(settings), base_url=PUBLIC_URL, follow_redirects=False) as client:
        registered = client.post(
            "/register",
            json={"redirect_uris": [REDIRECT_URI], "token_endpoint_auth_method": "none"},
        )
        _, challenge = _pkce()
        asked = client.get(
            "/authorize",
            params={
                "client_id": registered.json()["client_id"],
                "redirect_uri": REDIRECT_URI,
                "response_type": "code",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": "evolution",
                "resource": MCP_URL,
            },
        )
        consent_page = client.get(asked.headers["location"])
        unknown = client.get("/consent", params={"req": "not-a-request"})

    assert consent_page.status_code == 200
    assert "text/html" in consent_page.headers["content-type"]
    assert unknown.status_code in (400, 404)


def test_full_oauth_flow_then_initialize_names_the_server(tmp_path):
    settings = make_settings(tmp_path)
    with TestClient(build_app(settings), base_url=PUBLIC_URL, follow_redirects=False) as client:
        token = _full_token(client, settings, make_tenant("t_a"))
        answer = _mcp(
            client,
            token,
            "initialize",
            {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}},
            id=1,
        )

    info = answer["result"]["serverInfo"]
    assert info["name"] == "evolution-api-mcp"
    assert info["version"] == evolution_api_mcp.__version__


def test_a_token_whose_tenant_row_is_deleted_answers_401(tmp_path):
    settings = make_settings(tmp_path)
    with TestClient(build_app(settings), base_url=PUBLIC_URL, follow_redirects=False) as client:
        token = _full_token(client, settings, make_tenant("t_a"))
        assert _listed(client, token)
        _open_store(settings).delete_tenant("t_a")

        gone = client.post(
            "/mcp",
            headers=_AUTH_HEADERS | {"Authorization": f"Bearer {token}"},
            json=_rpc_body("tools/list", {}, id=4),
        )
    assert gone.status_code == 401
    assert "resource_metadata=" in gone.headers["www-authenticate"]


# --------------------------------------------------------- what a tenant sees
def test_a_standard_business_tenant_with_every_toolset_sees_exactly_the_38_store_tools(tmp_path):
    settings = make_settings(tmp_path)
    with TestClient(build_app(settings), base_url=PUBLIC_URL, follow_redirects=False) as client:
        token = _full_token(client, settings, make_tenant("t_a"))
        names = _listed(client, token)

    assert len(names) == 38
    specs = {spec.name: spec for spec in registry.specs()}
    assert {"list_chats", "read_messages", "search_messages", "list_templates", "send_text_message"} <= names
    for name in names:
        spec = specs[name]
        assert not spec.local_only, name
        assert registry.BUSINESS in spec.integrations, name
        assert spec.kind != "irreversible", name
        assert name not in policy.DEFAULT_DENY, name
    # Withheld on purpose: WhatsApp Web-only tools, secret-taking tools, irreversible and default-denied ones.
    assert not names & {"send_poll", "list_groups", "set_webhook", "create_chatbot", "delete_template", "post_status"}


def test_a_read_only_business_tenant_sees_only_reads(tmp_path):
    settings = make_settings(tmp_path)
    connected = make_tenant("t_ro", policy_name="read")
    with TestClient(build_app(settings), base_url=PUBLIC_URL, follow_redirects=False) as client:
        token = _full_token(client, settings, connected)
        names = _listed(client, token)

    expected = {
        spec.name
        for spec in registry.specs()
        if spec.kind == "read" and not spec.local_only and registry.BUSINESS in spec.integrations
    }
    assert names == expected
    assert {"list_chats", "read_messages", "list_templates", "get_instance_status"} <= names
    assert "send_text_message" not in names


def test_the_toolsets_ticked_at_consent_bound_the_listing(tmp_path):
    settings = make_settings(tmp_path)
    connected = make_tenant("t_two", toolsets=frozenset({"chats"}))
    with TestClient(build_app(settings), base_url=PUBLIC_URL, follow_redirects=False) as client:
        token = _full_token(client, settings, connected)
        names = _listed(client, token)

    specs = {spec.name: spec for spec in registry.specs()}
    assert "read_messages" in names
    assert "get_instance_status" in names  # universal: always there
    assert {specs[n].toolset for n in names} == {"chats", "instance"}


def test_a_baileys_tenant_on_a_widened_deployment_gets_baileys_tools_but_never_the_denied_ones(tmp_path):
    settings = make_settings(tmp_path, allowed_integrations=registry.ALL)
    connected = make_tenant("t_bai", integration=registry.BAILEYS)
    with TestClient(build_app(settings), base_url=PUBLIC_URL, follow_redirects=False) as client:
        token = _full_token(client, settings, connected)
        names = _listed(client, token)

    assert {"send_poll", "list_groups", "add_group_participants"} <= names
    assert "list_templates" not in names
    assert not names & (policy.DEFAULT_DENY | {"logout_instance", "leave_group", "delete_message_for_everyone"})


def test_a_refused_tool_call_is_a_tool_error_with_the_hosted_wording(tmp_path):
    settings = make_settings(tmp_path)
    with TestClient(build_app(settings), base_url=PUBLIC_URL, follow_redirects=False) as client:
        token = _full_token(client, settings, make_tenant("t_ro", policy_name="read"))
        answer = _mcp(
            client,
            token,
            "tools/call",
            {"name": "send_text_message", "arguments": {"chat": "393331234567", "text": "Hello"}},
            id=3,
        )

    result = answer["result"]
    assert result["isError"] is True
    assert (
        "send_text_message changes data, and this connection was authorised as read-only. "
        "Reconnect and choose the standard policy to allow it."
    ) in result["content"][0]["text"]


def test_each_token_reaches_only_its_own_tenants_evolution(tmp_path, evolutions):
    settings = make_settings(tmp_path)
    with TestClient(build_app(settings), base_url=PUBLIC_URL, follow_redirects=False) as client:
        token_a = _full_token(client, settings, make_tenant("t_a"))
        token_b = _full_token(
            client,
            settings,
            make_tenant("t_b", base_url=EVO_B, token=TOKEN_B, instance="inst-b", policy_name="read"),
        )

        seen = []
        for token in (token_a, token_b, token_a):
            answer = _mcp(client, token, "tools/call", {"name": "get_instance_status", "arguments": {}}, id=3)
            assert answer["result"].get("isError") is not True, answer
            seen.append(json.loads(answer["result"]["content"][0]["text"]))

    assert [(s["instance"], s["state"], s["policy"]) for s in seen] == [
        ("inst-a", "open", "standard"),
        ("inst-b", "close", "read"),
        ("inst-a", "open", "standard"),
    ]
    assert {s["server"] for s in seen} == {"hosted"}
    assert seen[0]["profile_name"] == "profile of inst-a"
    # Each Evolution only ever saw its own instance token.
    for base, token in ((EVO_A, TOKEN_A), (EVO_B, TOKEN_B)):
        assert evolutions[base].requests
        assert {r.headers["apikey"] for r in evolutions[base].requests} == {token}


# ------------------------------------------------------------ retention
def test_revoking_the_last_token_deletes_the_tenant_and_its_files(tmp_path):
    """Retention must match what the privacy page promises: the tenant row and its disk tree go with the last token."""
    settings = make_settings(tmp_path)
    with TestClient(build_app(settings), base_url=PUBLIC_URL, follow_redirects=False) as client:
        token = _full_token(client, settings, make_tenant("t_a"))
        store = _open_store(settings)
        assert store.find_tenant_by_key_hash(key_hash(EVO_A, TOKEN_A)).subject == "t_a"
        file_dir = tmp_path / "files" / "t_a" / "tok"
        file_dir.mkdir(parents=True)
        (file_dir / "photo.jpg").write_bytes(b"fake")
        provider = EvolutionAuthProvider(store, PUBLIC_URL)

        anyio.run(
            provider.revoke_token,
            AccessToken(token=token, client_id="test", scopes=[], expires_at=0, subject="t_a"),
        )

    assert store.get_tenant("t_a") is None
    assert not (tmp_path / "files" / "t_a").exists()


def test_the_retention_sweep_runs_periodically_not_only_at_startup(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(remote_app, "_sweep_once", lambda store: calls.append(1))
    monkeypatch.setattr(remote_app, "SWEEP_SECONDS", 0.1)

    with make_client(tmp_path):
        after_startup = len(calls)
        time.sleep(0.45)

    assert after_startup == 1
    assert len(calls) - after_startup >= 2


def test_startup_sweep_purges_an_idle_tenants_artifacts(tmp_path):
    settings = make_settings(tmp_path)
    seed = _open_store(settings)
    seed.init()
    seed.put_tenant(make_tenant("t_idle"))
    with sqlite3.connect(settings.data_dir / "remote.db") as db:
        db.execute(
            "UPDATE tenants SET last_used_at = ? WHERE subject = 't_idle'",
            ((datetime.now(timezone.utc) - timedelta(days=91)).isoformat(),),
        )
    file_dir = tmp_path / "files" / "t_idle" / "tok"
    file_dir.mkdir(parents=True)
    (file_dir / "old.jpg").write_bytes(b"fake")

    with TestClient(build_app(settings), base_url=PUBLIC_URL, follow_redirects=False):
        pass  # the lifespan runs the startup sweep

    assert seed.get_tenant("t_idle") is None
    assert not (tmp_path / "files" / "t_idle").exists()


def test_startup_purges_expired_files_but_keeps_live_ones(tmp_path):
    """An expired file row answers 404 after the lifespan's sweep; a live one still serves."""
    paths.set_data_dir_override(tmp_path)
    payload = tmp_path / "doc.txt"
    payload.write_bytes(b"payload")
    expired = files.publish(payload, "t_seed", public_url=PUBLIC_URL, ttl_minutes=0)
    payload.write_bytes(b"payload")
    fresh = files.publish(payload, "t_seed", public_url=PUBLIC_URL)

    with make_client(tmp_path) as client:
        gone = client.get(f"/files/{expired['url'].rsplit('/', 1)[-1]}")
        alive = client.get(f"/files/{fresh['url'].rsplit('/', 1)[-1]}")

    assert gone.status_code == 404
    assert alive.status_code == 200
    assert alive.content == b"payload"
