<!-- mcp-name: io.github.singleflo/evolution-api-mcp -->
# Evolution API Assistant

An MCP server that operates one [Evolution API](https://github.com/EvolutionAPI/evolution-api) v2 instance, which is
one WhatsApp number: it reads chats and message history, sends messages and media, manages groups, contacts, labels,
templates and profile, and configures the instance, its webhooks and its chatbots. 97 tools, one per operation,
grouped in 13 toolsets you switch on as needed. It talks only to the Evolution server you point it at,
with that instance's own token.

Evolution API Assistant is an independent project by Persevida SL, not affiliated with or endorsed by Meta, WhatsApp
or the Evolution API project.

## Two ways to use it

* **Local (stdio)** — `uvx evolution-api-mcp` runs on your machine and talks to any Evolution API 2.x server you can
  reach. It supports all three Evolution integrations (`WHATSAPP-BAILEYS`, `WHATSAPP-BUSINESS`, `EVOLUTION`) and can
  send files from your disk.
* **Hosted** — `https://evolution-mcp.singleflo.com/mcp` is served over the internet for Claude.ai, ChatGPT and Codex:
  nothing to install, you sign in once with your Evolution URL and instance token. The public hosted server accepts
  instances whose integration is `WHATSAPP-BUSINESS` (the official WhatsApp Business Platform) only, and never offers
  the tools that take secrets or cannot be undone. See "Hosted server" below.

## Quickstart

**Install with your AI agent.** If you already have an AI coding assistant — Claude Code, Claude Desktop, Cursor,
opencode, any of the hosts below — paste this link into your agent and ask it to set up Evolution API Assistant:
`https://raw.githubusercontent.com/singleflo/evolution-api-mcp/main/docs/INSTALL-WITH-YOUR-AGENT.md`
That page is written for the agent rather than for you: it asks you for the Evolution URL and the instance token,
installs `uv`, writes the configuration file its own host reads, and verifies the connection. The manual route is
below.

### 1. Install

Run the server directly:

```bash
uvx evolution-api-mcp
```

Or install it into your environment:

```bash
uv pip install evolution-api-mcp
```

Installing from source for development remains possible:

```bash
uv pip install git+https://github.com/singleflo/evolution-api-mcp
```

The server needs Python 3.10 or later. `uvx` fetches a suitable interpreter by itself.

### 2. Configure environment variables

Two variables are all a working setup needs:

* `EVOLUTION_API_URL`: the base URL of your Evolution server, for example `https://evolution.example.com`. A trailing
  slash is ignored.
* `EVOLUTION_INSTANCE_TOKEN`: the token of **one instance**. In Evolution Manager open the instance and copy its token,
  or use the token returned when the instance was created. The server's global `AUTHENTICATION_API_KEY` is refused,
  in both the local and the hosted server, because it controls every instance on that Evolution server.

The instance name and its integration are discovered from the token; you never type them. A missing variable is
reported when a tool is called, not at startup, so the server still starts and lists its tools while you fix the
configuration. Evolution resolves instance tokens only while it runs with `DATABASE_SAVE_DATA_INSTANCE=true` (its
default); with `false` it answers 401 and the server says so.

Everything else is optional. A blank value counts as unset for every variable. A value the server cannot accept stops
it at startup with exit code 2 and a message on stderr naming the variable and what it accepts.

| Variable | Default | Meaning |
|---|---|---|
| `EVOLUTION_MCP_TOOLSETS` | `core` | Comma list of toolsets or presets (`core`, `all`) to enable. The `--toolsets` option wins over it. |
| `EVOLUTION_MCP_ALLOW` | `*` | `*` allows every tool the deny list does not refuse; `none` makes the server read-only; otherwise a comma list of tools that change data. |
| `EVOLUTION_MCP_DENY` | the default deny list | Comma list of tools that are refused. Unset, it holds the 6 tools listed under "What the agent may do"; a value you set replaces that list entirely. |
| `EVOLUTION_MCP_ALLOW_IRREVERSIBLE` | off | `yes`, `true` or `1` (any case) lets the tools that cannot be undone run. `no`, `false` and `0` keep them off. |
| `EVOLUTION_MCP_DEFAULT_DELAY_MS` | `1200` | Milliseconds of "typing…" the recipient sees before a message is sent, `0` to `20000`. A tool's `delay_ms` overrides it. |
| `EVOLUTION_MCP_MAX_WRITES_PER_MINUTE` | `30` | Changes allowed per minute on this server; `0` turns the limit off. |
| `EVOLUTION_MCP_FILE_ROOTS` | your `Desktop`, `Documents`, `Downloads`, `Pictures`, `Movies` and `Music` folders that exist, plus the download folder | Folders `send_local_file` may read, separated like `PATH` (`:` on macOS and Linux, `;` on Windows). Each must be an absolute path to an existing directory. |
| `EVOLUTION_MCP_DOWNLOAD_DIR` | `~/Downloads/evolution-api-mcp` | Where `download_message_media` saves files. Must be an absolute path. |
| `EVOLUTION_MCP_DATA_DIR` | the platform's data directory | Where the hosted server keeps its data. The local server does not write there. |

Command-line options of `evolution-api-mcp`:

| Option | Effect |
|---|---|
| `--toolsets LIST` | Same as `EVOLUTION_MCP_TOOLSETS`; wins over it. |
| `--read-only` | Refuse every tool that changes data (same as `EVOLUTION_MCP_ALLOW=none`). |
| `--list-tools` | Print every tool as JSON (name, title, toolset, kind, integrations, local_only) and exit. Needs no credentials. |
| `--list-toolsets` | Print the toolsets, their tool counts, the presets and the default as JSON and exit. Needs no credentials. |
| `--version` | Print the version and exit. |

Nothing but the JSON-RPC stream reaches stdout while the server runs; diagnostics go to stderr.

## Toolsets

Every tool belongs to one toolset. A connection enables some toolsets and sees only their tools. The default is
`core`, which is `messaging`, `chats` and `contacts`; `all` enables all 13. `get_instance_status` belongs to
`instance` but is always available, whatever the toolsets.

| Toolset | Tools | Covers |
|---|---|---|
| `instance` | 5 | Connection status, QR pairing, restart, logout and online presence of the instance. |
| `messaging` | 15 | Send text, media, voice notes, locations, contact cards, polls, list and button messages; react, edit and delete sent messages. |
| `chats` | 11 | List chats, read and search message history, delivery status, read/unread and archive state, received media. |
| `contacts` | 6 | Find contacts, check numbers on WhatsApp, profiles and profile pictures, block and unblock. |
| `groups` | 17 | Group details, participants, invite links, creation, settings and membership. |
| `labels` | 3 | WhatsApp Business app labels on chats. |
| `profile` | 7 | The instance's own profile name, about text, picture and privacy settings. |
| `status` | 1 | Post status updates. |
| `catalog` | 2 | WhatsApp Business app product catalog and collections. |
| `templates` | 5 | WhatsApp Business Platform message templates: list, create, edit, delete and send. |
| `settings` | 4 | Instance behaviour settings and proxy. |
| `events` | 4 | Webhook and event-stream (WebSocket, RabbitMQ, NATS, SQS, Kafka, Pusher) configuration. |
| `integrations` | 17 | Evolution chatbots (Evolution Bot, Typebot, OpenAI, Dify, Flowise, n8n, EvoAI), their sessions, and Chatwoot. |

Local server: `EVOLUTION_MCP_TOOLSETS=messaging,chats,groups` or `--toolsets messaging,chats,groups`; presets and
toolset names mix (`core,groups`). An unknown name stops the server at startup. Hosted server: the toolsets are ticked on
the consent page and can be changed by signing in again.

Enabled toolsets are not the only thing that decides what a connection sees. `tools/list` shows exactly the tools a
call would not be refused for: toolset enabled, supported by the instance's integration, allowed by the policy, not
denied, and, for the irreversible ones, granted. How many that is depends on the connection:

| Connection | Tools visible |
|---|---|
| Local, WhatsApp Web (Baileys) instance, default toolsets and settings | 32 |
| Local, Baileys instance, all toolsets | 81 |
| Local, Baileys instance, all toolsets, `EVOLUTION_MCP_ALLOW_IRREVERSIBLE=yes` | 86 |
| Local, Baileys instance, all toolsets, read-only | 32 |
| Hosted, WhatsApp Business Platform instance, standard policy, all toolsets | 38 |
| Hosted, WhatsApp Business Platform instance, read policy, all toolsets | 21 |

The complete list, one table per toolset with each tool's kind and integrations, is [docs/TOOLS.md](docs/TOOLS.md),
generated from the code.

## What each integration supports

An Evolution instance uses one integration, and Evolution itself does not offer some operations on some of them.
`get_instance_status` reports which one an instance runs, and a tool called on an integration that lacks it is refused
with a message naming the integration it needs. The table counts the tools each toolset offers per integration.

| Toolset | Tools | Baileys | Business | Evolution |
|---|---|---|---|---|
| `instance` | 5 | 5 | 1 | 1 |
| `messaging` | 15 | 15 | 9 | 5 |
| `chats` | 11 | 11 | 8 | 6 |
| `contacts` | 6 | 6 | 1 | 1 |
| `groups` | 17 | 17 | 0 | 0 |
| `labels` | 3 | 3 | 0 | 0 |
| `profile` | 7 | 7 | 0 | 0 |
| `status` | 1 | 1 | 0 | 0 |
| `catalog` | 2 | 2 | 0 | 0 |
| `templates` | 5 | 0 | 5 | 0 |
| `settings` | 4 | 4 | 4 | 4 |
| `events` | 4 | 4 | 4 | 4 |
| `integrations` | 17 | 17 | 17 | 17 |
| **All** | **97** | **92** | **49** | **38** |

Baileys is `WHATSAPP-BAILEYS`, a WhatsApp Web session linked to a phone. Business is `WHATSAPP-BUSINESS`, the WhatsApp
Business Platform (Cloud API): it addresses people by phone number only, has no groups, pairing or read receipts, and
delivers free-form messages only within 24 hours of the person's last message, after which only approved templates
(`send_template_message`) reach them. Evolution is `EVOLUTION`, an Evolution channel. The per-tool detail is in
[docs/TOOLS.md](docs/TOOLS.md) and in the `evolution://guide` resource.

## Tools and resources

Tools follow a few rules that make them predictable for a model and reviewable for a person:

* One tool per operation. Reads and writes are separate tools, and there is no generic "call any endpoint" tool.
* Every tool declares its kind: `read`, `write` (private or ephemeral changes such as archiving a chat, labels,
  presence), `destructive` (sends something to other people, or replaces or removes something) or `irreversible`
  (cannot be undone from this server: `logout_instance`, `delete_message_for_everyone`, `leave_group`,
  `delete_template`, `delete_chatbot`, `delete_openai_credential`). The MCP hints (`readOnlyHint`, `destructiveHint`)
  are derived from the kind, so a host's approval dialog reflects the real risk. Every tool has `openWorldHint` set,
  because each one acts on an external server and, through it, on conversations with other people.
* Tools return compact JSON, never raw Evolution rows. Tokens, passwords and secrets are never returned; a
  configuration read shows only whether a secret is set. Timestamps are ISO-8601 UTC. A result longer than 10,000
  characters is cut with a notice that names the remedy.
* A failed call ends in one of three ways: done; refused or failed with "Nothing was changed." or "Nothing was sent.";
  or `UNCERTAIN`, when Evolution did not confirm a change that may already have been applied. The last one carries the
  state read back afterwards and says not to repeat the call before checking it.

Resource: `evolution://guide`, a Markdown guide with the chat-id forms, the integration matrix, where history comes
from, pacing and the safety model.

Chats are addressed by an international phone number with the country code first (`393331234567` or
`+39 333 123 4567`) or by the `chat_id` another tool returned: `<digits>@s.whatsapp.net` for people, `<digits>@g.us`
for groups, `<id>@lid` for people whose number WhatsApp hides.

Message history comes from Evolution's database. It holds messages only when the Evolution server runs with
`DATABASE_SAVE_DATA_NEW_MESSAGE=true` (older history synced at pairing needs `DATABASE_SAVE_DATA_HISTORIC=true`), and on
WhatsApp Business Platform instances it keeps received media only when its S3/MinIO storage is enabled. Evolution has
no text search, so `search_messages` scans the newest 2,000 messages itself; narrow it with a chat or a time range to
look further back.

## What the agent may do

Every tool call passes one gate before it reaches Evolution, and `tools/list` hides what the gate would refuse. The
gate reads settings that a human owns, in the host configuration file, and that the model cannot change:

* **Toolsets.** A tool outside the enabled toolsets is refused.
* **Integration.** A tool the instance's integration does not support is refused.
* **Read-only.** `EVOLUTION_MCP_ALLOW=none` or `--read-only` refuses every tool that is not a `read`.
* **Irreversible.** The 6 irreversible tools run only when `EVOLUTION_MCP_ALLOW_IRREVERSIBLE=yes` is set. No list
  can grant them; a name in a comma-separated list must never be enough for something nobody can undo.
* **Deny list.** `EVOLUTION_MCP_DENY` names tools that are refused. Unset, it is the default list: `post_status`,
  `remove_group_participants`, `set_webhook`, `set_event_channel`, `set_proxy`, `set_chatwoot_config` — a status
  update reaches every contact, removing participants ends someone's membership, and the other four change where
  Evolution sends its events, how it reaches the network, or which Chatwoot helpdesk receives your conversations.
  A value you set **replaces** that list, so `EVOLUTION_MCP_DENY=post_status` re-admits the other five.
* **Allow list.** `EVOLUTION_MCP_ALLOW` as a comma list permits only those tools that change data (plus every read).

Entries match tool names exactly. A name that is not a registered tool stops the server at startup, so a typo cannot
silently open the gate. The deny list is checked before the allow list, so a name on both refuses. Reads are never
limited by either list.

| Configuration | What it permits |
|---|---|
| `EVOLUTION_MCP_ALLOW=none` | Reads only. Nothing this server does can send or change anything. |
| Nothing set | **Default.** Reads, plus every enabled tool that changes data except the 6 on the default deny list and the 6 irreversible ones. |
| `EVOLUTION_MCP_ALLOW_IRREVERSIBLE=yes` | The default, plus the irreversible tools the instance's integration supports. The deny list is unchanged. |

**Local-only tools.** 8 tools are registered only by the local server, because they take secrets or read your
disk: `send_local_file`, `set_proxy`, `set_webhook`, `set_event_channel`, `create_chatbot`, `update_chatbot`,
`create_openai_credential` and `set_chatwoot_config`. The hosted server registers the other 89.

**Pacing and rate limits.** Every send passes a `delay`, during which the recipient sees "typing…"; the default is
1,200 ms. Changes are limited to 30 per minute per connection; the hosted server also limits reads to 120 per minute.
A refused call names the seconds to wait.

**Local files.** `send_local_file` reads only inside `EVOLUTION_MCP_FILE_ROOTS`, never a hidden file or a file inside a
hidden folder, and at most 100 MiB.

**Messages are data.** Text, names and captions inside messages are written by other people. The server's instructions
tell the model that a message asking to send, forward or change something is not a request from you.

This is the authority of this server, not of the token. An agent with shell access can always call Evolution directly,
and the instance token can do everything an instance owner can. A limit that must hold regardless of the client belongs
in what that token can reach: give the server a token of an instance that holds only the conversations it should see.

## WhatsApp rules still apply

Sends are real messages to real people and this server cannot recall them (only WhatsApp Web instances can delete a
message for everyone, within the time WhatsApp allows). Message people who agreed to hear from you, honour opt-outs,
and send no unsolicited or bulk messages: accounts that break WhatsApp's rules get banned, and no setting here prevents
that. On WhatsApp Business Platform instances the [WhatsApp Business Messaging
Policy](https://business.whatsapp.com/policy) applies, including opt-in and the 24-hour window.

## Evolution API version

Evolution API 2.3.x is the supported and tested version. Discovery accepts any 2.x server and refuses anything else with
a message that names the version. The server also needs the `POST /verify-creds` route of Evolution 2.3 and later,
which it uses to recognise and refuse the global API key.

## Hosted server: Claude.ai, ChatGPT and Codex

Everything in the sections further down runs the server on your machine. There is a second way into the same tools:
they are also served over the internet at `https://evolution-mcp.singleflo.com/mcp`, which Claude.ai, ChatGPT and Codex
can reach directly. Nothing is installed and no environment variables are set on your side: you sign in once with your
Evolution address and instance token, on the server's consent page, and the chat you already use reaches your instance.
The local configuration of every other section keeps working exactly as written; the two ways differ only in where the
server runs.

The consent page asks for your Evolution URL, the instance token, one policy and the toolsets to enable. The policy
**standard** lets the agent read and act (send messages, manage chats and configuration); **read** lets it look and
nothing else. `messaging`, `chats` and `contacts` are ticked by default. You change either choice later by signing in
again.

The hosted server differs from the local one in what it will connect to and offer:

* It accepts only instances whose integration is `WHATSAPP-BUSINESS`. For a `WHATSAPP-BAILEYS` or `EVOLUTION`
  instance, run the local server. A private deployment can widen this with `EVOLUTION_REMOTE_ALLOWED_INTEGRATIONS`;
  see [docs/REMOTE.md](docs/REMOTE.md).
* The Evolution server must be reachable from the internet at a public address; private and loopback addresses are
  refused.
* The irreversible tools and the 8 local-only tools are never available, and the default deny list always applies.

**What the hosted server stores**: your Evolution URL, the instance name, its integration, your policy and toolsets; the
instance token, encrypted at rest; OAuth tokens, kept only as hashes; and files a tool produces, which live there only
as links that expire after fifteen minutes. It stores no message contents and no contacts. The details are at
https://evolution-mcp.singleflo.com/privacy, with https://evolution-mcp.singleflo.com/terms and
https://evolution-mcp.singleflo.com/support on the same domain.

Per-host instructions: Claude.ai and ChatGPT are hosted-only connections and have their own sections below. Claude Code
and Codex keep their local configuration and gained a hosted one-liner each.

If you would rather run the hosted part yourself, the developer guide at [docs/REMOTE.md](docs/REMOTE.md) covers the
whole path: local run, tunnel, deployment.

## Host Configuration Examples

Every example below carries only what matters: the two variables. Toolsets default to `core` and the gate keeps its
defaults unless you add `EVOLUTION_MCP_TOOLSETS`, `EVOLUTION_MCP_ALLOW` or the other variables of the table above; the
last section shows a read-only setup. Note the quotes: environment values are strings.

The calls that wait on WhatsApp are the ones a host's tool timeout can cut short: a send waits for its `delay` (1,200 ms
by default), `send_chat_presence` holds the call for up to 20 seconds, `download_message_media` and `view_message_image`
allow Evolution up to 300 seconds, and `send_local_file` up to 600 seconds for a large upload. Where a host lets you set
a timeout, the examples raise it; a JSON file allows no comments, so this note lives here.

Each snippet below was checked against that host's own documentation, cited on the `Source:` line under it. Where a
host has a one-line add command, it is given as well, because it writes the same entry without a hand-edited file.

### Claude Desktop

Claude Desktop ships for macOS and Windows only, and keeps its servers in `claude_desktop_config.json`. Reach it from
**Settings → Developer → Edit Config**, or edit it where it lives:

* **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
* **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "evolution-api-mcp": {
      "command": "uvx",
      "args": [
        "evolution-api-mcp"
      ],
      "env": {
        "EVOLUTION_API_URL": "https://evolution.example.com",
        "EVOLUTION_INSTANCE_TOKEN": "your-instance-token-here"
      }
    }
  }
}
```

Quit Claude Desktop completely and reopen it: it reads the file at startup and does not reload it. Server logs land in
`~/Library/Logs/Claude/` on macOS and `%APPDATA%\Claude\logs` on Windows, one file per server, and stdio servers write
everything they say to stderr there.

Source: https://modelcontextprotocol.io/docs/develop/connect-local-servers

### Claude Code

One line adds the server. The `--` separates Claude Code's own options from the command that starts the server, and
everything after it is passed through untouched:

```bash
claude mcp add --env EVOLUTION_API_URL=https://evolution.example.com \
  --env EVOLUTION_INSTANCE_TOKEN=your-instance-token-here \
  --transport stdio evolution-api-mcp -- uvx evolution-api-mcp
