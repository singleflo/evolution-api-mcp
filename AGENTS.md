# AGENTS.md — evolution-api-mcp

MCP server that operates ONE Evolution API v2 instance (server URL plus that instance's own token) with a per-instance
tool surface grouped in toolsets. Two builds share one code base: the local stdio server (`evolution-api-mcp`, run with
`uvx`) and the hosted Streamable HTTP server (`evolution-api-mcp-remote`, `remote/`), which is the store listing.
Version 1.0.0. Creating, deleting or listing other instances on the Evolution server is out of scope by design.

## Read before coding

- `README.md` — what users are told: configuration, toolsets, safety model. `docs/TOOLS.md` is generated from the
  registry (never edit it by hand).
- `docs/mcp-spec/` — the MCP spec, vendored. Consult it instead of the web.
- `docs/RELEASE-CHECKLIST.md` — the release steps that need a human.
- `docs/research/` is git-ignored planning evidence (the implementation plan, store requirements, Evolution contract
  notes). It exists only in the original checkout. Where it disagrees with the code or with the Evolution source, the
  code and the source win.
- Evolution's own source (v2.3.7) is the contract for every endpoint, body key and response shape. Read it before
  changing what a tool sends or projects; do not infer from another project's client.
- `skill://build-mcp-server` (in `.agents/skills/`) and its `references/tool-design.md` before writing or renaming a tool.

## Commands

```bash
uv sync --extra remote                  # dev environment
uv run ruff check src tests scripts     # lint (E, F, I, B, UP, ASYNC; line length 120)
uv run ruff format src tests scripts    # format
uv run pytest                           # the default suite. addopts already excludes live, wheel, remote_live, sandbox
uv build && uv run pytest -m wheel      # the built wheel through uvx; needs dist/ and network
tests/sandbox/up.sh && uv run pytest -m sandbox   # against a Docker Evolution; tests/sandbox/down.sh afterwards
uv run pytest -m live                   # a real instance; needs the EVOLUTION_TEST_* variables (see tests/test_live.py)
uv run python scripts/generate_docs.py  # rewrite docs/TOOLS.md; add --check to fail on drift
uv run python scripts/check-release-consistency.py
uv run evolution-api-mcp --list-tools   # the whole catalog as JSON, no credentials needed
uv run evolution-api-mcp --list-toolsets
```

**Never write `-m "not live"`.** A `-m` on the command line replaces `addopts` instead of narrowing it, so the `wheel`,
`remote_live` and `sandbox` tests silently come back and fail on a clean checkout. Run plain `uv run pytest`.

Protocol check: `npx @modelcontextprotocol/inspector --cli uv run evolution-api-mcp --method tools/list`.

## Layout

- `registry.py` — the `@tool` decorator, `ToolSpec`, annotations, `register_all`. **The registry is the contract**:
  every tool's toolset, kind, idempotency, integrations, `local_only` and `universal` flags are declared once, in the
  decorator. `toolsets.py` holds the toolset names, descriptions, presets and default.
- `tools/<toolset>.py` — one module per toolset, each tool an `async def` decorated with `@registry.tool`.
  `tools/__init__.py` imports every module with `pkgutil` (`load_all()`; there is no hand list) and defines the shared
  `Annotated` parameter types (`Chat`, `Group`, `MessageId`, `ReplyTo`, `Mention`, `MentionEveryone`, `DelayMs`,
  `MediaUrl`).
- `policy.py` — `refusal()`/`visible()`, the one function behind both the per-call gate and the `tools/list` filter.
  `middleware.py` filters `tools/list`; the registry wrapper refuses a hidden tool with the same message when called.
- `context.py` — the `Connection` a call runs against; local (cached discovery, one client) or hosted (bound tenant).
  `discovery.py` identifies the instance from its token and refuses the global API key. `config.py` reads the local
  environment. `tenant.py` is the hosted seam.
- `client.py`, `calls.py`, `errors.py`, `netguard.py` — HTTP client, the single way a tool calls Evolution, the error
  mapping, the hosted SSRF guard. `jid.py`, `messages.py`, `sending.py`, `media.py`, `redact.py`, `ratelimit.py` — helpers.
- `remote/` — the hosted server (OAuth 2.1 authorization server, consent page, tenant store, file links).
- `assets/guide.md` — served as the `evolution://guide` resource; it sits inside the package so the wheel ships it.

## House rules

- **One tool per operation.** Reads and writes are separate tools. There is no generic executor, discovery or "call any
  endpoint" tool: both stores reject them. Names are snake_case, `^[a-z][a-z0-9_]{2,63}$`, and deterministic in order.
- **Titles and hints.** Every tool carries its title in `Tool.title` AND `annotations.title` (the Claude submission
  portal reads only the second). All four hints are explicit booleans and are derived from the kind by
  `registry.annotations_for`; never set a hint by hand. `openWorldHint` is true on every tool, because each acts on a
  user-supplied external server and sends reach other people.
- **Kinds.** `read`; `write` (private or ephemeral state: presence, archive, unread, labels, unblock, downloads, new
  drafts such as templates and OpenAI credentials, pairing); `destructive` (sends or exposes content to other people,
  replaces existing content or configuration, removes people or revokes access); `irreversible` (cannot be undone from
  this server: logout, delete for everyone, leave group, delete template, chatbot or credential). When in doubt between
  `write` and `destructive`, choose `destructive`: undoability does not by itself justify `false`.
- **Descriptions.** The docstring is the description. First line: what the tool does, at most 120 characters. Then plain
  declarative sentences: when to use it against its siblings, parameter meaning the schema does not carry, what it
  returns, side effects (who sees it, whether it can be undone), integration limits. Never imperative directives to the
  model ("you must", "always call", "ignore previous"): review treats them as prompt injection. Cross-tool guidance
  lives only in the server `instructions`. Every parameter has a `Field(description=...)`.
- **Output.** Compact JSON text through `errors.tool_result` (registered with `structured_output=False`); only
  `view_message_image` and `start_pairing` return content blocks. Results are capped at 10,000 characters with a notice
  that names the remedy. Never return tokens, API keys, passwords, secrets, Evolution database ids, device tags or raw
  protobuf blobs; pass configuration through `redact.redact` before projecting it, and show secrets only as `..._set`.
  Timestamps are ISO-8601 UTC. Tools whose inputs carry secrets (passwords, API keys, tokens) are `local_only`: the
  stores restrict collecting credentials.
- **Errors.** Three outcomes: success; refused or failed, ending "Nothing was changed." or "Nothing was sent."; and
  UNCERTAIN after a write that may have applied, which carries a re-read of the state and says not to repeat the call.
  Every failure raises `ToolExecutionError` (a `ToolError`) so the SDK forwards the message. No custom JSON-RPC codes.
  Route Evolution calls through `calls.call`, sends through `sending.send`.
- **Safety gate.** Decided by `policy.refusal` in a fixed order: local-only, toolset, integration, read, read-only,
  irreversible grant, deny list, allow list. Reads are never limited by the lists. `EVOLUTION_MCP_ALLOW` (`*`, `none`,
  or a list of tools that change data), `EVOLUTION_MCP_DENY` (unset means `policy.DEFAULT_DENY`; a value replaces it),
  `EVOLUTION_MCP_ALLOW_IRREVERSIBLE=yes` as the only grant for `irreversible`. Unknown tool names in ALLOW/DENY and
  unknown toolsets fail startup, so a typo cannot fail open. The hosted server has policy `read` or `standard`, the
  fixed default deny list, no irreversible tools and no local-only tools.
- **Pacing and rate limits.** Every send passes `delay` (default 1200 ms, range 0-20000). Local writes are limited to
  30 per minute by default; hosted 30 writes and 120 reads per minute per tenant.
- **Media.** Outgoing media is an http(s) URL Evolution downloads; local files go only through `send_local_file` on the
  local server, inside the configured roots, never hidden paths, at most 100 MiB. Incoming media: `view_message_image`
  (inline, up to 4 MiB) and `download_message_media` (a file locally, a 15-minute link when hosted).
- **Language and style.** Everything shipped is English. Commit messages are conventional commits with a prose subject
  (`feat(tools): ...`, `fix(remote): ...`).
- **Evolution quirks the code works around** (each was read in Evolution's source): the
  `findMessages` chat filter must send both `remoteJid` and `remoteJidAlt`; `fromMe: false` is dropped server-side, so
  incoming messages are filtered client-side; the time filter applies only when both ends are present and takes ISO
  strings; a Business rejection arrives as an HTTP 201 body; edit and delete limits are enforced here because Evolution
  does not enforce them. Change none of these without re-reading the source.
- **stdio transport: nothing may reach stdout** but the JSON-RPC stream. Diagnostics go to stderr. `print()` is allowed
  only for `--list-tools`, `--list-toolsets` and `--version`.
- **Never commit secrets.** Instance tokens, Evolution keys, Fernet keys and OAuth tokens stay out of the repository,
  docs, test fixtures and store dossiers. `.gitleaks.toml` and the pre-commit hook guard this.

## Adding or changing a tool

1. Write it in the `tools/<toolset>.py` of its toolset with `@registry.tool(...)`: title, toolset, kind, idempotent,
   integrations and flags exactly as the rubric above says.
2. Add its row to `MASTER_TABLE` in `tests/test_registry.py`; three tests pin the tool set: `test_registry.py` (every
   spec), `test_tool_metadata.py` (the counts and the hints over the wire) and `test_protocol.py` (the list a stdio
   session shows). Update the counts they assert and the hosted-Business set the listing dossier compares against.
3. Add tool tests in `tests/test_tools_<toolset>.py`: the exact request Evolution receives (method, path, body) and the
   projected result, plus every guard. Fake only Evolution's HTTP answers (`tests/fakes.py`), never our own layers.
4. Run `uv run python scripts/generate_docs.py`, then update the README toolset and integration tables (`tests/test_docs.py`
   compares them with the registry) and `src/evolution_api_mcp/assets/guide.md` when the integration matrix changes.

## Testing notes

- Async tests use `@pytest.mark.anyio` with the `anyio_backend` fixture (asyncio). `tests/conftest.py` gives `evo`, `bound`,
  `make_connection` and `make_identity`.
- Live and sandbox runs send real requests. `EVOLUTION_TEST_ALLOW_SEND=1` is test-suite-only and must never appear in a
  host configuration.
- The hosted server runs locally with `scripts/remote_token.py` and `scripts/remote_smoke.py`; see `docs/REMOTE.md`.
