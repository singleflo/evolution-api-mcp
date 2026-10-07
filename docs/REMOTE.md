# The hosted server: a developer guide

`evolution-api-mcp-remote` is the same package's second front door: one process serving the per-instance tools over
streamable HTTP at `/mcp`, with its own OAuth 2.1 authorization server in front of them. Instead of reading one
Evolution API connection from the environment, every request is bound to the person signed in (a tenant) whose
Evolution server address and instance token they entered on the consent page.

The user-facing side of this story is in the README, under the hosted server section. This guide is for running and
deploying that server yourself: locally, through a tunnel, against the Docker sandbox and on Coolify.

## Run it locally

```bash
uv sync --extra remote
export EVOLUTION_REMOTE_PUBLIC_URL=http://127.0.0.1:8000
export EVOLUTION_REMOTE_SECRET_KEY=$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')
uv run evolution-api-mcp-remote
```

This listens on `127.0.0.1:8000`. The variables (an empty value counts as unset, because the compose file passes every
optional variable as `${VAR:-}`):

| Variable | Required | Meaning |
|---|---|---|
| `EVOLUTION_REMOTE_PUBLIC_URL` | Yes | The address clients connect to. Must be an `https://` URL (or `http://localhost` / `http://127.0.0.1` for local runs). Everything the OAuth flow advertises (issuer, protected-resource metadata, consent and token URLs) is derived from it, character for character. |
| `EVOLUTION_REMOTE_SECRET_KEY` | Yes | A Fernet key. It encrypts every tenant's instance token and every stored OAuth client at rest; see "Rotating the secret" below. |
| `PORT` | No | Listen port. Defaults to the public URL's own port when it carries one, else 8000. |
| `EVOLUTION_REMOTE_HOST` | No | Bind address, default `0.0.0.0`. |
| `EVOLUTION_REMOTE_ALLOWED_INTEGRATIONS` | No | Which Evolution integrations the consent page accepts: `WHATSAPP-BUSINESS` (the default), `*`, or a comma-separated list of `WHATSAPP-BAILEYS`, `WHATSAPP-BUSINESS`, `EVOLUTION`. Anything else stops the server at startup. |
| `EVOLUTION_REMOTE_PUBLISHER` | No | The operator's name on the legal pages and the footer. Default `Persevida SL`. |
| `EVOLUTION_REMOTE_SUPPORT_EMAIL` | No | Where the legal pages send support requests: an address or an `https://` URL. Default the GitHub issues page. |
| `EVOLUTION_REMOTE_OPENAI_CHALLENGE` | No | The token OpenAI's domain verification asks for; served as plain text at `/.well-known/openai-apps-challenge`. Unset, that path answers 404. |
| `EVOLUTION_REMOTE_ALLOW_PRIVATE_TARGETS` | No | `1` (exactly) lets a consent form point at a private Evolution address; see "An Evolution server on a private address". |
| `EVOLUTION_MCP_DATA_DIR` | No | Where `remote.db` and the downloaded-file area live. Default: the platform's per-user data directory. The Docker image sets `/data`. |

If the process starts with `EVOLUTION_API_URL`, `EVOLUTION_INSTANCE_TOKEN`, `EVOLUTION_MCP_TOOLSETS`,
`EVOLUTION_MCP_ALLOW`, `EVOLUTION_MCP_DENY` or `EVOLUTION_MCP_ALLOW_IRREVERSIBLE` still in the environment, it refuses
to start: the hosted server is multi-tenant, takes its policy from each connection's consent, and must never lend one
connection or the local server's switches to the rest.

