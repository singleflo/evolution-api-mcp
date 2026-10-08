"""The consent page where a user connects their own Evolution API instance.

Two plain Starlette handlers, public by SDK design (custom routes are never authenticated). Everything the
handlers need travels in one `ConsentDeps` object read from `app.state.consent_deps`; no module singletons, so a
test or a second deployment builds its own. The app wires them on the SAME Starlette app `streamable_http_app()`
returns:

    http_app.state.consent_deps = ConsentDeps(store=..., provider=..., public_url=..., allow_private_targets=...,
                                              allowed_integrations=..., publisher=...)
    mcp.custom_route("/consent", methods=["GET"])(consent.consent_form)
    mcp.custom_route("/consent", methods=["POST"])(consent.consent_submit)

Security posture, in the order the POST enforces it: a 64 KiB body cap (custom routes sit outside the SDK's 4 MiB
`RequestBodyLimitMiddleware`), the pending-`req` check, the user's refusal, the required fields, the https rule,
the SSRF pre-check (the host is resolved and refused unless every address is public, BEFORE any connection, so a
public unauthenticated endpoint can never be turned into an internal prober), then a real verification against
the submitted Evolution server: `discovery.discover` over the connect-time SSRF guard, run in its own spawn
SUBPROCESS and reaped at the deadline (a host that accepts the socket and stalls could otherwise keep a worker
thread blocked; a process can be terminated and killed instead, so the HTTP request always answers within the
timeout). Then the integration rule, then the tenant row, reused by `key_hash` so one Evolution connection stays
one subject. The token is never logged and never echoed; every interpolated value, Evolution's own error text
included, goes through `html.escape` because it is untrusted external text landing in a browser page.
"""

import asyncio
import html
import logging
import multiprocessing
import secrets
import socket
from dataclasses import dataclass, replace
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from typing import Literal, Protocol
from urllib.parse import parse_qs, urlsplit

import httpcore2
from anyio.to_thread import run_sync as run_in_thread
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from evolution_api_mcp import discovery, netguard
from evolution_api_mcp.client import EvolutionClient, EvolutionHTTPError, EvolutionUncertain, EvolutionUnreachable
from evolution_api_mcp.registry import BUSINESS
from evolution_api_mcp.remote import ui
from evolution_api_mcp.remote.store import PendingAuthz, Store, key_hash
from evolution_api_mcp.tenant import Tenant
from evolution_api_mcp.toolsets import DEFAULT_TOOLSETS, TOOLSET_ORDER, TOOLSETS

logger = logging.getLogger(__name__)

_MAX_BODY = 64 * 1024
_VERIFY_TIMEOUT = 20
_TERMINATE_GRACE = 2.0
_GONE_MESSAGE = "This connection request is unknown or has expired. Start again from your AI assistant."
_PUBLIC_HOST_MESSAGE = "The Evolution URL must be a public host."
_POLICIES = ("standard", "read")


class ConsentProvider(Protocol):
    """The two auth-provider methods consent needs (`EvolutionAuthProvider` implements them)."""

    def complete_consent(self, pending_id: str, subject: str) -> str: ...

    def refuse_consent(self, pending_id: str) -> str: ...


@dataclass(frozen=True, slots=True)
class ConsentDeps:
    """The handlers' whole world, attached to `app.state.consent_deps`."""

    store: Store
    provider: ConsentProvider
    public_url: str
    allow_private_targets: bool
    allowed_integrations: frozenset[str] = frozenset({BUSINESS})
    publisher: str = "Persevida SL"


@dataclass(frozen=True, slots=True)
class _FormState:
    """What the form shows on a (re)render: the pending id, who is asking, the previous submission minus the
    token, and the error explaining the re-render."""

    req: str
    client_name: str = ""
    redirect_uri: str = ""
    scopes: str = ""
    base_url: str = ""
    policy: str = "read"
    toolsets: frozenset[str] = DEFAULT_TOOLSETS
    error: str | None = None


