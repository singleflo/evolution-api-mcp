# Install Evolution API Assistant — instructions for the AI agent reading this

You were given this URL by a person who wants one of their Evolution API instances (one WhatsApp number) connected to
you. Work through the steps below in order and do the work yourself: they should not have to edit a file by hand. What
is not spelled out here is in the README, which each step links into:
<https://github.com/singleflo/evolution-api-mcp/blob/main/README.md>

## 0. Ask the person three things, and only three

1. **The Evolution server URL** — for example `https://evolution.example.com`. A trailing slash is fine.
2. **The token of the one instance — never the server's global API key.** In Evolution Manager, open the instance and
   copy its token; it is also the token returned when the instance was created. This server refuses the global
   `AUTHENTICATION_API_KEY` by design, because it controls every instance on that Evolution server. Tell the person the
   token gives this assistant everything an owner of that instance can do.
3. **What the assistant may do** — one of: *read only* (look at chats and messages, send nothing), *the default*
   (messaging, chats and contacts: read, search and send), or *more* (name the extra areas: groups, labels, profile,
   status, catalog, templates, settings, events, integrations, or `all`).

Ask for nothing else. The instance name and its integration are discovered from the token, and every other setting has
a working default.

## 1. Detect the OS and the host you are running in

Find the operating system first:

- macOS or Linux: `uname -s` prints `Darwin` or `Linux`.
- Windows PowerShell: `$env:OS` prints `Windows_NT`.

Then work out which application you are running inside, because it decides which file you write in step 3. Check which
of these exist:

| Host | Look for |
|---|---|
| Claude Code | the `claude` command on `PATH`, `~/.claude.json`, or `.mcp.json` in the project |
| Claude Desktop | `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows) |
| OpenAI Codex CLI | `~/.codex/config.toml` |
| omp | the `omp` command on `PATH`, `~/.omp/agent/mcp.json`, or `.omp/mcp.json` in the project |
| GitHub Copilot CLI | the `copilot` command on `PATH`, or `~/.copilot/mcp-config.json` |
| Antigravity | `~/.gemini/config/mcp_config.json`, or `.agents/mcp_config.json` in the project |
| Kiro | `~/.kiro/settings/mcp.json` or `.kiro/settings/mcp.json` |
| Goose | `~/.config/goose/config.yaml` |
| LM Studio | the LM Studio application; its `mcp.json` opens from the Program tab with Install → Edit mcp.json |
| Kilo Code | `~/.config/kilo/kilo.jsonc` or `.kilo/kilo.jsonc` |
| Continue | a `.continue/` folder in the project |
| Qwen Code | `~/.qwen/settings.json` |
| Amp | `~/.config/amp/settings.json` |
| Warp | `~/.warp/.mcp.json` |
| Factory Droid | `~/.factory/mcp.json` |
| opencode | `~/.config/opencode/opencode.json` or `.jsonc`, or `opencode.json` in the project |
| Hermes | `~/.hermes/config.yaml` |
| Cursor | `~/.cursor/mcp.json` or `.cursor/mcp.json` |
| Windsurf (Devin Desktop) | `~/.config/devin/mcp_config.json` (Windows: `%APPDATA%\devin\mcp_config.json`), or `~/.codeium/windsurf/mcp_config.json` |
| VS Code / GitHub Copilot | `.mcp.json` or `.vscode/mcp.json` in the project, or `~/.copilot/mcp-config.json` |
| Gemini CLI | `~/.gemini/settings.json` |
| Zed | `~/.config/zed/settings.json`; if it is not there, have them open it from the Command Palette with `zed: open settings file` |

If several match, or none does, **ask the person which application they are talking to you in**. Do not guess. A
correct entry written into the wrong file gives them a server that never starts and no error to read.

## 2. Make sure `uv` is installed

The server runs as `uvx evolution-api-mcp`, so `uvx` has to exist. Run `uvx --version` first; if it prints a version, go
to step 3. Otherwise install uv — macOS and Linux:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Source: <https://docs.astral.sh/uv/getting-started/installation/>

Then confirm with `uvx --version` again, in a **new** shell: the installer puts `uv` on the `PATH` of shells started
after it, not the one you are in.

## 3. Write the host configuration

Twenty-four hosts have a local snippet in the README, each checked against that host's own documentation:
[Host Configuration Examples](https://github.com/singleflo/evolution-api-mcp/blob/main/README.md#host-configuration-examples).
Anchors follow the heading, lowercased and hyphenated: `#omp-oh-my-pi`, `#github-copilot-cli`, `#antigravity`,
`#kiro-ide-cli`, `#goose`, `#lm-studio`, `#kilo-code`, `#continue`, `#qwen-code`, `#amp`, `#warp`, `#factory-droid`,
`#openai-codex-cli`, `#hermes`, `#cursor`, `#windsurf-devin-desktop`, `#vs-code-and-github-copilot`, `#gemini-cli`,
`#cline`, `#zed`, `#jetbrains-ai-assistant`. Read the section before you write: the shapes differ in ways the hosts
reject outright. Zed's key is `context_servers`, VS Code's `.vscode/mcp.json` uses `servers` while its `.mcp.json` uses
`mcpServers`, opencode's and Kilo Code's is `mcp`, Amp's is `amp.mcpServers`, Goose and Continue use YAML, and Hermes
needs an absolute path to `uvx`. Merge into the existing file rather than overwriting it, because they usually have
other servers configured already. The three most common hosts, in full:

