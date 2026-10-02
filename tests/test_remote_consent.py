"""The consent page: form, SSRF guard, verification, integration rule, tenant reuse.

The page is public and unauthenticated by SDK design, so the tests assert the things that make that safe, not just
a 200: the private-address refusal happens BEFORE any connection attempt, the instance token is never echoed back
(not even escaped), Evolution's error text lands in the page html-escaped (it is untrusted external text in a
browser), an oversize body dies at 413 before any parsing, and a re-consent reuses the tenant instead of minting
subjects.

`consent._verify_isolated` and `consent.socket.getaddrinfo` are the two seams for the flow tests: both are patched
through the consent module's own namespace. The verifier runs in a spawn child that re-imports the module fresh,
so the flow tests pin the PARENT-side seam (the credentials consent hands it), and three tests run the real child
against a real local HTTP server that speaks Evolution's documented answers: a working token, the server's global
key, and the connect-time guard that refuses a private address even when the pre-check was passed.

Needs the `remote` extra (`uv sync --extra remote`): cryptography lives there, not in the base environment a stdio
install resolves.
"""

import json
import re
import socket
import sqlite3
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

pytest.importorskip("cryptography.fernet", reason="the store needs the [remote] extra: uv sync --extra remote")

from cryptography.fernet import Fernet  # noqa: E402
from mcp.shared.auth import OAuthClientInformationFull  # noqa: E402
from pydantic import AnyUrl  # noqa: E402

from evolution_api_mcp import discovery  # noqa: E402
from evolution_api_mcp.client import EvolutionHTTPError, EvolutionUncertain, EvolutionUnreachable  # noqa: E402
from evolution_api_mcp.registry import ALL, BAILEYS, BUSINESS  # noqa: E402
from evolution_api_mcp.remote import consent  # noqa: E402
from evolution_api_mcp.remote.consent import ConsentDeps  # noqa: E402
from evolution_api_mcp.remote.store import PENDING_TTL, PendingAuthz, Store, key_hash  # noqa: E402
from evolution_api_mcp.toolsets import DEFAULT_TOOLSETS, TOOLSET_ORDER, TOOLSETS  # noqa: E402

PUBLIC_URL = "https://mcp.example.test"
REDIRECT_URI = "http://127.0.0.1:43123/callback"
EVO_URL = "https://evolution.example.com"
TOKEN = "3F1C2A9E-7B4D-4E55-9A10-0C6D2B7E8F41"
INSTANCE = "shop-line"
BUSINESS_ONLY_REFUSAL = (
    "This hosted server connects WhatsApp Business Platform instances only (integration WHATSAPP-BUSINESS). "
    "This instance uses WHATSAPP-BAILEYS; run the local server (uvx evolution-api-mcp) for it."
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class FakeProvider:
    """The two methods consent calls on the auth provider: consume and answer with the final redirect URL of the
    client that asked for consent, carrying a code when the user approved, `access_denied` when refused."""

    def __init__(self):
        self.calls = []
        self.refusals = []

    def complete_consent(self, pending_id: str, subject: str) -> str:
        self.calls.append((pending_id, subject))
        return f"{REDIRECT_URI}?code=c-abc123&state=st-1"

    def refuse_consent(self, pending_id: str) -> str:
        self.refusals.append(pending_id)
        return f"{REDIRECT_URI}?error=access_denied&state=st-1"


class FakeVerify:
    """consent._verify_isolated: records what consent hands over and answers from a programmed outcome."""

    def __init__(self):
        self.outcome = consent._Verified("ok", "", None, INSTANCE, BUSINESS)
        self.calls = []

    def __call__(self, base_url, token, allow_private):
        self.calls.append({"base_url": base_url, "token": token, "allow_private": allow_private})
        return self.outcome

    def refuse(self, detail: str) -> None:
        self.outcome = consent._Verified("error", detail, None)


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    """The sandbox has no DNS for `evolution.example.com`; every flow test resolves it to a public address unless
    it installs its own resolver (later `setattr` calls replace this one)."""

    def public(host, port=None, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]

    monkeypatch.setattr("evolution_api_mcp.remote.consent.socket.getaddrinfo", public)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "remote.db"


@pytest.fixture
def store(db_path):
    s = Store(db_path, Fernet.generate_key().decode())
    s.init()
    s.put_pending(_pending_row())  # one live authorisation waiting for consent
    return s


@pytest.fixture
def verify(monkeypatch):
    recorder = FakeVerify()
    monkeypatch.setattr(consent, "_verify_isolated", recorder)
    return recorder


@pytest.fixture
def provider():
    return FakeProvider()


def _pending_row(**kw) -> PendingAuthz:
    row = PendingAuthz(
        id="pend-1",
        client_id="cid-1",
        redirect_uri=REDIRECT_URI,
        redirect_uri_explicit=True,
        scopes="evolution",
        code_challenge="challenge-x",
        resource=None,
        state="st-1",
        expires_at=_now() + PENDING_TTL,
    )
    return replace(row, **kw)


def _form(**over):
    """A form body as a browser sends it: a dict, with `toolsets` as a list for the repeated checkbox."""
    data = {
        "req": "pend-1",
        "base_url": EVO_URL,
        "instance_token": TOKEN,
        "policy": "read",
        "toolsets": ["messaging", "chats", "contacts"],
    }
    data.update(over)
    return {k: v for k, v in data.items() if v is not None}


def client(store, provider, *, allow_private_targets=False, allowed_integrations=frozenset({BUSINESS})):
    """A bare Starlette app with only the two consent routes. Raw answers only: the redirect target belongs to the
    client, not to this bare app, so following it would just manufacture a 404."""
    app = Starlette(
        routes=[
            Route("/consent", consent.consent_form, methods=["GET"]),
            Route("/consent", consent.consent_submit, methods=["POST"]),
        ]
    )
    app.state.consent_deps = ConsentDeps(
        store=store,
        provider=provider,
        public_url=PUBLIC_URL,
        allow_private_targets=allow_private_targets,
        allowed_integrations=allowed_integrations,
        publisher="Persevida SL",
    )
    return TestClient(app, follow_redirects=False)


def _fake_resolver(monkeypatch, address):
    def fake_getaddrinfo(host, port=None, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (address, 0))]

    monkeypatch.setattr("evolution_api_mcp.remote.consent.socket.getaddrinfo", fake_getaddrinfo)