# Each verification spawns a child process and dials a host the caller chose, so the number running at once is
# capped: an unauthenticated form must not be a cheap way to start processes or probe other servers.
MAX_CONCURRENT_VERIFICATIONS = 4
_verifications_running = 0


async def consent_form(request: Request) -> Response:
    """GET /consent?req=<id>: the page a user fills in with their own Evolution instance."""
    deps: ConsentDeps = request.app.state.consent_deps
    pending = deps.store.load_pending(request.query_params.get("req", ""))
    if pending is None:
        return _plain_page(deps, _GONE_MESSAGE, status_code=400)
    return HTMLResponse(_form_html(deps, _asking(deps, pending)))


def _asking(deps: ConsentDeps, pending: PendingAuthz) -> _FormState:
    """A blank form that says who is asking, for what, and where it leads.

    The specification's consent-UI rules require the page to name the requesting client, state the scope and show
    the redirect it registered: a consent screen that omits them asks a user to approve a stranger. The name
    arrives from the client's own dynamic registration, so it is untrusted text like any other: escaped on the way
    out, and shown next to the redirect URI, which is the part an attacker cannot fake past `redirect_uri`
    validation.
    """
    client = deps.store.get_client(pending.client_id)
    name = getattr(client, "client_name", None) or pending.client_id
    return _FormState(
        req=pending.id, client_name=name, redirect_uri=pending.redirect_uri, scopes=pending.scopes or "evolution"
    )


async def consent_submit(request: Request) -> Response:
    """POST /consent: verify the instance, store the tenant, and send the browser on to the client's
    redirect_uri with a fresh code."""
    deps: ConsentDeps = request.app.state.consent_deps
    fields = await _bounded_fields(request)
    if fields is None:
        return Response("Request body too large.", status_code=413)

    pending = deps.store.load_pending(_first(fields, "req"))
    if pending is None:
        return _plain_page(deps, _GONE_MESSAGE, status_code=400)

    if _first(fields, "action") == "deny":
        # Refusing is part of the flow, not the absence of one. The client is told `access_denied` and the pending
        # row is consumed, so the browser goes back to the assistant that asked instead of being left on a page
        # whose only exit is the window's close button.
        logger.info("consent: refused for client %s", pending.client_id)
        return RedirectResponse(deps.provider.refuse_consent(pending.id), status_code=302)

    base_url = _first(fields, "base_url").strip().rstrip("/")
    token = _first(fields, "instance_token").strip()
    policy = _first(fields, "policy")
    toolsets = frozenset(name for name in fields.get("toolsets", []) if name in TOOLSETS)
    shown = replace(_asking(deps, pending), base_url=base_url, toolsets=toolsets)
    if not base_url or not token:
        return _rerender(deps, shown, "Fill in the Evolution URL and the instance token.")
    if policy not in _POLICIES:
        return _rerender(deps, shown, "Choose what the assistant may do.")
    shown = replace(shown, policy=policy)
    if not toolsets:
        return _rerender(deps, shown, "Choose at least one toolset.")

    host = _url_host(base_url)
    if host is None:
        return _rerender(
            deps,
            shown,
            "The Evolution URL must be an https:// address (http://localhost works for a server on this machine).",
        )

    if not deps.allow_private_targets:
        refusal = await run_in_thread(_ssrf_refusal, host, abandon_on_cancel=True)
        if refusal is not None:
            return _rerender(deps, shown, refusal)

    global _verifications_running
    if _verifications_running >= MAX_CONCURRENT_VERIFICATIONS:
        return _rerender(deps, shown, "The server is checking other connections right now. Try again in a minute.")
    _verifications_running += 1
    try:
        # Bounded by construction: the thread joins the child for at most _VERIFY_TIMEOUT (+ the reap), so a
        # cancelled request waits out at most that, and the terminate/kill bookkeeping is never half-done.
        outcome = await run_in_thread(_verify_isolated, base_url, token, deps.allow_private_targets)
    except Exception:
        logger.info("consent: verification failed for host %s", host)
        return _rerender(deps, shown, "The Evolution server could not be verified.")
    finally:
        _verifications_running -= 1
    if outcome.status != "ok":
        logger.info("consent: verification %s for host %s", outcome.status, host)
        return _rerender(deps, shown, outcome.detail)

    if outcome.integration not in deps.allowed_integrations:
        logger.info("consent: integration %s not allowed for host %s", outcome.integration, host)
        return _rerender(deps, shown, _integration_refusal(deps.allowed_integrations, outcome.integration))

    existing = deps.store.find_tenant_by_key_hash(key_hash(base_url, token))
    if existing is not None:
        subject = existing.subject
        deps.store.put_tenant(
            replace(
                existing,
                instance_name=outcome.instance_name,
                integration=outcome.integration,
                policy=policy,
                toolsets=toolsets,
            )
        )
    else:
        subject = "t_" + secrets.token_urlsafe(16)
        deps.store.put_tenant(
            Tenant(
                subject=subject,
                base_url=base_url,
                token=token,
                instance_name=outcome.instance_name,
                integration=outcome.integration,
                policy=policy,
                toolsets=toolsets,
            )
        )
    logger.info("consent: subject %s connected to host %s", subject, host)
    return RedirectResponse(deps.provider.complete_consent(pending.id, subject), status_code=302)