```

Note the order. `--env` takes `KEY=value` pairs and keeps reading them, so the server name must not follow it directly:
put at least one other option, here `--transport stdio`, in between, or the CLI reads `evolution-api-mcp` as another
pair and rejects it.

`--scope` decides where the entry lands: `local` (the default: this project, you only), `project` (`.mcp.json` at the
repo root, committed and shared), or `user` (every project). To write it by hand, the same entry goes under `mcpServers`
in `.mcp.json` or in `~/.claude.json`:

```json
{
  "mcpServers": {
    "evolution-api-mcp": {
      "command": "uvx",
      "args": [
        "evolution-api-mcp"
      ],
      "env": {
        "EVOLUTION_API_URL": "https://evolution.example.com",
        "EVOLUTION_INSTANCE_TOKEN": "your-instance-token-here"
      },
      "timeout": 120000
    }
  }
}
```

The per-server `timeout` is a wall-clock limit per tool call, in milliseconds, and overrides the `MCP_TOOL_TIMEOUT`
environment variable for this server alone; `MCP_TIMEOUT`, also milliseconds, bounds server startup instead. Reconnect
the server from the `/mcp` panel after editing, or restart Claude Code.

The one-liner above runs the server on your machine. Claude Code can also use the hosted server, with no install and no
environment variables:

```bash
claude mcp add --transport http evolution-api-mcp https://evolution-mcp.singleflo.com/mcp
```

The first tool call starts the sign-in and lands on the consent page, where the policy and toolsets are chosen. On the
hosted route the gate is decided there, not by local `EVOLUTION_MCP_*` variables, and the irreversible tools are not
offered at all.

Source: https://code.claude.com/docs/en/mcp

### OpenAI Codex CLI

Codex keeps MCP servers in **TOML**, in `~/.codex/config.toml`, or in a project's `.codex/config.toml` once you have
trusted that project. The table is spelled with an underscore: `mcp_servers`, not `mcp.servers`. The ChatGPT desktop app,
the Codex CLI and the IDE extension all read this one file, so configuring it once covers the three.

```bash
codex mcp add evolution-api-mcp \
  --env EVOLUTION_API_URL=https://evolution.example.com \
  --env EVOLUTION_INSTANCE_TOKEN=your-instance-token-here \
  -- uvx evolution-api-mcp