Check it came up:

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok","version":"…"}
```

Keep the address you use and `EVOLUTION_REMOTE_PUBLIC_URL` consistent. The server refuses requests whose `Host` header
does not match the public URL (DNS-rebinding protection), so pointing the client at `127.0.0.1:8000` while the server
advertises `localhost:8000`, or the other way round, answers 421 and looks like a protocol failure. It is a spelling
mismatch.

## Which instances it accepts

The consent page verifies the instance before it stores anything: the server must answer like Evolution API 2.x, the
token must be an instance token (the server-wide `AUTHENTICATION_API_KEY` is refused), and the token must belong to
exactly one instance. It then checks the instance's integration against `EVOLUTION_REMOTE_ALLOWED_INTEGRATIONS`. The
public deployment keeps the default, `WHATSAPP-BUSINESS`, so an instance on another integration is refused with:

> This hosted server connects WhatsApp Business Platform instances only (integration WHATSAPP-BUSINESS). This instance
> uses WHATSAPP-BAILEYS; run the local server (uvx evolution-api-mcp) for it.

A private deployment can widen the list. What a connection can do is then decided per request by the same gate the
local server uses: the toolsets and the policy chosen on the consent page, the instance's integration, and the hosted
rules (no irreversible tools, no local-only tools, the default deny list).

## Get a token: scripts/remote_token.py

The token script walks the whole flow a host walks (dynamic client registration, PKCE, `/authorize`, the consent page,
which is the step that stores your connection as a tenant, and the code exchange) and prints the access token:

```bash
uv run python scripts/remote_token.py --help
export EVOLUTION_TEST_API_URL=https://your-evolution.example.com
export EVOLUTION_TEST_INSTANCE_TOKEN=the-token-of-your-test-instance
uv run python scripts/remote_token.py http://127.0.0.1:8000 --policy standard --toolsets messaging,chats,contacts
```

`--evolution-url` and `--instance-token` override the two environment variables. `--policy` is the choice the consent
page offers: `standard` (the default) or `read`. `--toolsets` takes toolset names or the presets `core` and `all`
(default `core`). When the consent page refuses the instance the script exits 1 and prints the page's message.

**Test against a test instance only.** A `standard` token can send real messages from whatever instance the consent
form named. Point it at an instance whose owner agreed to receive them.

## Smoke test: scripts/remote_smoke.py

```bash
TOKEN=$(uv run python scripts/remote_token.py http://127.0.0.1:8000 --toolsets messaging,chats,contacts)
uv run python scripts/remote_smoke.py http://127.0.0.1:8000 "$TOKEN"
```

It speaks plain JSON-RPC over HTTP: `initialize`, `tools/list`, then `tools/call get_instance_status`. It prints the
number of tools this connection was granted and the status the server reported, and exits 1 on any error.

For a freshly deployed endpoint, before any OAuth traffic:

```bash
bash scripts/remote_live_check.sh https://evolution-mcp.singleflo.com
```

Three checks, each PASS or FAIL: `/health` answers 200, the protected-resource metadata answers 200, and `/mcp`
without a bearer token answers 401 naming that metadata.

## Verify with MCP Inspector

From the repository root:

```bash
npx @modelcontextprotocol/inspector --cli http://127.0.0.1:8000/mcp \
  --transport http \
  --header "Authorization: Bearer <token>" \
  --method tools/list
```

The JSON wraps the tools as `{"result":{"tools":[…]}}`. Each carries a `title` and the four annotation hints. A
`standard` connection with every toolset on a `WHATSAPP-BUSINESS` instance sees 41 tools; a `read` connection sees only
the tools that read (22 on that instance).

Two Inspector CLI traps:

* **Exported variables do not reach a server the Inspector launches.** When the Inspector starts the server itself (a
  stdio command), pass values as `-e KEY=value` on the Inspector command line. The HTTP invocation above has no child
  process; the token goes in the `--header`.
* **Never pass `--directory`.** Any flag name the Inspector itself defines is consumed by the Inspector instead of
  forwarded, so the target silently mis-reads. That is why the invocation runs from the repository root.

## Expose it with a tunnel

Claude.ai and ChatGPT can only reach public HTTPS addresses. To test the local server from a real host, put a tunnel in
front of it:

```bash
cloudflared tunnel --url http://localhost:8000
# … https://something-random.trycloudflare.com
export EVOLUTION_REMOTE_PUBLIC_URL=https://something-random.trycloudflare.com
uv run evolution-api-mcp-remote
```

Start the tunnel first: it prints the URL you must then set. **`EVOLUTION_REMOTE_PUBLIC_URL` must equal the tunnel URL
exactly.** The issuer URL and the protected-resource metadata (RFC 9728) are derived from that variable, and a host
authenticating against the tunnel fetches `/.well-known/…` from it and compares what it advertises with the URL it is
talking to: an exact match, no normalization. If the server advertises `http://127.0.0.1:8000` while the client is on
the tunnel, the failure is a protected-resource metadata mismatch and reads like an authentication bug. Check that the
two URLs match before debugging anything else. `ngrok http 8000` works the same way.

A tunnel exposes your local server to the public internet. Authentication stays on (it is not optional in this server)
and the tunnel should be shut down when you are done testing.

## An Evolution server on a private address

By default the consent page refuses an Evolution URL that resolves to a private or loopback address, and the client
that later calls Evolution refuses it again at connect time, on the address it is about to dial. The server holds a
token and makes authenticated connections on a stranger's instruction; without those checks any signed-in tenant could
aim it at your internal network. A development Evolution on `192.168.x.x`, `10.x.x.x` or `localhost` therefore needs:

```bash
export EVOLUTION_REMOTE_ALLOW_PRIVATE_TARGETS=1
```

Set it for local testing and never in production: on the deployed server that variable is the door to everything behind
the proxy, and no legitimate tenant is on a private address.

### Against the Docker sandbox

`tests/sandbox/up.sh` starts a real Evolution API in Docker on `127.0.0.1:18080` with the instance `mcp-sandbox`
(token `sandbox-instance-token`, integration `WHATSAPP-BAILEYS`); `tests/sandbox/down.sh` removes it. The hosted server
accepts that instance only when it is allowed to reach a loopback address and to accept Baileys instances:

```bash
export EVOLUTION_REMOTE_PUBLIC_URL=http://localhost:8765 PORT=8765
export EVOLUTION_REMOTE_ALLOW_PRIVATE_TARGETS=1 EVOLUTION_REMOTE_ALLOWED_INTEGRATIONS='*'
export EVOLUTION_MCP_DATA_DIR=$(mktemp -d)
export EVOLUTION_REMOTE_SECRET_KEY=$(uv run python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')
uv run evolution-api-mcp-remote &

bash scripts/remote_live_check.sh http://localhost:8765
TOKEN=$(uv run python scripts/remote_token.py http://localhost:8765 \
  --evolution-url http://127.0.0.1:18080 --instance-token sandbox-instance-token \
  --toolsets messaging,chats,contacts,settings)
uv run python scripts/remote_smoke.py http://localhost:8765 "$TOKEN"
```

The smoke script prints `34 tools` and a status naming `mcp-sandbox` with `"state": "close"` (the sandbox session is
never paired). Restart the server without `EVOLUTION_REMOTE_ALLOWED_INTEGRATIONS` and `remote_token.py` exits 1 with
the Business-only refusal quoted above.

## Connect from the hosts

The host-side snippets live in the README and are repeated here so this guide is complete on its own. All of them point
at the deployed server; for a tunnel, substitute the tunnel URL.

* **Claude.ai**: Customize → Connectors → Add custom connector, URL `https://evolution-mcp.singleflo.com/mcp`; the
  first use lands on the consent page.
* **ChatGPT**: chatgpt.com/plugins → plus button → Add custom MCP server, enter `https://evolution-mcp.singleflo.com/mcp`
  under Connection, then **Create as a plugin**.
* **Claude Code**: `claude mcp add --transport http evolution-api-mcp https://evolution-mcp.singleflo.com/mcp`
* **Codex**: `codex mcp add evolution-api-mcp --url https://evolution-mcp.singleflo.com/mcp` then
  `codex mcp login evolution-api-mcp`

## What the server keeps, and for how long

One SQLite database, `remote.db` under the server's data directory, holds the OAuth artifacts and the tenants. Per
tenant: the Evolution URL, the instance name and integration Evolution reported at connect time, the policy and the
toolsets chosen on the consent page, and the instance token, encrypted with `EVOLUTION_REMOTE_SECRET_KEY` and never
stored in the clear. Sign-in tokens are stored only as SHA-256 hashes: access tokens live one hour, refresh tokens 30
days, authorization codes and pending consent requests 10 minutes. Messages, contacts and conversation content are
never stored.

The policy is what the gate enforces for that person's requests: `read` admits only the tools that read; `standard`
admits reads and writes, still under the default deny list (`post_status`, `remove_group_participants` and the
webhook, event-channel, proxy and Chatwoot configuration tools). Tools that cannot be undone (`logout_instance`,
`delete_message_for_everyone`, `leave_group`, deleting templates, chatbots and OpenAI credentials) are never
available on the hosted server under either choice, and the eight local-only tools (the ones that take secrets as
input, plus `send_local_files`) are not registered on it at all. Writes are limited to 30 per minute and reads to 120
per minute for each connection, and sends default to a 1200 ms typing delay.

Media a tool downloads never comes back as a blob. `download_message_media` moves the file under the tenant's files
area and answers with a link `…/files/<token>` that serves the bytes for **fifteen minutes**; the token is the
credential, an expired link answers 404 and deletes the file, and the response carries `Cache-Control: private,
no-store` so no shared cache keeps a customer's attachment. `export_chat` works the same way: it builds the export in
a temporary folder, zips it (at most 100 MiB), moves the ZIP under the files area and answers with such a link.

The retention sweep runs at startup and then every hour (`SWEEP_SECONDS` in `remote/app.py`): expired OAuth rows,
expired file links, and tenants idle for 90 days without a live token together with their files. Disconnecting the
connector in the host revokes its tokens and, when the last one goes, deletes the tenant and its files.

## Rotating the secret

`EVOLUTION_REMOTE_SECRET_KEY` encrypts every stored instance token and OAuth client. Rotating it (generate a new Fernet
key and restart) makes everything already stored undecryptable. There is no migration path, by design: a key that could
decrypt under its successor would not be worth rotating. Stop the server, delete `remote.db` from the data directory,
start it with the new key, and have every tenant connect again through the consent page.

## Deploying to Coolify

The deployed instance serves `https://evolution-mcp.singleflo.com`. It is a Coolify application in Docker Compose mode
built from `docker-compose.yaml` and the `Dockerfile`. Set these variables on the application:

* `EVOLUTION_REMOTE_PUBLIC_URL`: required, `https://evolution-mcp.singleflo.com`.
* `EVOLUTION_REMOTE_SECRET_KEY`: required, a Fernet key generated once and kept.
* `EVOLUTION_REMOTE_OPENAI_CHALLENGE`, `EVOLUTION_REMOTE_PUBLISHER`, `EVOLUTION_REMOTE_SUPPORT_EMAIL`,
  `EVOLUTION_REMOTE_ALLOWED_INTEGRATIONS`: optional; leave blank for the defaults.

Do not set `EVOLUTION_REMOTE_ALLOW_PRIVATE_TARGETS` there. The `mcp_data` volume holds `/data` (the database and the
files area); keep it across deploys.

Deploy by pushing a `v*` tag: `.github/workflows/deploy.yml` runs the tests and then calls the Coolify deploy webhook,
and skips the webhook when its secrets are absent. Or press **Redeploy** in Coolify for the same commit.

Then check health **first**:

```bash
curl https://evolution-mcp.singleflo.com/health
```

While the container is starting or failing its health check, Coolify's proxy answers `No available server` as plain
text. That is the proxy speaking, not an error from the application: there may be nothing listening yet, and the deploy
logs, not an HTTP status, tell you which. Once the container is up, the same URL returns
`{"status":"ok","version":"…"}`. Then run `scripts/remote_live_check.sh`.

One configuration rule binds this document to `server.json`: on the deployed instance, `EVOLUTION_REMOTE_PUBLIC_URL`
must be `https://evolution-mcp.singleflo.com`, character for character. The registry entry's remote URL is that plus
`/mcp`, and the two are only correct together. OpenAI also binds the origin to the listing: once published it can never
change.

## Store submission

The listing dossier (public copy, starter prompts, test cases, annotation justifications, icon) and the per-directory
submission guides live in `docs/listing/`. Submitting to the Claude directory or the ChatGPT Plugin Directory is a
human step; the guides walk it, the agents stop at preparing everything up to the click.

## Sources

* https://claude.com/docs/connectors/custom/remote-mcp
* https://claude.com/docs/connectors/building/directory-vs-custom
* https://claude.com/docs/connectors/building/testing
* https://developers.openai.com/plugins/deploy/connect-chatgpt
* https://developers.openai.com/codex/cli/reference
* https://code.claude.com/docs/en/mcp
* https://modelcontextprotocol.io/registry/remote-servers