def _rerender(deps: ConsentDeps, shown: _FormState, error: str) -> HTMLResponse:
    return HTMLResponse(_form_html(deps, replace(shown, error=error)))


def _integration_refusal(allowed: frozenset[str], integration: str) -> str:
    """Why this instance's integration is not admitted on this deployment."""
    if allowed == frozenset({BUSINESS}):
        return (
            "This hosted server connects WhatsApp Business Platform instances only (integration WHATSAPP-BUSINESS). "
            f"This instance uses {integration}; run the local server (uvx evolution-api-mcp) for it."
        )
    return (
        f"This hosted server connects instances of these integrations only: {', '.join(sorted(allowed))}. "
        f"This instance uses {integration}; run the local server (uvx evolution-api-mcp) for it."
    )


def _url_host(base_url: str) -> str | None:
    """The host of an acceptable Evolution URL, or None: https, or http on this machine (localhost / 127.0.0.1)."""
    try:
        parts = urlsplit(base_url)
        host = parts.hostname or ""
        parts.port  # noqa: B018  (raises ValueError for a malformed port)
    except ValueError:
        return None
    if parts.username or parts.password:
        return None
    if parts.scheme == "https" or (parts.scheme == "http" and host in ("localhost", "127.0.0.1")):
        return host or None
    return None


def _first(fields: dict[str, list[str]], name: str) -> str:
    """The first submitted value of a field, "" when absent (every field except `toolsets` is single-valued)."""
    values = fields.get(name)
    return values[0] if values else ""


async def _bounded_fields(request: Request) -> dict[str, list[str]] | None:
    """The form fields of a body capped at 64 KiB, or None when oversize.

    Refusing at the first chunk past the cap keeps an oversized POST from being read into memory at all. Every
    field keeps its submitted values in order: `toolsets` is a repeated checkbox and uses all of them, every other
    field uses only the first (`_first`).
    """
    size = 0
    chunks: list[bytes] = []
    async for chunk in request.stream():
        size += len(chunk)
        if size > _MAX_BODY:
            return None
        chunks.append(chunk)
    return parse_qs(b"".join(chunks).decode("utf-8", "replace"))


