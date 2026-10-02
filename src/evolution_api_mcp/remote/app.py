#!/usr/bin/env python3
"""evolution-api-mcp-remote — the hosted, multi-tenant server.

One process serves many tenants, each with their own Evolution API connection (server URL, instance token, policy
and toolsets) stored via the consent page. The architecture is fixed by three measured facts:

* **The only tenant binding is a JSON-RPC-tier `ServerMiddleware`.** `streamable_http_app()` accepts no user
  Starlette middleware and builds `AuthenticationMiddleware` + `AuthContextMiddleware` internally, so any Starlette
  middleware would run BEFORE the auth contextvar is set and `auth_context.get_access_token()` would always be None
  there. `BindTenant` therefore runs inside the JSON-RPC dispatch, where the contextvar is set.
* **`stateless_http=True` is mandatory.** In a stateful session the initializing request's tenant would be pinned to
  every later message on that session: two tenants behind one host would swap instances. Stateless mode re-binds per
  JSON-RPC message.
* **The 401 for a token whose tenant is gone comes from `load_access_token` returning None** (remote/auth.py checks
  the tenant row), which makes the SDK's `RequireAuthMiddleware` emit 401 + a `WWW-Authenticate` header with
  `resource_metadata=`. `BindTenant`'s own missing-tenant error is only the defensive second line (a ServerMiddleware
  can only answer inside HTTP 200).

Wiring constraint: `consent.py` handlers read `request.app.state.consent_deps`, and `request.app` is the app
`streamable_http_app()` RETURNS: the state and the custom routes both live there. Custom routes are consumed when
`streamable_http_app()` runs, so they are registered BEFORE it is called.

Lifespan: the SDK's returned app already installs a lifespan that starts the streamable-HTTP session manager
(`router.lifespan_context`), and Starlette never runs a mounted app's lifespan, so the startup purges are wrapped
around the installed context rather than replacing or remounting it.

A multi-tenant process must never carry one tenant's Evolution connection or the local server's policy variables:
`build_app` refuses to start when any of `_SINGLE_TENANT_ENV_VARS` is set.
"""

import html
import logging
import os
import re
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from importlib import resources as importlib_resources
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import anyio
from mcp.server.auth.middleware import auth_context
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_REQUEST
from pydantic import AnyHttpUrl
from starlette.applications import Starlette
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from evolution_api_mcp import __version__, context, paths, policy, registry, tenant, tools
from evolution_api_mcp.remote import consent, files, ui
from evolution_api_mcp.remote.auth import EvolutionAuthProvider
from evolution_api_mcp.remote.store import Store
from evolution_api_mcp.server import REPO_URL, build_server
from evolution_api_mcp.toolsets import TOOLSET_ORDER, TOOLSETS

DEFAULT_PUBLISHER = "Persevida SL"
DEFAULT_ALLOWED_INTEGRATIONS = registry.BUSINESS

# Refused before anything else starts: one of these in the environment turns the multi-tenant server into a quiet
# proxy onto one Evolution instance, or hands the hosted gate policy that only the local server may set.
_SINGLE_TENANT_ENV_VARS = (
    "EVOLUTION_API_URL",
    "EVOLUTION_INSTANCE_TOKEN",
    "EVOLUTION_MCP_TOOLSETS",
    "EVOLUTION_MCP_ALLOW",
    "EVOLUTION_MCP_DENY",
    "EVOLUTION_MCP_ALLOW_IRREVERSIBLE",
)