def _checked_toolsets(page: str) -> set[str]:
    return {m.group(1) for m in re.finditer(r'name="toolsets" value="([a-z]+)" checked', page)}


# ---------------------------------------------------------------- the form
def test_the_form_lists_every_toolset_and_pre_checks_only_the_three_core_ones(store, provider):
    """Given a pending authorisation, When the form renders, Then every toolset has a checkbox with its
    description, messaging/chats/contacts are ticked and nothing else, the policy defaults to read-only and the
    fields carry the wording of the consent contract."""
    shown = client(store, provider).get("/consent?req=pend-1")

    assert shown.status_code == 200
    page = shown.text
    for name in TOOLSET_ORDER:
        assert f'name="toolsets" value="{name}"' in page
        assert TOOLSETS[name].split(".")[0].replace("'", "&#x27;") in page
    assert _checked_toolsets(page) == set(DEFAULT_TOOLSETS) == {"messaging", "chats", "contacts"}
    assert 'name="req" value="pend-1"' in page
    assert 'name="base_url" type="url"' in page
    assert 'placeholder="https://evolution.example.com"' in page
    assert 'name="instance_token" type="password"' in page
    assert "The token of this one instance: in Evolution Manager open the instance and copy its token" in page
    assert "The server's global API key is refused." in page
    assert 'value="read" checked' in page
    assert 'value="standard"' in page and 'value="standard" checked' not in page
    assert "Read and act: send messages, manage chats and configuration" in page
    assert "Read only: nothing is sent or changed" in page
    assert f'href="{PUBLIC_URL}/privacy"' in page
    assert "connects WhatsApp Business Platform instances only" in page


def test_the_assurance_section_states_what_the_token_is_for_and_what_is_never_offered(store, provider):
    """The page that asks for a credential says, before the footer, who runs it, where the token goes and what the
    hosted server will never do."""
    body = client(store, provider).get("/consent?req=pend-1").text.split("<footer")[0]

    assert (
        "independent project of Persevida SL, not affiliated with Meta, WhatsApp or the Evolution API project" in body
    )
    assert "stored encrypted and used only to call the Evolution server you typed above" in body
    assert "Disconnecting deletes it." in body
    assert "Irreversible actions (logging out, deleting a message for everyone, leaving a group" in body
    assert "tools that take secrets are never available" in body
    assert "WhatsApp Business Messaging Policy applies: opt-in" in body