```

The same entry written out:

```toml
[mcp_servers.evolution-api-mcp]
command = "uvx"
args = ["evolution-api-mcp"]
startup_timeout_sec = 30
tool_timeout_sec = 300

[mcp_servers.evolution-api-mcp.env]
EVOLUTION_API_URL = "https://evolution.example.com"
EVOLUTION_INSTANCE_TOKEN = "your-instance-token-here"
```

Both timeouts are in **seconds**: `startup_timeout_sec` defaults to 10 and `tool_timeout_sec` to 60. The second one is
raised above because a large upload or a long typing indicator can pass a minute. After editing, press **Restart** on
the server in the desktop app or the IDE extension; in the CLI, start a new session and check it with `/mcp`.

The hosted server is added by URL instead, and the sign-in is its own command:

```bash
codex mcp add evolution-api-mcp --url https://evolution-mcp.singleflo.com/mcp
codex mcp login evolution-api-mcp
```

`codex mcp login` walks the same OAuth flow and lands on the consent page, where the policy and toolsets are chosen;
`codex mcp logout evolution-api-mcp` ends the connection. As on every hosted route: reads always work, writes follow the
policy chosen at sign-in, and the irreversible tools are not available at all.

Source: https://developers.openai.com/codex/mcp

Source: https://developers.openai.com/codex/config-file/config-reference

### ChatGPT

ChatGPT reaches the hosted server through **developer mode**, available to Pro, Plus, Business, Enterprise and Education
accounts, on the web:

1. In [ChatGPT](https://chatgpt.com), open **Settings → Security and login** and turn on **Developer mode**.
2. Open [chatgpt.com/plugins](https://chatgpt.com/plugins), press the plus button and create a developer-mode app for
   the MCP URL `https://evolution-mcp.singleflo.com/mcp`.
