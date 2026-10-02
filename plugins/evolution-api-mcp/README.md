# evolution-api-mcp plugin

Evolution API Assistant as an installable plugin for Claude Code and Codex,
straight from this repository — no store, no review. The plugin wraps the same
stdio MCP server that `uvx evolution-api-mcp` runs.

It operates one Evolution API instance (one WhatsApp number). The local server
supports the `WHATSAPP-BAILEYS`, `WHATSAPP-BUSINESS` and `EVOLUTION`
integrations; the default toolsets are `messaging`, `chats` and `contacts`.

## Before you install

Export the two environment variables the server needs, in the shell you start
your host from:

```bash
export EVOLUTION_API_URL="https://evolution.example.com"   # your Evolution API 2.x server
export EVOLUTION_INSTANCE_TOKEN="the-instance-token"       # this one instance's token, not the global API key
```

The instance name and integration are discovered from the token. Evolution's
server-wide `AUTHENTICATION_API_KEY` is refused.

`uv` must be on your PATH (`curl -LsSf https://astral.sh/uv/install.sh | sh`),
because both manifests start the server with `uvx evolution-api-mcp`. Optional
settings (toolsets, allow and deny lists, pacing, file folders) are documented in
the [README](../../README.md#2-configure-environment-variables); set them as environment variables in
the same shell.

## Claude Code

```bash
/plugin marketplace add singleflo/evolution-api-mcp
/plugin install evolution-api-mcp@evolution-api-mcp
```

Then check `/mcp` shows the server. The marketplace and the plugin share the
name `evolution-api-mcp`, hence the `@evolution-api-mcp` suffix.

## Codex

```bash
codex plugin marketplace add https://github.com/singleflo/evolution-api-mcp
```

Then install from the Plugins Directory, "Personal" tab. The portable
manifests are `plugin.json` and `mcp.json` (Agent Plugins 1.0.0); the Claude
Code shapes are `.claude-plugin/plugin.json` and `.mcp.json`.

## Support

The Agent Plugins 1.0.0 manifest schema has no support field, so support
requests go to the issue tracker:
https://github.com/singleflo/evolution-api-mcp/issues

## Versions move together

`pyproject.toml`, `server.json`, `plugin.json`, `.claude-plugin/plugin.json` and
the entry in `.claude-plugin/marketplace.json` must carry the same version —
`tests/test_marketplace_manifests.py` fails on drift.

Evolution API Assistant is an independent project by Persevida SL, not
affiliated with or endorsed by Meta, WhatsApp or the Evolution API project.