def _ssrf_refusal(host: str) -> str | None:
    """Why this host may not be dialed from a public endpoint, or None.

    Runs before any connection: consent is unauthenticated, so without this guard anyone could make the server
    probe internal ranges and the cloud metadata address on their own schedule. The connect-time guard in
    `netguard` repeats the check where the socket opens, which is what defeats DNS rebinding.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        return f"Could not resolve the Evolution host: {exc}"
    try:
        netguard.vet_addresses(host, [str(info[4][0]).split("%", 1)[0] for info in infos], allow_private=False)
    except httpcore2.ConnectError:
        return _PUBLIC_HOST_MESSAGE
    return None


def _readable_failure(exc: Exception) -> str:
    """One sentence a person can act on, from what discovery raised.

    The exception text is Evolution's or the transport's own wording and reaches the page escaped. Only the
    connect-time SSRF refusal is rewritten, because its raw text names an address the user did not type.
    """
    if isinstance(exc, discovery.DiscoveryRefused):
        return exc.message
    if isinstance(exc, EvolutionUnreachable):
        if exc.reason.startswith("refused "):
            return _PUBLIC_HOST_MESSAGE
        return f"Could not reach {exc.host}: {exc.reason}."
    if isinstance(exc, EvolutionUncertain):
        return f"Evolution at {exc.host} did not finish answering: {exc.reason}."
    if isinstance(exc, EvolutionHTTPError):
        return f"Evolution answered {exc.status}: {exc.message}."
    return f"The Evolution server could not be verified ({type(exc).__name__})."


@dataclass(frozen=True, slots=True)
class _Verified:
    """What one isolated verification concluded. The process travels along so a test (or a supervisor) can
    confirm nothing survives the call."""

    status: Literal["ok", "error", "timeout"]
    detail: str
    process: BaseProcess | None
    # The instance Evolution reported for the token; empty unless status is "ok".
    instance_name: str = ""
    integration: str = ""


async def _discover(base_url: str, token: str, allow_private: bool) -> discovery.InstanceIdentity:
    client = EvolutionClient(
        base_url,
        token,
        transport=netguard.guarded_transport(allow_private=allow_private),
        timeout=float(_VERIFY_TIMEOUT),
    )
    try:
        return await discovery.discover(client)
    finally:
        await client.aclose()


def _verify_entry(base_url: str, token: str, allow_private: bool, send_conn: Connection) -> None:
    """Child side: one discovery, then the outcome on the pipe. "ok" carries the instance name and integration;
    "error" carries the sentence to show. Only three plain values cross the boundary."""
    try:
        identity = asyncio.run(_discover(base_url, token, allow_private))
        send_conn.send(("ok", identity.name, identity.integration))
    except Exception as exc:
        send_conn.send(("error", _readable_failure(exc)))
    finally:
        send_conn.close()


_last_verifier: BaseProcess | None = None


def _verify_isolated(base_url: str, token: str, allow_private: bool) -> _Verified:
    """One instance check in its own short-lived spawn process.

    A host that accepts the connection and stalls must not block a worker thread forever, and a cancelled thread
    cannot stop a blocked socket. A subprocess instead is joined with a hard deadline and then reaped: the caller
    always gets an answer within `_VERIFY_TIMEOUT`, and no process survives this call. `_last_verifier` holds the
    most recent child's handle.
    """
    global _last_verifier
    ctx = multiprocessing.get_context("spawn")
    recv_conn, send_conn = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_verify_entry, args=(base_url, token, allow_private, send_conn), daemon=True)
    _last_verifier = proc
    proc.start()
    send_conn.close()  # the parent keeps no write end: EOF means the child died
    try:
        proc.join(_VERIFY_TIMEOUT)
        if proc.is_alive():
            _reap(proc)
            return _Verified("timeout", f"Evolution did not answer within {_VERIFY_TIMEOUT:g} seconds.", proc)
        try:
            message = recv_conn.recv()
        except EOFError:  # the child died without answering
            return _Verified("error", "The Evolution server could not be verified.", proc)
        if message[0] == "ok":
            return _Verified("ok", "", proc, message[1], message[2])
        return _Verified("error", message[1], proc)
    finally:
        recv_conn.close()


def _reap(proc: BaseProcess) -> None:
    """Terminate, then kill: a hung socket read must never outlive the request that started it."""
    proc.terminate()
    proc.join(_TERMINATE_GRACE)
    if proc.is_alive():
        proc.kill()
        proc.join(_TERMINATE_GRACE)


def _plain_page(deps: ConsentDeps, message: str, status_code: int) -> HTMLResponse:
    return HTMLResponse(
        ui.layout("This request has expired", f"<p>{html.escape(message)}</p>", publisher=deps.publisher),
        status_code=status_code,
    )


# The keyboard hygiene every credential field needs on a phone: iOS capitalises the first letter of a URL and
# autocorrects a token unless told not to, and the consent page is reached from the in-app browser of Claude and
# ChatGPT far more often than from a desktop.
_NO_TYPING_HELP = 'autocapitalize="off" autocorrect="off" spellcheck="false"'

_POLICY_OPTIONS = (
    ("standard", "Read and act: send messages, manage chats and configuration"),
    ("read", "Read only: nothing is sent or changed"),
)


def _scope_sentence(allowed: frozenset[str]) -> str:
    if allowed == frozenset({BUSINESS}):
        return "This hosted server connects WhatsApp Business Platform instances only (integration WHATSAPP-BUSINESS)."
    return f"This hosted server connects instances of these integrations only: {', '.join(sorted(allowed))}."


def _toolset_options(selected: frozenset[str]) -> str:
    rows = []
    for name in TOOLSET_ORDER:
        checked = " checked" if name in selected else ""
        rows.append(
            '<label class="policy-option">'
            f'<input type="checkbox" name="toolsets" value="{html.escape(name)}"{checked}>'
            f"<span><strong>{html.escape(name)}</strong>"
            f'<span class="policy-detail">{html.escape(TOOLSETS[name])}</span></span></label>'
        )
    return "".join(rows)


_KNOWN_HOSTS = frozenset({"claude.ai", "claude.com", "chatgpt.com", "localhost", "127.0.0.1", "::1"})
_KNOWN_SCHEMES = frozenset({"cursor", "vscode", "vscode-insiders", "windsurf", "zed", "claude", "codex"})
_UNKNOWN_CLIENT_WARNING = (
    "This application is not one of the assistants this server knows."
    " Only continue if you started this connection yourself."
)
_NAME_DISPLAY_CHARS = 80


def _unknown_client(redirect_uri: str) -> bool:
    """True when the redirect leads somewhere other than the assistants this server knows: https or http to a host
    outside the known list, or a custom application scheme outside the known list. Anyone can register a client, so
    the page says so rather than let a stranger's name pass for a familiar one."""
    try:
        parts = urlsplit(redirect_uri)
        host = parts.hostname
    except ValueError:
        return True
    scheme = parts.scheme.lower()
    if scheme in ("https", "http"):
        return host not in _KNOWN_HOSTS
    return scheme not in _KNOWN_SCHEMES


