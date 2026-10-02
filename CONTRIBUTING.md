# Contributing to Evolution API Assistant

Thank you for contributing to Evolution API Assistant (`evolution-api-mcp`). Please follow these guidelines to set up
your development environment, run the tests and submit changes. [AGENTS.md](AGENTS.md) holds the design rules in full;
this page is the short version for people.

## Development environment setup

This project uses `uv` for Python package management and needs Python 3.10 or later.

1. **Install uv**: follow the instructions at [astral.sh/setup-uv](https://astral.sh/setup-uv).

2. **Clone the repository**:
   ```bash
   git clone https://github.com/singleflo/evolution-api-mcp.git
   cd evolution-api-mcp
   ```

3. **Sync dependencies** (the `remote` extra is needed for the hosted server and its tests):
   ```bash
   uv sync --extra remote
   ```

## Hard rules for code changes

* **The registry is the contract.** Every tool is declared once, with `@registry.tool(...)` in
  `src/evolution_api_mcp/tools/<toolset>.py`: title, toolset, kind, idempotency, integrations and flags. MCP hints are
  derived from the kind, never set by hand.
  Adding, renaming or re-classifying a tool means updating the tests that pin the tool set (`tests/test_registry.py`,
  `tests/test_tool_metadata.py`, `tests/test_protocol.py`), regenerating `docs/TOOLS.md`, and updating the README tables.
* **One tool per operation, reads and writes separate.** No generic "call any endpoint" tool.
* **Descriptions are declarative.** The docstring is the tool description: what it does in the first line (at most 120
  characters), then plain sentences about when to use it, what it returns and who sees the effect. No imperative
  directives to the model.
* **Evolution is the contract for requests.** Check the endpoint, body keys and response shape in Evolution's own source
  before changing what a tool sends or reads. Tests fake only Evolution's HTTP answers.
* **No stdout printing.** The stdio transport uses stdout for JSON-RPC. Diagnostics go to stderr.
* **Never return secrets.** Configuration passes through `redact.redact`; a secret is reported only as set or not set.
* **Never commit secrets.** No instance tokens, Evolution keys or Fernet keys in code, tests, docs or fixtures.

## Running tests

We use `pytest`.

### Default suite

```bash
uv run pytest
```

The default suite needs no Evolution server. The `addopts` in `pyproject.toml` already excludes the
opt-in markers (`live`, `wheel`, `remote_live`, `sandbox`). **Do not pass `-m "not live"`**: a command-line `-m`
replaces `addopts`, which silently re-enables the `wheel` and `sandbox` tests and fails on a clean checkout.

### Lint and format

```bash
uv run ruff check src tests scripts
uv run ruff format src tests scripts
```

### Docs

`docs/TOOLS.md` is generated from the registry and the tool docstrings. After changing a tool:

```bash
uv run python scripts/generate_docs.py
```

`tests/test_docs.py` fails when the file, or a number in the README, drifts from the registry.

### Sandbox tests (Docker)

`tests/sandbox/` starts a real Evolution API in Docker (with Postgres and Redis) and creates one unpaired instance:

```bash
tests/sandbox/up.sh
uv run pytest -m sandbox
tests/sandbox/down.sh
```

### Wheel tests

```bash
uv build
uv run pytest -m wheel
```

They run the built wheel through `uvx` and need `dist/` and network access.

### Live tests

To run tests against a real Evolution instance you opt in with the connection details. Sending stays off unless you
allow it, so a read-only run cannot message anyone:

```bash
export EVOLUTION_TEST_API_URL="https://evolution.example.com"
export EVOLUTION_TEST_INSTANCE_TOKEN="your-instance-token"
export EVOLUTION_TEST_CHAT="393331234567"      # a chat that agreed to receive test messages
export EVOLUTION_TEST_ALLOW_SEND=1              # optional: lets the send test run
uv run pytest -m live
```

Use an instance and a chat you own; the send test delivers a real message.

## Commit conventions

We follow conventional commits with a prose subject, matching the repository history: `feat(tools): ...`,
`fix(remote): ...`, `docs: ...`, `chore(release): ...`. Stage only intended files and never commit secrets. The
pre-commit hook runs gitleaks; install it with `pre-commit install`.

## Reporting a security issue

Do not open a public issue for a vulnerability. See [SECURITY.md](SECURITY.md).