def test_the_form_names_who_is_asking_and_where_the_access_returns(store, provider):
    """The MCP specification's consent-UI rules require the requesting client, the redirect and the scope on the
    page. The registered name is attacker-supplied text, so it must arrive escaped rather than as markup."""
    store.put_client(
        OAuthClientInformationFull(
            client_id="cid-1",
            client_name="Claude <Desktop>",
            redirect_uris=[AnyUrl(REDIRECT_URI)],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            scope="evolution",
            token_endpoint_auth_method="none",
        )
    )

    shown = client(store, provider).get("/consent?req=pend-1")

    assert "Claude &lt;Desktop&gt;" in shown.text
    assert "<Desktop>" not in shown.text
    assert REDIRECT_URI in shown.text
    assert "<code>evolution</code>" in shown.text


def test_an_unregistered_client_is_named_by_its_id_rather_than_left_blank(store, provider):
    assert "cid-1" in client(store, provider).get("/consent?req=pend-1").text


def test_the_waiting_affordance_ships_with_the_page_and_the_refuse_button_has_its_own_label(store, provider):
    """Pressing Connect waits on a live connection to someone's Evolution server, up to `_VERIFY_TIMEOUT`. The note
    that explains the wait ships hidden with the page; the Refuse button never makes a live check, so it carries
    its own busy label and the CSS reveals the note only while the primary button is busy."""
    from importlib import resources

    page = client(store, provider).get("/consent?req=pend-1").text
    css = (resources.files("evolution_api_mcp.remote") / "pages" / "style.css").read_text(encoding="utf-8")

    assert 'class="sending-note"' in page and 'role="status"' in page
    assert "up to twenty seconds" in page
    assert 'data-busy-label="Connecting' in page
    assert 'data-busy-label="Refusing…"' in page
    assert "e.submitter||" in page
    assert ".is-sending:has(button.primary.is-busy) .sending-note" in css


# ------------------------------------------------------------- happy path
def test_happy_path_stores_an_encrypted_tenant_and_redirects_with_a_code(db_path, store, verify, provider):
    """Given a pending authorisation, When the user posts the Evolution URL and token, Then the redirect carries a
    code, the provider got the pending id and one t_ subject, the tenant holds what discovery reported plus the
    chosen policy and toolsets, and the raw database file holds no plaintext token."""
    c = client(store, provider)

    answer = c.post("/consent", data=_form(policy="standard", toolsets=["messaging", "settings"]))

    assert answer.status_code == 302
    assert answer.headers["location"].startswith(REDIRECT_URI)
    assert "code=" in answer.headers["location"]
    assert len(provider.calls) == 1 and provider.calls[0][0] == "pend-1"
    subject = provider.calls[0][1]
    assert subject.startswith("t_")
    assert verify.calls == [{"base_url": EVO_URL, "token": TOKEN, "allow_private": False}]
    tenant = store.get_tenant(subject)
    assert (tenant.base_url, tenant.token) == (EVO_URL, TOKEN)
    assert (tenant.instance_name, tenant.integration) == (INSTANCE, BUSINESS)
    assert tenant.policy == "standard"
    assert tenant.toolsets == frozenset({"messaging", "settings"})
    assert store.find_tenant_by_key_hash(key_hash(EVO_URL, TOKEN)).subject == subject
    blob = db_path.read_bytes()
    wal = db_path.with_name("remote.db-wal")
    if wal.exists():
        blob += wal.read_bytes()
    assert TOKEN.encode() not in blob


def test_a_trailing_slash_and_stray_spaces_are_trimmed_before_verification(store, verify, provider):
    answer = client(store, provider).post(
        "/consent", data=_form(base_url=f"  {EVO_URL}/  ", instance_token=f" {TOKEN} ")
    )

    assert answer.status_code == 302
    assert verify.calls[0]["base_url"] == EVO_URL
    assert verify.calls[0]["token"] == TOKEN


def test_a_second_consent_with_the_same_url_and_token_reuses_the_subject(db_path, store, verify, provider):
    """Given a tenant already stored for this URL+token, When the user reconnects through a new pending request
    and picks another policy and toolsets, Then the SAME subject is reused with the new choices: one tenant row,
    no subject proliferation."""
    c = client(store, provider)
    c.post("/consent", data=_form())
    first_subject = provider.calls[0][1]
    store.put_pending(_pending_row(id="pend-2"))
    verify.outcome = consent._Verified("ok", "", None, "renamed-line", BUSINESS)

    answer = c.post("/consent", data=_form(req="pend-2", policy="standard", toolsets=["templates", "chats"]))

    assert answer.status_code == 302
    assert provider.calls[1][1] == first_subject
    tenant = store.get_tenant(first_subject)
    assert tenant.policy == "standard"
    assert tenant.toolsets == frozenset({"templates", "chats"})
    assert tenant.instance_name == "renamed-line"
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT count(*) FROM tenants").fetchone()[0] == 1