_HEADING = re.compile(r"(#{1,4})\s+(.*)")
_BULLET = re.compile(r"[-*]\s+(.*)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_CODE = re.compile(r"`([^`]+)`")

logger = logging.getLogger(__name__)


def _refuse_single_tenant_env() -> None:
    for name in _SINGLE_TENANT_ENV_VARS:
        if os.environ.get(name):
            raise RuntimeError(
                f"{name} is set in the environment; evolution-api-mcp-remote is multi-tenant and must not carry one"
                " connection or the local server's policy. Unset it (connections are stored per tenant via the"
                " consent page)."
            )


def _parse_allowed_integrations(raw: str | None) -> frozenset[str]:
    """`*` or a comma list of integrations; blank means the default (WHATSAPP-BUSINESS only)."""
    if not raw or not raw.strip():
        return frozenset({DEFAULT_ALLOWED_INTEGRATIONS})
    if raw.strip() == "*":
        return registry.ALL
    names = [part.strip() for part in raw.split(",") if part.strip()]
    unknown = sorted(set(names) - registry.ALL)
    if not names or unknown:
        raise RuntimeError(
            f"EVOLUTION_REMOTE_ALLOWED_INTEGRATIONS must be '*' or a comma-separated list of"
            f" {', '.join(sorted(registry.ALL))}; got {raw!r}."
        )
    return frozenset(names)


@dataclass(frozen=True, slots=True)
class RemoteSettings:
    """The hosted server's configuration, parsed once from the environment."""

    public_url: str
    secret_key: str
    host: str
    port: int
    openai_challenge: str | None
    publisher: str
    support_email: str
    data_dir: Path
    allow_private_targets: bool
    allowed_integrations: frozenset[str]

    @classmethod
    def from_env(cls) -> "RemoteSettings":
        public_url = (os.environ.get("EVOLUTION_REMOTE_PUBLIC_URL") or "").rstrip("/")
        if not public_url:
            raise RuntimeError(
                "EVOLUTION_REMOTE_PUBLIC_URL is required (e.g. https://evolution-mcp.example.com); it is the address"
                " clients connect to."
            )
        parts = urlsplit(public_url)
        host = parts.hostname or ""
        if parts.scheme != "https" and not (parts.scheme == "http" and host in ("localhost", "127.0.0.1")):
            raise RuntimeError(
                "EVOLUTION_REMOTE_PUBLIC_URL must be an https:// URL (or http://localhost for local runs),"
                f" got {public_url!r}."
            )
        secret_key = os.environ.get("EVOLUTION_REMOTE_SECRET_KEY") or ""
        if not secret_key:
            raise RuntimeError(
                "EVOLUTION_REMOTE_SECRET_KEY is required; generate one with:"
                " python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
            )
        # `or`, not a get() default: the compose file passes every optional variable as `${VAR:-}`, so an operator
        # who leaves the field blank hands the container an EMPTY value rather than no value, and a get() default
        # would never fire. A blank publisher would then reach the legal pages, which both directories read, as a
        # hole where the operator's name belongs, and a blank challenge would answer with an empty 200 instead of 404.
        return cls(
            public_url=public_url,
            secret_key=secret_key,
            host=os.environ.get("EVOLUTION_REMOTE_HOST") or "0.0.0.0",
            # PORT overrides; otherwise listen on the public URL's own port, so
            # EVOLUTION_REMOTE_PUBLIC_URL=https://host:8443 is reachable as is.
            port=int(os.environ.get("PORT") or (parts.port or 8000)),
            openai_challenge=os.environ.get("EVOLUTION_REMOTE_OPENAI_CHALLENGE") or None,
            publisher=os.environ.get("EVOLUTION_REMOTE_PUBLISHER") or DEFAULT_PUBLISHER,
            support_email=os.environ.get("EVOLUTION_REMOTE_SUPPORT_EMAIL") or f"{REPO_URL}/issues",
            data_dir=paths.data_dir(),
            allow_private_targets=os.environ.get("EVOLUTION_REMOTE_ALLOW_PRIVATE_TARGETS") == "1",
            allowed_integrations=_parse_allowed_integrations(os.environ.get("EVOLUTION_REMOTE_ALLOWED_INTEGRATIONS")),
        )


class BindTenant:
    """Binds the request's tenant around every JSON-RPC message.

    The store lookup is the same sub-millisecond SQLite read the auth provider already does; the bind/reset bracket
    is the only thing standing between two tenants sharing this process.
    """

    def __init__(self, store: Store) -> None:
        self._store = store

    async def __call__(self, ctx: ServerRequestContext[Any, Any], call_next: CallNext) -> HandlerResult:
        token = auth_context.get_access_token()
        bound = self._store.get_tenant(token.subject) if token is not None and token.subject else None
        if bound is None:
            # Defensive only: the real 401 comes from load_access_token returning None. Tell the client to start a
            # fresh authorization.
            raise MCPError(
                code=INVALID_REQUEST,
                message=(
                    "Your connection is no longer authorized on this server. Reconnect to start a new authorization."
                ),
            )
        bound_token = tenant.bind(bound)
        try:
            return await call_next(ctx)
        finally:
            tenant.reset(bound_token)


def _inline_md(text: str) -> str:
    escaped = html.escape(text, quote=False)

    # The placeholder round-trip stops link and bold patterns inside a code span from being rewritten, while keeping
    # the escaping guarantee intact (the content is already escaped before the placeholder is made).
    codes: list[str] = []

    def _stash_code(match: re.Match) -> str:
        codes.append(match.group(1))
        return f"\x00{len(codes) - 1}\x00"

    stashed = _CODE.sub(_stash_code, escaped)

    # Operator-supplied substitutions enter this function through _serve_page. The CSP is a second line of defense,
    # not the first.
    def _replace_link(match: re.Match) -> str:
        label = match.group(1)
        # The text is already escaped above, so escaping the target again with html.escape(target, quote=True) would
        # double-escape every ampersand in a query string. Only the double quote needs escaping, to prevent
        # attribute injection.
        target = match.group(2).replace('"', "&quot;")
        return f'<a href="{target}">{label}</a>'

    linked = _LINK.sub(_replace_link, stashed)
    bolded = _BOLD.sub(r"<strong>\1</strong>", linked)

    def _restore_code(match: re.Match) -> str:
        return f"<code>{codes[int(match.group(1))]}</code>"

    return re.sub(r"\x00(\d+)\x00", _restore_code, bolded)


def _cells(row: str) -> list[str]:
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def _markdown_to_html(md: str) -> str:
    """Headings, bullet lists, tables, paragraphs, links, bold, inline code. No dependency.

    Tables earn their branch: the privacy page states what is stored and for how long as a table, and without this
    every row would reach the browser as a paragraph of pipes.
    """
    out: list[str] = []
    state: str | None = None  # None, "list" or "table"

    def close() -> None:
        nonlocal state
        if state == "list":
            out.append("</ul>")
        elif state == "table":
            out.append("</tbody></table></div>")
        state = None

    for line in md.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        heading = _HEADING.match(stripped)
        bullet = _BULLET.match(stripped)
        row = _cells(stripped) if stripped.startswith("|") and stripped.endswith("|") else None
        if heading:
            close()
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline_md(heading.group(2))}</h{level}>")
        elif row is not None:
            if all(cell and set(cell) <= set("-: ") for cell in row):
                continue  # the |---|---| rule under a header row
            if state != "table":
                close()
                head = "".join(f"<th>{_inline_md(c)}</th>" for c in row)
                out.append(f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>')
                state = "table"
            else:
                body = "".join(f"<td>{_inline_md(c)}</td>" for c in row)
                out.append(f"<tr>{body}</tr>")
        elif bullet:
            if state != "list":
                close()
                out.append("<ul>")
                state = "list"
            out.append(f"<li>{_inline_md(bullet.group(1))}</li>")
        else:
            close()
            out.append(f"<p>{_inline_md(stripped)}</p>")
    close()
    return "\n".join(out)


def _split_title(source: str) -> tuple[str, str]:
    """The page's own `# ` heading becomes the layout's title, once.

    Left in the body it would render a second `<h1>` under the one the shell already draws.
    """
    lines = source.splitlines()
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        heading = _HEADING.match(line.strip())
        if heading and len(heading.group(1)) == 1:
            return heading.group(2).strip(), "\n".join(lines[index + 1 :])
        break
    return "", source


def _pages_dir() -> Any:
    return importlib_resources.files("evolution_api_mcp.remote") / "pages"


def _serve_page(name: str, settings: RemoteSettings) -> Response:
    source = (_pages_dir() / f"{name}.md").read_text(encoding="utf-8")

    # The support destination is operator-supplied and may be either an address or a URL, which is why the shape
    # decides.
    support_dest = settings.support_email
    if support_dest.startswith("https://"):
        support_dest = f"[{support_dest}]({support_dest})"
    elif "@" in support_dest and "://" not in support_dest:
        support_dest = f"[{support_dest}](mailto:{support_dest})"

    source = source.replace("{{PUBLISHER}}", settings.publisher).replace("{{SUPPORT_EMAIL}}", support_dest)
    title, body = _split_title(source)
    return HTMLResponse(
        ui.layout(
            title or name.capitalize(),
            f'<div class="doc">{_markdown_to_html(body)}</div>',
            publisher=settings.publisher,
            active=f"/{name}",
        )
    )


def _serve_style() -> Response:
    """The one stylesheet, from the packaged pages directory.

    Immutable for a year because `ui.layout` hangs the version on the query: a deploy changes the URL, so nothing
    has to be revalidated and nobody reads a new page through an old stylesheet.
    """
    css = (_pages_dir() / "style.css").read_text(encoding="utf-8")
    return Response(css, media_type="text/css", headers={"Cache-Control": "public, max-age=31536000, immutable"})


def _hosted_surface(allowed_integrations: frozenset[str]) -> dict[str, list[str]]:
    """The tools a hosted connection can ever use, per toolset, for the integrations this deployment accepts.

    Asked of the policy itself, once per accepted integration with the most permissive hosted choices (standard
    policy, every toolset), so the landing page can never promise a tool the gate would refuse.
    """
    names: dict[str, set[str]] = {}
    for integration in sorted(allowed_integrations):
        conn = context.Connection(
            mode="hosted",
            policy="standard",
            toolsets=frozenset(TOOLSET_ORDER),
            allow=None,
            deny=policy.DEFAULT_DENY,
            deny_is_default=True,
            irreversible_granted=False,
            identity=context.InstanceIdentity("instance", integration),
            subject=None,
            default_delay_ms=context.HOSTED_DEFAULT_DELAY_MS,
            max_writes_per_minute=context.HOSTED_MAX_WRITES_PER_MINUTE,
            max_reads_per_minute=context.HOSTED_MAX_READS_PER_MINUTE,
        )
        for spec in registry.specs():
            if policy.visible(spec, conn):
                names.setdefault(spec.toolset, set()).add(spec.name)
    return {toolset: sorted(names[toolset]) for toolset in TOOLSET_ORDER if toolset in names}


def _landing_page(settings: RemoteSettings) -> Response:
    endpoint = html.escape(f"{settings.public_url}/mcp")
    surface = _hosted_surface(settings.allowed_integrations)
    tool_count = sum(len(names) for names in surface.values())
    toolset_items = "".join(
        f"<li><strong>{html.escape(toolset)}</strong>: {html.escape(TOOLSETS[toolset])}"
        f" ({len(names)} {'tool' if len(names) == 1 else 'tools'})</li>"
        for toolset, names in surface.items()
    )
    if settings.allowed_integrations == frozenset({registry.BUSINESS}):
        scope = (
            "<p>This hosted server connects WhatsApp Business Platform instances only (Evolution integration"
            " <code>WHATSAPP-BUSINESS</code>), through your own Evolution API server. For an instance that pairs"
            " through WhatsApp Web, or an Evolution channel, run the local server:"
            " <code>uvx evolution-api-mcp</code>.</p>"
        )
    else:
        accepted = ", ".join(f"<code>{html.escape(name)}</code>" for name in sorted(settings.allowed_integrations))
        scope = (
            f"<p>This deployment connects Evolution instances with the integration {accepted}, through your own"
            " Evolution API server. The local server (<code>uvx evolution-api-mcp</code>) supports every"
            " integration.</p>"
        )
    return HTMLResponse(
        ui.layout(
            "Your Evolution API instance, in the chat you already use",
            '<p class="endpoint-label">Server address</p>'
            f'<p class="endpoint"><code>{endpoint}</code></p>'
            "<h2>Connect it</h2>"
            "<p>Nothing is installed on your side. You add the address above to your assistant, sign in once with"
            " your Evolution server address and the token of your one instance, and choose what the assistant may"
            " do and which toolsets it gets.</p>"
            '<div class="cards">'
            '<div class="card"><h3>Claude</h3><p>Customize, then Connectors, then Add custom connector. Paste the'
            " address and confirm.</p></div>"
            '<div class="card"><h3>ChatGPT</h3><p>Turn on developer mode in Settings, then create an app for the'
            " address at chatgpt.com/plugins.</p></div>"
            '<div class="card"><h3>Codex and Claude Code</h3><p>One line each:'
            f" <code>codex mcp add evolution-api-mcp --url {endpoint}</code> or"
            f" <code>claude mcp add --transport http evolution-api-mcp {endpoint}</code>.</p></div>"
            "</div>"
            "<h2>What it does</h2>"
            f"<p>Up to {tool_count} tools operate the one instance you connect. You pick the toolsets on the"
            " consent page:</p>"
            f"<ul>{toolset_items}</ul>"
            f"{scope}"
            "<h2>What it never does</h2>"
            "<p>Irreversible actions (logging the session out, deleting a message for everyone, leaving a group,"
            " deleting templates, chatbots or credentials) and every tool that takes a secret as input are not"
            " reachable here under any choice. Your messages, contacts and conversations are never stored on this"
            " server: what it keeps is your Evolution address, your instance token encrypted, and the choices you"
            ' made, all listed on the <a href="/privacy">privacy page</a>. Sends are real messages to real people'
            " and the WhatsApp Business Messaging Policy applies. Rotating the instance token in Evolution ends the"
            " access immediately, without going through us.</p>"
            f"<p>Operated by {html.escape(settings.publisher)}.</p>",
            # Both the Anthropic and OpenAI directories reject anything implying endorsement by a third party, and a
            # reviewer reads the headline, not the footer.
            lead=(
                "An independent, open-source project that connects the assistant you already use to your own"
                " Evolution API server. Not affiliated with, endorsed or sponsored by Meta, WhatsApp or the"
                " Evolution API project."
            ),
            publisher=settings.publisher,
            active="/",
        )
    )


class SecurityHeaders:
    """Framing, referrer and sniffing headers everywhere; a strict Content Security Policy on the HTML this server
    draws itself.

    The MCP specification's consent-UI rules require the authorization page to refuse being framed,
    `frame-ancestors 'none'` with the older `X-Frame-Options` beside it, because a framed consent page is a
    clickjacking target. The policy can be this narrow, down to `default-src 'none'`, precisely because these pages
    carry exactly one inline script, admitted by its own SHA-256 hash, which is why `script-src` names a hash rather
    than an origin, and nothing is fetched from a third party: one stylesheet from this origin and an inline SVG mark.
    """

    _ALWAYS = {
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
    }
    # `form-action` is deliberately ABSENT, and must stay absent. Chrome and Safari check that directive against the
    # whole redirect chain a form submission triggers, not just its action URL, and the consent POST answers 302 to
    # the client's own callback, which is the authorization response OAuth is made of. Measured in Claude Desktop
    # (reference project): with `form-action 'self'` the browser refused the submission and the sign-in never
    # completed. Dynamic client registration means those callbacks cannot be enumerated in advance, so no value of
    # this directive is both correct and workable.
    _HTML_CSP = (
        "default-src 'none'; style-src 'self'; img-src 'self' data:;"
        f" script-src {ui.SCRIPT_HASH}; frame-ancestors 'none'; base-uri 'none'"
    )

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in self._ALWAYS.items():
                    headers.setdefault(name, value)
                if headers.get("content-type", "").startswith("text/html"):
                    headers.setdefault("Content-Security-Policy", self._HTML_CSP)
            await send(message)

        await self._app(scope, receive, send_with_headers)


# How often the retention promises are re-swept, in seconds; cited by docs/REMOTE.md.
SWEEP_SECONDS = 3600


def _sweep_once(store: Store) -> None:
    """The retention sweeps in one place: expired OAuth rows, tenants idle past the window (their disk artifacts go
    with them), expired file links."""
    store.purge_expired()
    for subject in store.purge_idle_tenants():
        files.purge_tenant_artifacts(subject)
    files.purge_expired_files()


async def _sweep_forever(store: Store) -> None:
    """_sweep_once every SWEEP_SECONDS until the lifespan cancels the task.

    One failing sweep must never take the server down: log it and wait for the next tick.
    """
    while True:
        await anyio.sleep(SWEEP_SECONDS)
        try:
            _sweep_once(store)
        except Exception:
            logger.exception("periodic retention sweep failed; retrying in %s s", SWEEP_SECONDS)


def _page_handler(name: str, settings: RemoteSettings) -> Callable[[Request], Awaitable[Response]]:
    async def handler(request: Request) -> Response:
        return _serve_page(name, settings)

    return handler


def build_app(settings: RemoteSettings) -> Starlette:
    """The complete hosted server: MCP at /mcp plus the consent, files and pages routes, with every request bound
    to its tenant."""
    _refuse_single_tenant_env()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    # One data root everywhere: pin the process-wide override once at startup so files and per-tenant scratch
    # directories all resolve settings.data_dir.
    paths.set_data_dir_override(settings.data_dir)
    tools.load_all()
    tenant.configure(allow_private_targets=settings.allow_private_targets, public_url=settings.public_url)
    store = Store(settings.data_dir / "remote.db", settings.secret_key)
    store.init()
    provider = EvolutionAuthProvider(store, settings.public_url)

    mcp = build_server(
        mode="hosted",
        auth_server_provider=provider,
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(settings.public_url),
            service_documentation_url=AnyHttpUrl(REPO_URL),
            resource_server_url=AnyHttpUrl(f"{settings.public_url}/mcp"),
            required_scopes=["evolution"],
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=["evolution"], default_scopes=["evolution"]
            ),
            revocation_options=RevocationOptions(enabled=True),
            validate_token_resource=True,
        ),
        extra_middleware=[BindTenant(store)],
    )

    mcp.custom_route("/consent", methods=["GET"])(consent.consent_form)
    mcp.custom_route("/consent", methods=["POST"])(consent.consent_submit)
    mcp.custom_route("/files/{token}", methods=["GET"])(files.serve_file)

    async def health(request: Request) -> Response:
        return JSONResponse({"status": "ok", "version": __version__})

    mcp.custom_route("/health", methods=["GET"])(health)

    async def challenge(request: Request) -> Response:
        if settings.openai_challenge is None:
            return PlainTextResponse("Not Found", status_code=404)
        return PlainTextResponse(settings.openai_challenge)

    mcp.custom_route("/.well-known/openai-apps-challenge", methods=["GET"])(challenge)

    async def landing(request: Request) -> Response:
        return _landing_page(settings)

    async def style(request: Request) -> Response:
        return _serve_style()

    mcp.custom_route("/style.css", methods=["GET"])(style)
    mcp.custom_route("/", methods=["GET"])(landing)
    for name in ("privacy", "terms", "support"):
        mcp.custom_route(f"/{name}", methods=["GET"])(_page_handler(name, settings))

    host = urlsplit(settings.public_url).hostname or "localhost"
    app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,  # mandatory: a stateful session pins one tenant
        host=settings.host,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[host, f"{host}:*"],
            allowed_origins=[settings.public_url, "https://claude.ai", "https://chatgpt.com"],
        ),
    )
    app.add_middleware(SecurityHeaders)
    app.state.session_manager = mcp._lowlevel_server._session_manager
    # consent.py reads request.app.state.consent_deps; request.app is THIS app (custom routes join it unmounted).
    # Set it on the returned app, after streamable_http_app() has run.
    app.state.consent_deps = consent.ConsentDeps(
        store=store,
        provider=provider,
        public_url=settings.public_url,
        allow_private_targets=settings.allow_private_targets,
        allowed_integrations=settings.allowed_integrations,
        publisher=settings.publisher,
    )

    # Wrap, not replace: the session manager only starts through the lifespan the SDK app already installed.
    session_manager_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        _sweep_once(store)
        async with session_manager_lifespan(app):
            # anyio waits for children on a normal exit instead of cancelling them, so the sweep must be cancelled
            # explicitly or the shutdown would hang on the sleeping task.
            async with anyio.create_task_group() as sweeps:
                sweeps.start_soon(_sweep_forever, store)
                try:
                    yield
                finally:
                    sweeps.cancel_scope.cancel()

    app.router.lifespan_context = lifespan
    return app


def main() -> None:
    """Entry point of the `evolution-api-mcp-remote` console script."""
    if "--help" in sys.argv[1:] or "-h" in sys.argv[1:]:
        print("usage: evolution-api-mcp-remote\n\nRun the hosted Evolution API Assistant MCP server.")
        return
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        settings = RemoteSettings.from_env()
        app = build_app(settings)
    except RuntimeError as exc:
        sys.exit(f"evolution-api-mcp-remote: {exc}")
    import uvicorn

    # No access log: /files/<token> and /consent?req=<id> carry bearer secrets in the URL.
    uvicorn.run(app, host=settings.host, port=settings.port, log_config=None, access_log=False)


if __name__ == "__main__":
    main()