def _display_name(name: str) -> str:
    """The client's own name, cut to 80 characters with an ellipsis; the caller still escapes it."""
    return name if len(name) <= _NAME_DISPLAY_CHARS else name[:_NAME_DISPLAY_CHARS] + "…"


def _form_html(deps: ConsentDeps, shown: _FormState) -> str:
    error = f'<p class="error" role="alert">{html.escape(shown.error)}</p>' if shown.error else ""
    warning = (
        f'<p class="error" role="note">{html.escape(_UNKNOWN_CLIENT_WARNING)}</p>'
        if _unknown_client(shown.redirect_uri)
        else ""
    )
    client_name = html.escape(_display_name(shown.client_name))
    policy_options = "".join(
        '<label class="policy-option">'
        f'<input type="radio" name="policy" value="{value}"{" checked" if shown.policy == value else ""}>'
        f"<span><strong>{html.escape(label)}</strong></span></label>"
        for value, label in _POLICY_OPTIONS
    )
    privacy = f"{html.escape(deps.public_url)}/privacy"
    terms = f"{html.escape(deps.public_url)}/terms"
    publisher = html.escape(deps.publisher)
    return ui.layout(
        "Connect your Evolution API instance",
        (
            '<p class="lead">'
            f"<strong>{client_name}</strong> is asking to reach one Evolution API instance on"
            " your behalf. Give the address of your Evolution server and that instance's token, and choose what"
            " it may do.</p>"
            '<dl class="facts">'
            f"<div><dt>Requested by</dt><dd>{client_name}</dd></div>"
            f"<div><dt>Access returns to</dt><dd><code>{html.escape(shown.redirect_uri)}</code></dd></div>"
            f"<div><dt>Scope</dt><dd><code>{html.escape(shown.scopes)}</code></dd></div>"
            "</dl>"
            f'<p class="policy-note">{html.escape(_scope_sentence(deps.allowed_integrations))}</p>'
            f"{warning}{error}"
            '<form method="post" action="/consent" class="consent-form">'
            f'<input type="hidden" name="req" value="{html.escape(shown.req)}">'
            '<p class="field"><label for="base_url">Evolution server address</label>'
            '<input id="base_url" name="base_url" type="url" required inputmode="url" autocomplete="url"'
            f' {_NO_TYPING_HELP} placeholder="https://evolution.example.com"'
            f' value="{html.escape(shown.base_url)}">'
            '<span class="field-help">The address your Evolution API answers at. It must be reachable over'
            " https from the public internet.</span></p>"
            '<p class="field"><label for="instance_token">Instance token</label>'
            '<input id="instance_token" name="instance_token" type="password" required autocomplete="off"'
            f" {_NO_TYPING_HELP}>"
            '<span class="field-help">The token of this one instance: in Evolution Manager open the instance and'
            " copy its token, or use the token returned when the instance was created. The server's global API key"
            " is refused.</span></p>"
            '<fieldset class="policy"><legend>What the assistant may do</legend>'
            f"{policy_options}"
            '<p class="policy-note">Either choice can be changed later by connecting again. Irreversible actions'
            " are never available here.</p></fieldset>"
            '<fieldset class="policy"><legend>Which toolsets to enable</legend>'
            f"{_toolset_options(shown.toolsets)}"
            '<p class="policy-note">Only the tools of the ticked toolsets are offered to the assistant. Choose at'
            " least one.</p></fieldset>"
            '<section class="assurance"><h2>About the token you are about to paste</h2><ul>'
            f"<li>This is an independent project of {publisher}, not affiliated with Meta, WhatsApp or the"
            " Evolution API project.</li>"
            "<li>The token is stored encrypted and used only to call the Evolution server you typed above, on your"
            " behalf. It is never shared and never used for anything else. Disconnecting deletes it.</li>"
            "<li>Irreversible actions (logging out, deleting a message for everyone, leaving a group, deleting"
            " templates, chatbots or credentials) and tools that take secrets are never available on this"
            " server.</li>"
            "<li>Sends are real and reach real people. The WhatsApp Business Messaging Policy applies: opt-in,"
            " the 24-hour window, approved templates.</li>"
            "<li>Your messages and contacts are never stored here. What is kept is listed in the"
            f' <a href="{privacy}">privacy notice</a>; the rules of use are in the <a href="{terms}">terms</a>.</li>'
            "</ul></section>"
            '<div class="actions">'
            '<button type="submit" class="primary" data-busy-label="Connecting…">Connect</button>'
            '<button type="submit" class="secondary" name="action" value="deny" formnovalidate'
            ' data-busy-label="Refusing…">Refuse</button></div>'
            # Hidden until the form reports itself as sending. What follows the press is a live connection to the
            # user's Evolution server, which can take the better part of half a minute on a cold host;
            # role="status" so a screen reader hears it appear rather than only seeing it.
            '<p class="sending-note" role="status">Checking your Evolution server and the token you gave — this'
            " can take up to twenty seconds.</p>"
            "</form>"
        ),
        publisher=deps.publisher,
    )