def test_a_repeated_toolsets_field_keeps_every_value_while_other_fields_keep_the_first(store, verify, provider):
    """`toolsets` is a repeated checkbox and every submitted value counts; for every other field the first value
    wins, so a duplicated `policy` cannot smuggle a second choice in."""
    body = (
        "req=pend-1&base_url=https%3A%2F%2Fevolution.example.com&instance_token=tok"
        "&policy=read&policy=standard&toolsets=groups&toolsets=labels&toolsets=not-a-toolset"
    )

    answer = client(store, provider).post(
        "/consent", content=body, headers={"content-type": "application/x-www-form-urlencoded"}
    )

    assert answer.status_code == 302
    tenant = store.get_tenant(provider.calls[0][1])
    assert tenant.policy == "read"
    assert tenant.toolsets == frozenset({"groups", "labels"})


# --------------------------------------------------------------- refusals
def test_refusing_answers_access_denied_and_touches_nothing(db_path, store, provider, verify):
    """Given the form open, When the user presses Refuse, Then the browser is sent back to the client with
    `access_denied`, no Evolution server is contacted and no tenant is stored."""
    answer = client(store, provider).post("/consent", data={"req": "pend-1", "action": "deny"})

    assert answer.status_code == 302
    assert "error=access_denied" in answer.headers["location"]
    assert provider.refusals == ["pend-1"]
    assert provider.calls == []
    assert verify.calls == []
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT count(*) FROM tenants").fetchone()[0] == 0


def test_an_empty_toolset_selection_re_renders_the_form_and_dials_nothing(store, verify, provider):
    """Given every checkbox cleared, When posted, Then the form asks for at least one toolset, keeps the URL and
    the chosen policy, and never contacts Evolution."""
    answer = client(store, provider).post("/consent", data=_form(toolsets=None, policy="standard"))

    assert answer.status_code == 200
    assert "Choose at least one toolset." in answer.text
    assert _checked_toolsets(answer.text) == set()
    assert 'value="standard" checked' in answer.text
    assert EVO_URL in answer.text
    assert TOKEN not in answer.text
    assert verify.calls == [] and provider.calls == []


def test_only_unknown_toolset_values_count_as_none(store, verify, provider):
    """A tampered form naming a toolset that does not exist selects nothing."""
    answer = client(store, provider).post("/consent", data=_form(toolsets=["admin", "everything"]))

    assert answer.status_code == 200
    assert "Choose at least one toolset." in answer.text
    assert verify.calls == []


def test_missing_fields_re_render_the_form(store, verify, provider):
    answer = client(store, provider).post("/consent", data=_form(base_url="", instance_token=""))

    assert answer.status_code == 200
    assert "Fill in the Evolution URL and the instance token." in answer.text
    assert verify.calls == [] and provider.calls == []


def test_a_missing_or_unknown_policy_re_renders_the_form(store, verify, provider):
    c = client(store, provider)

    assert "Choose what the assistant may do." in c.post("/consent", data=_form(policy=None)).text
    assert "Choose what the assistant may do." in c.post("/consent", data=_form(policy="admin")).text
    assert verify.calls == [] and provider.calls == []


@pytest.mark.parametrize(
    "url",
    [
        "http://evolution.example.com",
        "ftp://evolution.example.com",
        "https://user:pw@evolution.example.com",
        "https://x:99999",
    ],
)
def test_only_https_urls_without_credentials_are_accepted(store, verify, provider, url):
    """Given an http:// URL to a remote host (or one carrying credentials or a broken port), When submitted, Then
    the form re-renders asking for https and nothing is dialed."""
    answer = client(store, provider).post("/consent", data=_form(base_url=url))

    assert answer.status_code == 200
    assert "must be an https:// address" in answer.text
    assert verify.calls == [] and provider.calls == []


