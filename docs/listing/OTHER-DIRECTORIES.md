# Secondary MCP Directories and Catalogs

> **Note:** None of the directory submissions listed below are executed as part of the release plan. They are optional distribution steps for the repository owner once `v1.0.0` is published to PyPI and the MCP Registry.

Publishing `evolution-api-mcp` to additional MCP marketplaces and catalogs increases discoverability beyond the main store listings. The official MCP Registry entry is published by the `publish.yml` workflow when the `v1.0.0` tag is pushed (name `io.github.singleflo/evolution-api-mcp`); it will be readable at:
`https://registry.modelcontextprotocol.io/v0.1/servers/io.github.singleflo%2Fevolution-api-mcp/versions`

Directory entries below were carried over from the reference project's notes. Where the current page was read while writing this file the entry says so; every other entry is marked **confirm on submission day**, because these portals change their forms without notice and nothing in CI re-checks them.

The local server takes two required environment variables, `EVOLUTION_API_URL` and `EVOLUTION_INSTANCE_TOKEN` (the instance's own token, never the server-wide Evolution API key). Every catalog that installs it locally must pass both.

## Confirmed directories

### Cursor Marketplace

- **URL**: https://cursor.com/marketplace/publish
- **What to paste**: Identity (Name, Tagline, Long description), Logo asset (`docs/listing/icon.png`), and repository URL from `docs/listing/README.md`. The page currently presents a publisher application for a plugin; its fields were not visible to an automated read — confirm on submission day.
- **Transport**: stdio accepted (local `uvx evolution-api-mcp` configuration for Cursor `.cursor/mcp.json`).
- **Review required**: Yes (publisher review by Anysphere).
- **Cost**: Free.

### Cline MCP Marketplace

- **URL**: https://github.com/cline/mcp-marketplace
- **What to paste**: Open a GitHub Issue using the `mcp-server-submission.yml` template in `cline/mcp-marketplace` (read on the repository on 2026-09-30). Include the repository link, a 400x400 PNG logo (`docs/listing/icon.png` is 512x512, so resize a copy) and the reason for addition. Cline's README asks the submitter to confirm that Cline can install the server from the `README.md` (or an optional `llms-install.md`) alone, so test that first.
- **Transport**: stdio accepted.
- **Review required**: Yes (maintainer review via GitHub issue; the approval process weighs community adoption, developer credibility and project maturity).
- **Cost**: Free (open source).

### Docker MCP Catalog

- **URL**: https://github.com/docker/mcp-registry
- **What to paste**: Submit a Pull Request with the server's metadata following the registry's `CONTRIBUTING.md` (read on the repository on 2026-09-30: Docker-built image or self-provided pre-built image). Reference the repository `Dockerfile`, the declared environment variables (`EVOLUTION_API_URL`, `EVOLUTION_INSTANCE_TOKEN` as a secret) and the description from `docs/listing/README.md`. The repository `Dockerfile` installs both console scripts but its default command starts the hosted server (`evolution-api-mcp-remote`); a stdio catalog entry must run `evolution-api-mcp` instead — confirm on submission day how the registry's metadata sets the command.
- **Transport**: stdio accepted (containerized stdio execution via Docker Desktop MCP Toolkit).
- **Review required**: Yes (Docker maintainer PR review).
- **Cost**: Free.

### Glama MCP Registry

- **URL**: https://glama.ai/mcp/servers
- **What to paste**: The reference project's notes record that Glama indexes the official MCP Registry automatically. Action required: log in to Glama and claim the listing with the repository owner's GitHub account. Not re-verified — confirm on submission day.
- **Transport**: stdio and remote accepted.
- **Review required**: No (indexed automatically, claim verification via GitHub auth).
- **Cost**: Free.

### mcp.so Directory

- **URL**: https://mcp.so
- **What to paste**: Submit via the submission page (https://mcp.so/submit), read on 2026-09-30: it offers free review or immediate publication with a paid tier. Paste Name, Short/Long description, Category, Repository URL, and Documentation URL from `docs/listing/README.md`.
- **Transport**: stdio and remote accepted.
- **Review required**: Yes on the free path (submission review).
- **Cost**: Free path available; a paid tier publishes immediately.

### VS Code MCP Gallery

- **URL**: https://code.visualstudio.com/docs/copilot/customization/mcp-servers
- **What to paste**: No action required. VS Code's MCP server gallery is fed from the official MCP Registry. Not re-verified — confirm on submission day.
- **Transport**: stdio and remote accepted.
- **Review required**: No separate submission (fed by official registry).
- **Cost**: Free.

### Gemini CLI Extensions

- **URL**: https://geminicli.com
- **What to paste**: The reference project recorded that Gemini CLI was deprecated and replaced by Antigravity CLI on June 18, 2026, and that a separate `gemini-extension.json` manifest is not worth a follow-up; standard `mcpServers` configuration in `~/.gemini/settings.json` with `uvx evolution-api-mcp` is sufficient. Not re-verified — confirm on submission day.
- **Transport**: stdio accepted.
- **Review required**: No (client-side configuration).
- **Cost**: Free.

## Dropped directories

- **PulseMCP** (https://pulsemcp.com/submit): Gated behind Cloudflare Bot Management returning HTTP 403 Access Denied during the reference project's automated verification. Dropped from active submission guidance.