3. ChatGPT starts the sign-in, which lands on the server's consent page: enter your Evolution URL and instance token,
   and choose the policy and the toolsets.
4. In a conversation, choose **Developer mode** from the plus menu and select the Evolution API Assistant app.

There is deliberately no local snippet here: developer mode connects to remote MCP servers over HTTPS only, so the stdio
configuration of the other sections does not apply. A public listing in ChatGPT's Plugin Directory, which removes the
developer-mode step, is planned but has not arrived yet.

What the agent may do is decided once, at sign-in: the policy and the toolsets, and never the irreversible tools,
whatever the conversation asks for.

Source: https://developers.openai.com/api/docs/guides/developer-mode

### Claude.ai (web, Desktop, mobile)

Claude connects to the hosted server as a custom connector. Open **Customize → Connectors → Add custom connector**, paste
`https://evolution-mcp.singleflo.com/mcp` as the server URL and confirm. On Team and Enterprise plans an owner adds it
once under **Organization settings → Connectors**; members then connect from **Customize → Connectors**.

The first use starts the sign-in, which lands on the consent page: your Evolution URL, the instance token, and the
policy and toolsets.

This link opens the same dialog with the name and URL already filled in; review them and confirm, nothing is added until
you do:

```text
https://claude.ai/customize/connectors?modal=add-custom-connector&connectorName=Evolution%20API%20Assistant&connectorUrl=https%3A%2F%2Fevolution-mcp.singleflo.com%2Fmcp
```