**[Claude Desktop](https://github.com/singleflo/evolution-api-mcp/blob/main/README.md#claude-desktop)** —
`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS,
`%APPDATA%\Claude\claude_desktop_config.json` on Windows:

```json
{
  "mcpServers": {
    "evolution-api-mcp": {
      "command": "uvx",
      "args": ["evolution-api-mcp"],
      "env": {
        "EVOLUTION_API_URL": "https://evolution.example.com",
        "EVOLUTION_INSTANCE_TOKEN": "the-token-they-gave-you"
      }
    }
  }
}
```

**[Claude Code](https://github.com/singleflo/evolution-api-mcp/blob/main/README.md#claude-code)** — one command writes
it. Keep `--transport stdio` between the last `--env` and the server name: `--env` keeps reading `KEY=value` pairs, so a
name directly after it is read as another pair and rejected.

```bash
claude mcp add --env EVOLUTION_API_URL=https://evolution.example.com \
  --env EVOLUTION_INSTANCE_TOKEN=the-token-they-gave-you \
  --transport stdio evolution-api-mcp -- uvx evolution-api-mcp
```

**[opencode](https://github.com/singleflo/evolution-api-mcp/blob/main/README.md#opencode)** —
`~/.config/opencode/opencode.json`. The key is `mcp`, `command` is one array, the block is `environment`, and `timeout`
defaults to 5000 ms, which a send with its typing delay can overrun:

```json
{
  "mcp": {
    "evolution-api-mcp": {
      "type": "local",
      "enabled": true,
      "command": ["uvx", "evolution-api-mcp"],
      "timeout": 120000,
      "environment": {
        "EVOLUTION_API_URL": "https://evolution.example.com",
        "EVOLUTION_INSTANCE_TOKEN": "the-token-they-gave-you"
      }
    }
  }
}
```

## 4. Decide what the server may do

Use the person's answer to question 3, and add variables to the `env` block only when needed:

- **Read only** — add `"EVOLUTION_MCP_ALLOW": "none"`. Nothing the server does can send or change anything, and the
  send tools are not even listed.
- **The default** — add nothing. The toolsets are `messaging`, `chats` and `contacts`; sends are allowed, one tool per
  kind of message, and the tools that cannot be undone and the six on the default deny list stay off.
- **More** — add `"EVOLUTION_MCP_TOOLSETS"` with the comma list of toolsets they named, for example
  `"messaging,chats,contacts,groups"`, or `"all"`. Toolset names: `instance`, `messaging`, `chats`, `contacts`,
  `groups`, `labels`, `profile`, `status`, `catalog`, `templates`, `settings`, `events`, `integrations`. Tell them a
  bigger set means more tools in the host's tool list.

**Never set `EVOLUTION_MCP_ALLOW_IRREVERSIBLE`, and do not set `EVOLUTION_MCP_DENY`.** The first is the only thing that
lets logout, delete-for-everyone, leaving a group and deleting templates, chatbots or credentials run, none of which can
be undone, and it is not yours to turn on. The second **replaces** the default deny list (`post_status`,
`remove_group_participants`, `set_webhook`, `set_event_channel`, `set_proxy`, `set_chatwoot_config`) instead of
extending it. If the person asks for either, tell them where it is documented and let them write it themselves:
[What the agent may do](https://github.com/singleflo/evolution-api-mcp/blob/main/README.md#what-the-agent-may-do).

Leave the pacing variables (`EVOLUTION_MCP_DEFAULT_DELAY_MS`, `EVOLUTION_MCP_MAX_WRITES_PER_MINUTE`) at their defaults.

## 5. Restart the host, then verify

The host reads its configuration at startup and does not reload it. Restart it fully: for Claude Desktop, quit the
application rather than closing the window. Then call `get_instance_status`. It returns the instance name, its
integration (`WHATSAPP-BAILEYS`, `WHATSAPP-BUSINESS` or `EVOLUTION`), the connection state, the linked phone number, the
enabled toolsets and how many tools are available. Tell the person the instance name and the state you got back: it
proves you reached *their* instance. It sends and changes nothing.

If the state is not `open`, the WhatsApp session is not connected and sends and live lookups will fail. On a
`WHATSAPP-BAILEYS` instance the `instance` toolset's `start_pairing` links it again; add that toolset if they want you
to. On other integrations, reconnecting is done in Evolution.

If it fails, the message names the cause. The common ones:

- "Missing Evolution credentials": a variable is not in the `env` block, or the host did not pass it on.
- "does not answer like an Evolution API server": the URL is wrong, or points at something that is not Evolution.
- "This is the Evolution server's global AUTHENTICATION_API_KEY": use the instance's own token instead.
- "Evolution did not accept this token as an instance token": the token is wrong, or that Evolution server runs with
  `DATABASE_SAVE_DATA_INSTANCE=false`, in which case it cannot resolve instance tokens.
- "This token belongs to N instances": give each instance its own token.
- "Evolution API <version> is not supported; this server needs Evolution API 2.x": the server runs another major version.

If a call times out, raise the host's timeout; the key differs per host, and the README section for each gives it:

- opencode: `timeout`, milliseconds, default 5000. Raise to 120000.
- Claude Code: `timeout`, milliseconds, per server.
- Codex CLI: `startup_timeout_sec` and `tool_timeout_sec`, **seconds**.
- Hermes: `timeout` and `connect_timeout`, **seconds**.
- Kilo Code: `timeout`, milliseconds, default 10000. Raise to 120000.
- omp: `timeout`, milliseconds, default 30000.
- Goose: `timeout`, **seconds**.
- GitHub Copilot CLI: `--timeout` on `copilot mcp add`, milliseconds.
- Gemini CLI: `timeout`, milliseconds, already 600000 by default.
- Cline: no key in the file; it is a setting in the MCP servers panel.

## 6. Tell the person what you did

Close with four things, plainly:

1. **Which file you wrote**, by full path.
2. **Which variables you set** — name them and their values, token redacted.
3. **That the instance token sits in plain text in that file.** Anyone who can read the file, or any backup or repository
   it reaches, has the token. Say so explicitly if the file is inside a git repository. The token can be rotated in
   Evolution.
4. **What the server may do** — read only if you set `EVOLUTION_MCP_ALLOW=none`; otherwise the toolsets you enabled,
   where sends are real messages to real people that this server cannot recall, and where WhatsApp's rules still apply
   (message only people who agreed to hear from them, honour opt-outs, send nothing unsolicited or in bulk).