@pytest.mark.parametrize("url", ["http://localhost:8080", "http://127.0.0.1:8080"])
def test_http_is_accepted_for_a_server_on_this_machine(store, verify, provider, monkeypatch, url):
    _fake_resolver(monkeypatch, "127.0.0.1")

    answer = client(store, provider, allow_private_targets=True).post("/consent", data=_form(base_url=url))

    assert answer.status_code == 302
    assert verify.calls[0]["base_url"] == url
    assert verify.calls[0]["allow_private"] is True


@pytest.mark.parametrize("address", ["169.254.169.254", "10.0.0.5", "100.64.0.1", "::1", "::ffff:127.0.0.1"])
def test_private_addresses_are_refused_before_any_connection(store, verify, provider, monkeypatch, address):
    """Given a URL that resolves into a refused range, When submitted, Then the page answers 'public host' and
    verification is NEVER invoked: the unauthenticated endpoint must not become an internal prober."""

    def fake_getaddrinfo(host, port=None, *args, **kwargs):
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 0, "", (address, 0))]

    monkeypatch.setattr("evolution_api_mcp.remote.consent.socket.getaddrinfo", fake_getaddrinfo)

    answer = client(store, provider).post("/consent", data=_form())

    assert answer.status_code == 200
    assert "The Evolution URL must be a public host." in answer.text
    assert verify.calls == [] and provider.calls == []


def test_one_private_address_among_public_ones_refuses_the_host(store, verify, provider, monkeypatch):
    def fake_getaddrinfo(host, port=None, *args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.5", 0)),
        ]

    monkeypatch.setattr("evolution_api_mcp.remote.consent.socket.getaddrinfo", fake_getaddrinfo)

    answer = client(store, provider).post("/consent", data=_form())

    assert "public host" in answer.text
    assert verify.calls == []


def test_an_unresolvable_host_is_reported_and_not_dialed(store, verify, provider, monkeypatch):
    def failing(host, port=None, *args, **kwargs):
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr("evolution_api_mcp.remote.consent.socket.getaddrinfo", failing)

    answer = client(store, provider).post("/consent", data=_form())

    assert "Could not resolve the Evolution host" in answer.text
    assert verify.calls == []


def test_allow_private_targets_admits_the_private_address(store, verify, provider, monkeypatch):
    """Given the dev override on and a private target, When submitted, Then the flow proceeds to the redirect."""
    _fake_resolver(monkeypatch, "169.254.169.254")

    answer = client(store, provider, allow_private_targets=True).post(
        "/consent", data=_form(base_url="https://169.254.169.254")
    )

    assert answer.status_code == 302
    assert verify.calls[0]["base_url"] == "https://169.254.169.254"


# ------------------------------------------------------- integration rule
def test_a_baileys_instance_is_refused_with_the_business_only_message_and_nothing_is_stored(
    db_path, store, verify, provider
):
    """Given an instance whose integration is WHATSAPP-BAILEYS on a default (Business-only) deployment, When
    consent is submitted, Then the page gives the exact refusal, no tenant is stored and no code is minted."""
    verify.outcome = consent._Verified("ok", "", None, INSTANCE, BAILEYS)

    answer = client(store, provider).post("/consent", data=_form())

    assert answer.status_code == 200
    assert BUSINESS_ONLY_REFUSAL in answer.text
    assert provider.calls == []
    assert store.find_tenant_by_key_hash(key_hash(EVO_URL, TOKEN)) is None
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT count(*) FROM tenants").fetchone()[0] == 0
    assert len(verify.calls) == 1  # the verification DID reach Evolution; the rule applies to its answer


def test_a_widened_deployment_admits_baileys_and_names_the_allowed_set_when_it_still_refuses(store, verify, provider):
    verify.outcome = consent._Verified("ok", "", None, INSTANCE, BAILEYS)
    admitted = client(store, provider, allowed_integrations=frozenset(ALL)).post("/consent", data=_form())
    assert admitted.status_code == 302
    assert store.get_tenant(provider.calls[0][1]).integration == BAILEYS

    store.put_pending(_pending_row(id="pend-2"))
    refused = client(store, provider, allowed_integrations=frozenset({BUSINESS, "EVOLUTION"})).post(
        "/consent", data=_form(req="pend-2", instance_token="another-token")
    )
    assert "these integrations only: EVOLUTION, WHATSAPP-BUSINESS." in refused.text
    assert "This instance uses WHATSAPP-BAILEYS" in refused.text
    assert len(provider.calls) == 1