Source: https://claude.com/docs/connectors/custom/remote-mcp

Source: https://claude.com/docs/connectors/building/directory-vs-custom

### opencode

Add this to `opencode.json` or `.opencode/opencode.json` in your project, or to `~/.config/opencode/opencode.json` to
make the server available everywhere:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "evolution-api-mcp": {
      "type": "local",
      "enabled": true,
      "command": [
        "uvx",
        "evolution-api-mcp"
      ],
      "timeout": 120000,
      "environment": {
        "EVOLUTION_API_URL": "https://evolution.example.com",
        "EVOLUTION_INSTANCE_TOKEN": "your-instance-token-here"
      }
    }
  }
}
```

opencode's shape differs from the hosts above in ways it rejects outright. The key is `mcp` (not `mcpServers`), `type`
is required, `command` is a single array holding the program and its arguments (there is no separate `args`), and the
environment block is `environment` (not `env`).

Set `timeout` deliberately. It defaults to **5000 ms**, which a send with its typing delay, a first call that also runs
discovery against a slow Evolution server, or a media download can exceed. Set it to 120000.

opencode reads its config once at startup and does not hot-reload it. Quit and restart after editing. Anything you
change here, the toolsets and the allow and deny lists included, takes effect only on the next launch.

Source: https://opencode.ai/docs/mcp-servers

### Hermes

Hermes keeps its servers in **YAML**, under `mcp_servers:` in `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  evolution-api-mcp:
    command: /Users/you/.local/bin/uvx
    args:
      - evolution-api-mcp
    env:
      EVOLUTION_API_URL: https://evolution.example.com
      EVOLUTION_INSTANCE_TOKEN: your-instance-token-here
    timeout: 120
    connect_timeout: 60
    enabled: true
