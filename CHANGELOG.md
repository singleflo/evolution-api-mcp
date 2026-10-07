# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-10-07

First release. An MCP server that operates one Evolution API v2 instance (one WhatsApp number) with that instance's own
token, as a local stdio server and as a hosted Streamable HTTP server.

### Added
- **100 tools in 13 toolsets** covering the per-instance surface of Evolution API: `instance`, `messaging`, `chats`,
  `contacts`, `groups`, `labels`, `profile`, `status`, `catalog`, `templates`, `settings`, `events` and `integrations`.
  One tool per operation, reads and writes in separate tools, no generic "call any endpoint" tool. The generated
  catalog is `docs/TOOLS.md`.
- **Toolsets** are chosen with `EVOLUTION_MCP_TOOLSETS` or `--toolsets` (presets `core`, the default, and `all`), or on
  the consent page of the hosted server. `--list-tools` and `--list-toolsets` print the catalog as JSON without
  credentials.
- **Discovery.** The server learns the instance name and its integration (`WHATSAPP-BAILEYS`, `WHATSAPP-BUSINESS`,
  `EVOLUTION`) from the token and refuses Evolution's global `AUTHENTICATION_API_KEY`, tokens shared by several
  instances, non-Evolution URLs and Evolution versions other than 2.x. `tools/list` shows only the tools the instance's
  integration supports.
- **Safety gate.** Every tool has a kind (`read`, `write`, `destructive`, `irreversible`) from which the MCP hints are
  derived. `EVOLUTION_MCP_ALLOW` (`*`, `none`, or a list), `EVOLUTION_MCP_DENY` (a default list of six tools that a value
  replaces), and `EVOLUTION_MCP_ALLOW_IRREVERSIBLE=yes` as the only grant for the six tools that cannot be undone. Unknown
  names fail startup. The same function decides both the per-call gate and the `tools/list` filter.
- **Sending.** Text, media from a URL, voice notes, video notes, stickers, locations, contact cards, polls, list and
  button messages, reactions, edits and deletion for everyone, with quoted replies, mentions, typing-indicator pacing
  (`EVOLUTION_MCP_DEFAULT_DELAY_MS`, default 1200 ms) and a per-minute write limit
  (`EVOLUTION_MCP_MAX_WRITES_PER_MINUTE`, default 30). `send_local_files` sends up to 10 files from configured folders on
  the local server and `forward_message` re-sends a message to up to 5 chats; each message counts against the write
  limit. WhatsApp Business Platform rejections, including the 24-hour window, are reported as refusals.
- **Reading.** Chats, message history with a documented client-side text search, delivery status, images inline and media
  downloads (a file locally, a 15-minute link on the hosted server).
- **Chat export.** `export_chat` saves a chat or a period of it as Markdown, JSON or WhatsApp-style text, with its
  attachments: a folder under `exports/` of the download folder locally, a ZIP behind a 15-minute link when hosted.
  Attachments are fetched four at a time and none starts after 90 seconds; what is left out is listed in
  `media_skipped` with a reason, never with Evolution's signed download address.
- **Errors in three outcomes.** Success; refused or failed with "Nothing was changed" or "Nothing was sent"; and
  UNCERTAIN after a write that may have applied, which carries a re-read of the state and says not to repeat the call.
- **Secrets stay out.** Configuration reads are redacted and show only whether a secret is set. The eight tools whose
  inputs carry secrets or local files are local-only.
- **Hosted server** at `https://evolution-mcp.singleflo.com/mcp` with an in-process OAuth 2.1 authorization server
  (dynamic client registration, PKCE S256, refresh rotation, revocation), a consent page that collects the Evolution URL,
  the instance token, a `read` or `standard` policy and the toolsets, encrypted token storage, a connect-time SSRF guard,
  per-tenant rate limits (30 changes and 120 reads per minute) and privacy, terms and support pages. It accepts
  `WHATSAPP-BUSINESS` instances only unless a private deployment sets `EVOLUTION_REMOTE_ALLOWED_INTEGRATIONS`, and never
  offers the irreversible, local-only or default-denied tools.
- **Distribution.** PyPI package `evolution-api-mcp` (console scripts `evolution-api-mcp` and
  `evolution-api-mcp-remote`), MCP Registry entry `io.github.singleflo/evolution-api-mcp`, Claude Code and Codex plugin
  manifests, Docker image for the hosted server, and the store listing dossier under `docs/listing/`.
- **Documentation.** README with configuration for fifteen hosts, `docs/INSTALL-WITH-YOUR-AGENT.md`, `docs/REMOTE.md`,
  `docs/RELEASE-CHECKLIST.md`, `SECURITY.md`, `CONTRIBUTING.md`, `AGENTS.md`, and the `evolution://guide` resource.