# ---------------------------------------------------- verification errors
def test_a_failed_verification_re_renders_the_error_stores_nothing_and_keeps_the_request_alive(store, verify, provider):
    """Given a token Evolution refuses, When consent is submitted, Then the page re-renders with the reason,
    nothing is stored, no code is minted, and the pending request survives so a corrected token completes."""
    verify.refuse("Evolution did not accept this token as an instance token.")
    c = client(store, provider)

    answer = c.post("/consent", data=_form())

    assert answer.status_code == 200
    assert "Evolution did not accept this token as an instance token." in answer.text
    assert store.find_tenant_by_key_hash(key_hash(EVO_URL, TOKEN)) is None
    assert provider.calls == []

    verify.outcome = consent._Verified("ok", "", None, INSTANCE, BUSINESS)
    assert c.post("/consent", data=_form()).status_code == 302


def test_the_error_text_is_rendered_escaped(store, verify, provider):
    """Evolution's error text is untrusted: markup in it never becomes html in a browser page."""
    verify.refuse("<script>alert('xss')</script>")

    answer = client(store, provider).post("/consent", data=_form())

    assert "&lt;script&gt;" in answer.text
    assert "alert('xss')" not in answer.text
    # The page carries one script of its own, the submit-state helper the policy admits by hash, so counting tags
    # separates "ours" from "the error text became markup".
    assert answer.text.count("<script") == 1
    assert "dataset.sending" in answer.text


def test_a_failed_attempt_never_echoes_the_token(store, verify, provider):
    """The URL is prefilled on a re-render; the password field is not, and the token appears nowhere."""
    verify.refuse("Evolution did not accept this token as an instance token.")

    answer = client(store, provider).post("/consent", data=_form())

    assert TOKEN not in answer.text
    assert EVO_URL in answer.text
    assert 'type="password"' in answer.text


def test_a_re_render_keeps_the_submitted_policy_and_toolsets(store, verify, provider):
    verify.refuse("Nope.")

    answer = client(store, provider).post("/consent", data=_form(policy="standard", toolsets=["groups", "events"]))

    assert 'value="standard" checked' in answer.text
    assert _checked_toolsets(answer.text) == {"groups", "events"}


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (discovery.DiscoveryRefused(discovery.GLOBAL_KEY_MESSAGE), discovery.GLOBAL_KEY_MESSAGE),
        (
            EvolutionUnreachable("evo.example.com", "connection refused"),
            "Could not reach evo.example.com: connection refused.",
        ),
        (
            EvolutionUnreachable("evo.example.com", "refused non-public address 10.0.0.5 for evo.example.com"),
            "The Evolution URL must be a public host.",
        ),
        (EvolutionHTTPError(502, "Bad Gateway", None), "Evolution answered 502: Bad Gateway."),
        (
            EvolutionUncertain("evo.example.com", "ReadTimeout"),
            "Evolution at evo.example.com did not finish answering: ReadTimeout.",
        ),
        (RuntimeError("boom"), "The Evolution server could not be verified (RuntimeError)."),
    ],
)
def test_readable_failure_maps_each_discovery_outcome_to_one_sentence(exc, expected):
    assert consent._readable_failure(exc) == expected