```

Three details this shape does not forgive. `command` is a **string** and takes only the program, with the arguments in a
separate `args` list, the opposite of opencode's single array. The environment block is `env`. And the command needs an
**absolute path**: Hermes runs as a desktop application, which does not inherit the `PATH` of your shell, so a bare
`uvx` is not found.

Both timeouts here are in **seconds**, not milliseconds: `timeout` is the tool-call limit and defaults to 300,
`connect_timeout` bounds the initial connection and defaults to 60. Reload the servers with `/reload-mcp` after editing
rather than restarting.

`hermes mcp add` can write this entry for you (its signature is `add <name> [--url URL] [--command CMD] [--auth
oauth|header] [--args ...]`) but pass `--args` **last**: it takes the remaining argv, so anything after it is swallowed
into `args`, which is how credentials end up there and the server starts with none.

Source: https://hermes-agent.nousresearch.com/docs/reference/mcp-config-reference

### Cursor

Add this to `.cursor/mcp.json` in your project, to `~/.cursor/mcp.json` to make the server available everywhere, or
configure it from **Customize** in the sidebar:

```json
{
  "mcpServers": {
    "evolution-api-mcp": {
      "command": "uvx",
      "args": [
        "evolution-api-mcp"
      ],
      "env": {
        "EVOLUTION_API_URL": "https://evolution.example.com",
        "EVOLUTION_INSTANCE_TOKEN": "your-instance-token-here"
      }
    }
  }
}
```

Cursor interpolates `${env:NAME}` inside `command`, `args`, `env`, `url` and `headers`, so `"EVOLUTION_INSTANCE_TOKEN":
"${env:EVOLUTION_INSTANCE_TOKEN}"` keeps the token out of a file you might commit. When a call fails, the reason is in
the Output panel under **MCP Logs**.

Source: https://cursor.com/docs/context/mcp

### Windsurf

Windsurf's Cascade agent reads **one global file**, `~/.codeium/windsurf/mcp_config.json`, on every platform. There is no
project-scoped equivalent, so this entry applies to every workspace you open:

```json
{
  "mcpServers": {
    "evolution-api-mcp": {
      "command": "uvx",
      "args": [
        "evolution-api-mcp"
      ],
      "env": {
        "EVOLUTION_API_URL": "https://evolution.example.com",
        "EVOLUTION_INSTANCE_TOKEN": "your-instance-token-here"
      }
    }
  }
}
```

Open it from the `MCPs` icon in the Cascade panel, or from **Settings → Cascade → MCP Servers**, then refresh the server
list. The file interpolates `${env:VAR_NAME}` and `${file:/path/to/file}` in `command`, `args` and `env`, so the token can
live outside it. Cascade caps the agent at 100 tools in total. On a Baileys instance this server shows 32 tools with the
default toolsets and 81 with `EVOLUTION_MCP_TOOLSETS=all` (see the table under "Toolsets"), so keep the default, or pick
the toolsets you need, when other servers share the budget.

Source: https://docs.windsurf.com/windsurf/cascade/mcp

### VS Code and GitHub Copilot

VS Code's root key is **`servers`**, not `mcpServers`: an entry copied from another host's documentation will not be
seen. Put it in `.vscode/mcp.json` in your workspace, to commit it with the project, or run **MCP: Open User
Configuration** from the Command Palette for the copy that follows your user profile into every workspace:

```json
{
  "servers": {
    "evolution-api-mcp": {
      "type": "stdio",
      "command": "uvx",
      "args": [
        "evolution-api-mcp"
      ],
      "env": {
        "EVOLUTION_API_URL": "https://evolution.example.com",
        "EVOLUTION_INSTANCE_TOKEN": "your-instance-token-here"
      }
    }
  }
}
```

The command line writes the same entry:

```bash
code --add-mcp "{\"name\":\"evolution-api-mcp\",\"command\":\"uvx\",\"args\":[\"evolution-api-mcp\"]}"
```

The first time VS Code starts a server it asks whether you trust it; decline and the server never runs. Use the code
lenses in `mcp.json`, or **MCP: List Servers** in the Command Palette, to start, stop and restart it and to read its
output. Avoid hardcoding the token in a committed workspace file: VS Code provides input variables for exactly this.

Source: https://code.visualstudio.com/docs/copilot/customization/mcp-servers

### Gemini CLI

Gemini CLI reads `mcpServers` from `settings.json`: `~/.gemini/settings.json` for every session, or
`.gemini/settings.json` in a project's root for that project only, which takes precedence.

```bash
gemini mcp add evolution-api-mcp uvx evolution-api-mcp \
  --env EVOLUTION_API_URL=https://evolution.example.com \
  --env EVOLUTION_INSTANCE_TOKEN=your-instance-token-here \
  --scope user