# ---------------------------------------------- the real verification child
class _FakeEvolutionHandler(BaseHTTPRequestHandler):
    """Evolution's documented answers (`discovery.py` contract) over real HTTP: `GET /`, `POST /verify-creds`,
    `GET /instance/fetchInstances`. The instance row carries the token the caller presented."""

    def _send(self, status: int, body: object) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path == "/":
            body = {"status": 200, "message": "Welcome to the Evolution API, it is working!", "version": "2.3.7"}
            self._send(200, body)
        elif self.path.startswith("/instance/fetchInstances"):
            row = {"name": INSTANCE, "integration": self.server.integration, "token": self.headers["apikey"]}
            self._send(200, [row])
        else:
            self._send(404, {"message": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        if self.path == "/verify-creds":
            if self.server.global_key:
                self._send(200, {"status": 200, "message": "Credentials are valid"})
            else:
                self._send(401, {"status": 401, "error": "Unauthorized", "message": "Unauthorized"})
        else:
            self._send(404, {"message": "not found"})

    def log_message(self, *args):
        pass


@pytest.fixture
def evolution_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeEvolutionHandler)
    server.integration = BUSINESS
    server.global_key = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server.url = f"http://127.0.0.1:{server.server_address[1]}"
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def test_the_real_verification_child_discovers_the_instance_and_leaves_no_process(store, provider, evolution_server):
    """Given a real Evolution-shaped server, When consent runs the unpatched spawn child with the private-target
    override, Then discovery's identity reaches the tenant row and the child is gone afterwards."""
    evolution_server.integration = BUSINESS

    answer = client(store, provider, allow_private_targets=True).post(
        "/consent", data=_form(base_url=evolution_server.url)
    )

    assert answer.status_code == 302
    tenant = store.get_tenant(provider.calls[0][1])
    assert (tenant.instance_name, tenant.integration) == (INSTANCE, BUSINESS)
    assert tenant.base_url == evolution_server.url
    verifier = consent._last_verifier
    assert verifier is not None and not verifier.is_alive() and verifier.exitcode == 0


def test_the_real_verification_child_refuses_the_servers_global_key(store, provider, evolution_server):
    """A key that `POST /verify-creds` accepts is the server-wide AUTHENTICATION_API_KEY: the page says so and no
    tenant is stored."""
    evolution_server.global_key = True

    answer = client(store, provider, allow_private_targets=True).post(
        "/consent", data=_form(base_url=evolution_server.url)
    )

    assert answer.status_code == 200
    assert "global AUTHENTICATION_API_KEY" in answer.text
    assert "Use the instance&#x27;s own token instead." in answer.text
    assert provider.calls == []
    assert store.find_tenant_by_key_hash(key_hash(evolution_server.url, TOKEN)) is None


def test_the_connect_time_guard_refuses_a_private_address_even_when_the_pre_check_passed(
    store, provider, evolution_server, monkeypatch
):
    """The pre-check resolves once; DNS can answer differently at connect time. With the pre-check bypassed, the
    real child still cannot reach a loopback server when private targets are not allowed."""
    monkeypatch.setattr(consent, "_ssrf_refusal", lambda host: None)

    answer = client(store, provider, allow_private_targets=False).post(
        "/consent", data=_form(base_url=evolution_server.url)
    )

    assert answer.status_code == 200
    assert "The Evolution URL must be a public host." in answer.text
    assert provider.calls == []


def _hang_entry(base_url: str, token: str, allow_private: bool, send_conn) -> None:
    """Spawn-child target for the termination test: a verifier that never answers, the shape of an Evolution host
    that accepts the socket and stalls."""
    time.sleep(30)


def test_a_hanging_verification_is_terminated_and_the_request_answers(store, provider, monkeypatch):
    """Given a verifier child that never answers, When the deadline passes, Then the REQUEST answers the timeout
    page promptly AND no process survives the helper."""
    monkeypatch.setattr(consent, "_VERIFY_TIMEOUT", 0.2)
    monkeypatch.setattr(consent, "_verify_entry", _hang_entry)
    c = client(store, provider, allow_private_targets=True)

    before = time.monotonic()
    answer = c.post("/consent", data=_form())
    elapsed = time.monotonic() - before

    assert answer.status_code == 200
    assert "Evolution did not answer within 0.2 seconds." in answer.text
    assert elapsed < 8  # the child hangs for 30 s; answering proves the reap
    verifier = consent._last_verifier
    assert verifier is not None and not verifier.is_alive() and verifier.exitcode is not None
    assert provider.calls == []


# --------------------------------------------------------------- body & req
def test_an_oversized_body_answers_413(store, verify, provider):
    """Given a 65 KiB body, When posted, Then 413: custom routes sit outside the SDK's 4 MiB limit, so consent caps
    itself before parsing."""
    big = b"base_url=x&filler=" + b"q" * (65 * 1024)

    answer = client(store, provider).post(
        "/consent", content=big, headers={"content-type": "application/x-www-form-urlencoded"}
    )

    assert answer.status_code == 413
    assert verify.calls == [] and provider.calls == []


def test_an_expired_or_unknown_req_answers_400(store, verify, provider):
    """Given a pending row already expired (or an id never issued), When the page is opened or the form posted,
    Then 400, and Evolution is never dialed."""
    store.put_pending(_pending_row(expires_at=_now() - timedelta(minutes=1)))
    c = client(store, provider)

    assert c.get("/consent?req=pend-1").status_code == 400
    assert c.post("/consent", data=_form()).status_code == 400
    assert c.get("/consent?req=never-issued").status_code == 400
    assert verify.calls == [] and provider.calls == []