```

The same entry written out:

```json
{
  "mcpServers": {
    "evolution-api-mcp": {
      "command": "uvx",
      "args": [
        "evolution-api-mcp"
      ],
      "env": {
        "EVOLUTION_API_URL": "https://evolution.example.com",
        "EVOLUTION_INSTANCE_TOKEN": "your-instance-token-here"
      },
      "timeout": 600000
    }
  }
}
```

`timeout` is the request timeout in **milliseconds** and already defaults to 600000, ten minutes, so it needs nothing
from you here; the line is shown only because it is the key to lower if you want a faster failure. Two other habits pay
off: Gemini CLI redacts anything matching `*KEY*`, `*TOKEN*` or `*SECRET*` from the inherited environment before
spawning a server, so a variable must be named in this `env` block to arrive at all, and `"$MY_VAR"` inside it expands
from your shell. Restart the CLI after editing, then check the server with `/mcp`.

Source: https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/mcp-server.md

Source: https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/cli-reference.md

### Cline

Cline's CLI reads `~/.cline/mcp.json`. In the IDE extensions, open the **MCP Servers** icon in the Cline panel, go to the
**Configure** tab and press **Configure MCP Servers**, which opens the extension's own settings JSON. Both use the same
`mcpServers` shape:

```json
{
  "mcpServers": {
    "evolution-api-mcp": {
      "command": "uvx",
      "args": [
        "evolution-api-mcp"
      ],
      "env": {
        "EVOLUTION_API_URL": "https://evolution.example.com",
        "EVOLUTION_INSTANCE_TOKEN": "your-instance-token-here"
      },
      "disabled": false,
      "autoApprove": []
    }
  }
}
```

Leave `autoApprove` empty. It is the list of tools that run without asking, and the gate in this server is not a
substitute for reading a send call before it happens. `cline mcp` opens an interactive wizard that adds, edits, enables
and removes servers without touching the file. The request timeout is a per-server setting in the MCP settings panel
rather than a key in this file: raise it there if a large upload times out, and restart the server from the same panel.

Source: https://docs.cline.bot/mcp/mcp-overview

### Roo Code

Roo Code reads two files: a global `mcp_settings.json`, opened by the **Edit Global MCP** button at the bottom of the MCP
settings view, and a per-project `.roo/mcp.json` opened by **Edit Project MCP** next to it, which Roo creates if it does
not exist. A server name present in both takes its project definition.

```json
{
  "mcpServers": {
    "evolution-api-mcp": {
      "command": "uvx",
      "args": [
        "evolution-api-mcp"
      ],
      "env": {
        "EVOLUTION_API_URL": "https://evolution.example.com",
        "EVOLUTION_INSTANCE_TOKEN": "your-instance-token-here"
      },
      "alwaysAllow": [],
      "disabled": false,
      "timeout": 300
    }
  }
}
```

`timeout` here is in **seconds**, not milliseconds: it accepts 1 to 3600 and defaults to 60, and the same value is the
**Network Timeout** dropdown in the server's own panel. Leave `alwaysAllow` empty, for the reason given under Cline.
Press the restart button next to the server after editing.

Committing `.roo/mcp.json` shares the server with your team, so put the token in a system environment variable and
reference it as `${env:EVOLUTION_INSTANCE_TOKEN}` rather than writing it into a file that goes into version control.

Source: https://docs.roocode.com/features/mcp/using-mcp-in-roo

### Zed

Zed calls them context servers, and the key is **`context_servers`**, not `mcpServers`. Add the entry to your settings
file (Command Palette, `zed: open settings file`) or let Zed write it for you from **Settings → AI → MCP Servers → Add
Server → Add Local Server**:

```json
{
  "context_servers": {
    "evolution-api-mcp": {
      "command": "uvx",
      "args": [
        "evolution-api-mcp"
      ],
      "env": {
        "EVOLUTION_API_URL": "https://evolution.example.com",
        "EVOLUTION_INSTANCE_TOKEN": "your-instance-token-here"
      }
    }
  }
}
```

The indicator dot beside the server's name in **Settings → AI → MCP Servers** says whether it came up: green, with
"Server is active" in its tooltip, means Zed reached it. Tool approval is governed by `agent.tool_permissions.default`,
which is `"confirm"` by default; per-tool rules use the key format `mcp:evolution-api-mcp:<tool_name>`, for example
`mcp:evolution-api-mcp:send_text_message`.

Source: https://zed.dev/docs/ai/mcp

### JetBrains AI Assistant

JetBrains AI Assistant takes the configuration through a dialog rather than a file you locate yourself. Go to
**Settings | Tools | AI Assistant | Model Context Protocol (MCP)**, click **Add**, choose STDIO, and paste this as the
JSON configuration:

```json
{
  "mcpServers": {
    "evolution-api-mcp": {
      "command": "uvx",
      "args": [
        "evolution-api-mcp"
      ],
      "env": {
        "EVOLUTION_API_URL": "https://evolution.example.com",
        "EVOLUTION_INSTANCE_TOKEN": "your-instance-token-here"
      }
    }
  }
}
```

The dialog documents `command` and `args`, and adds two fields of its own beside the JSON: **Working directory**, and
**Server level**, which decides whether the server is available globally or only in the current project. Click OK, then
**Apply**: that is what actually starts the server, and the Status column reports whether it connected. If you already
have this server in Claude Desktop, **Import from Claude** carries the whole entry over instead, including its
environment block.

Source: https://www.jetbrains.com/help/ai-assistant/mcp.html

### Read-only and wider setups

To point an agent at a live instance for reading only, add `EVOLUTION_MCP_ALLOW` set to `"none"`. Here is how it looks in
Claude Desktop, with the messaging, chat, contact and group toolsets:

```json
{
  "mcpServers": {
    "evolution-api-mcp": {
      "command": "uvx",
      "args": [
        "evolution-api-mcp"
      ],
      "env": {
        "EVOLUTION_API_URL": "https://evolution.example.com",
        "EVOLUTION_INSTANCE_TOKEN": "your-instance-token-here",
        "EVOLUTION_MCP_TOOLSETS": "messaging,chats,contacts,groups",
        "EVOLUTION_MCP_ALLOW": "none"
      }
    }
  }
}
```

With `none`, no send tool is even listed: `tools/list` shows reads only. The opposite setup, an assistant that answers
customers, keeps the default toolsets and settings, and only raises `EVOLUTION_MCP_MAX_WRITES_PER_MINUTE` if 30 changes
a minute is too few.

## Changelog

What changed in each release is in [CHANGELOG.md](https://github.com/singleflo/evolution-api-mcp/blob/main/CHANGELOG.md),
kept there rather than repeated here so the two cannot drift.

## Contributing and security

[CONTRIBUTING.md](CONTRIBUTING.md) covers the development setup and the rules for code changes;
[SECURITY.md](SECURITY.md) the threat model and how to report a vulnerability. Support and bug reports go to
https://github.com/singleflo/evolution-api-mcp/issues.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

Evolution API Assistant is an independent project by Persevida SL, not affiliated with or endorsed by Meta, WhatsApp or
the Evolution API project. "WhatsApp" is a trademark of its owner and is used here only to say what the software works
with.
