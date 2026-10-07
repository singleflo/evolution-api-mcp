<!-- mcp-name: io.github.singleflo/evolution-api-mcp -->
# Evolution API Assistant

An MCP server that operates one [Evolution API](https://github.com/EvolutionAPI/evolution-api) v2 instance, which is
one WhatsApp number: it reads chats and message history, sends messages and media, manages groups, contacts, labels,
templates and profile, and configures the instance, its webhooks and its chatbots. 100 tools, one per operation,
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
  instances whose integration is `WHATSAPP-BUSINESS` (the official WhatsApp Business Platform) or `WHATSAPP-BAILEYS`
  (a WhatsApp Web session), and never offers the tools that take secrets or cannot be undone. See "Hosted server" below.

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
| `EVOLUTION_MCP_FILE_ROOTS` | your `Desktop`, `Documents`, `Downloads`, `Pictures`, `Movies` and `Music` folders that exist, plus the download folder | Folders `send_local_files` may read, separated like `PATH` (`:` on macOS and Linux, `;` on Windows). Each must be an absolute path to an existing directory. |
| `EVOLUTION_MCP_DOWNLOAD_DIR` | `~/Downloads/evolution-api-mcp` | Where `download_message_media` saves files and `export_chat` its `exports/` folders. Must be an absolute path. |
| `EVOLUTION_MCP_TIMEZONE` | the computer's zone (`TZ`, then `/etc/localtime`), else `UTC` | IANA time zone (for example `Europe/Rome`) in which tools show times and in which a time given without a zone is read. `get_instance_status` reports it. |
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
| `messaging` | 16 | Send text, media, voice notes, locations, contact cards, polls, list and button messages; react, edit and delete sent messages. |
| `chats` | 13 | List chats, show the newest messages across chats, read and search message history, export chats with their attachments, delivery status, read/unread and archive state, received media. |
| `contacts` | 6 | Find chats by name or number, check numbers on WhatsApp, profiles and profile pictures, block and unblock. |
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
| Local, WhatsApp Web (Baileys) instance, default toolsets and settings | 35 |
| Local, Baileys instance, all toolsets | 84 |
| Local, Baileys instance, all toolsets, `EVOLUTION_MCP_ALLOW_IRREVERSIBLE=yes` | 89 |
| Local, Baileys instance, all toolsets, read-only | 33 |
| Hosted, WhatsApp Business Platform instance, standard policy, all toolsets | 41 |
| Hosted, WhatsApp Business Platform instance, read policy, all toolsets | 22 |

The complete list, one table per toolset with each tool's kind and integrations, is [docs/TOOLS.md](docs/TOOLS.md),
generated from the code.

## What each integration supports

An Evolution instance uses one integration, and Evolution itself does not offer some operations on some of them.
`get_instance_status` reports which one an instance runs, and a tool called on an integration that lacks it is refused
with a message naming the integration it needs. The table counts the tools each toolset offers per integration.

| Toolset | Tools | Baileys | Business | Evolution |
|---|---|---|---|---|
| `instance` | 5 | 5 | 1 | 1 |
| `messaging` | 16 | 16 | 10 | 5 |
| `chats` | 13 | 13 | 10 | 7 |
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
| **All** | **100** | **95** | **52** | **39** |

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
  configuration read shows only whether a secret is set. Timestamps are ISO-8601 in the server's display time zone
  (`EVOLUTION_MCP_TIMEZONE`; UTC on the hosted server). A result longer than 30,000 characters is cut with a notice
  that names the remedy.
* A failed call ends in one of three ways: done; refused or failed with "Nothing was changed." or "Nothing was sent.";
  or `UNCERTAIN`, when Evolution did not confirm a change that may already have been applied. The last one carries the
  state read back afterwards and says not to repeat the call before checking it.

Resources: `evolution://guide`, a Markdown guide with the chat-id forms, the integration matrix, where history comes
from, pacing and the safety model; and `evolution://recipes`, tested call sequences for common tasks (what is new,
finding an attachment, replying, forwarding, exporting).

Prompts: `inbox` (what is new, grouped by chat), `reply` (draft a reply and wait for approval), `find_attachment` and
`export`. Their `chat` argument completes contact and group names from the same in-memory name directory the tools use.

Chats are addressed by an international phone number with the country code first (`393331234567` or
`+39 333 123 4567`), by the `chat_id` another tool returned (`<digits>@s.whatsapp.net` for people, `<digits>@g.us`
for groups, `<id>@lid` for people whose number WhatsApp hides), or by a contact or group name. A name shared by several
chats is refused with their chat_ids; tools that send or change something accept only an exact name.

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
disk: `send_local_files`, `set_proxy`, `set_webhook`, `set_event_channel`, `create_chatbot`, `update_chatbot`,
`create_openai_credential` and `set_chatwoot_config`. The hosted server registers the other 92.

**Pacing and rate limits.** Every send passes a `delay`, during which the recipient sees "typing…"; the default is
1,200 ms. Changes are limited to 30 per minute per connection; the hosted server also limits reads to 120 per minute.
A call that sends several messages (`send_local_files`, `forward_message`) counts each message as one change.
A refused call names the seconds to wait.

**Local files.** `send_local_files` reads only inside `EVOLUTION_MCP_FILE_ROOTS`, never a hidden file or a file inside a
hidden folder, at most 100 MiB per file, and at most 10 files and 300 MiB per call.

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

* The public deployment at `evolution-mcp.singleflo.com` accepts instances whose integration is `WHATSAPP-BUSINESS` or
  `WHATSAPP-BAILEYS`; an `EVOLUTION` channel instance needs the local server. A server started without
  `EVOLUTION_REMOTE_ALLOWED_INTEGRATIONS` accepts `WHATSAPP-BUSINESS` only, which is the default of the code and the
  setting the store listings assume; see [docs/REMOTE.md](docs/REMOTE.md). A `WHATSAPP-BAILEYS` instance is an
  unofficial WhatsApp Web client: WhatsApp's terms do not cover automating a personal account that way, and WhatsApp
  can restrict a number that is used like this. The choice is yours; the Business Platform is the supported route.
* The Evolution server must be reachable from the internet at a public address; private and loopback addresses are
  refused.
* The irreversible tools and the 8 local-only tools are never available, and the default deny list always applies.

**What the hosted server stores**: your Evolution URL, the instance name, its integration, your policy and toolsets; the
instance token, encrypted at rest; OAuth tokens, kept only as hashes; and files a tool produces, which live there only
as links that expire after fifteen minutes. It keeps message contents only inside files you ask for (downloaded media
and chat exports), behind links that expire after fifteen minutes, and contact names only in memory for ten minutes.
The details are at
https://evolution-mcp.singleflo.com/privacy, with https://evolution-mcp.singleflo.com/terms and
https://evolution-mcp.singleflo.com/support on the same domain.

Per-host instructions: Claude.ai, ChatGPT, Open WebUI and Mistral Le Chat are hosted-only connections and have their own
sections below. Claude Code and Codex keep their local configuration and gained a hosted one-liner each.

If you would rather run the hosted part yourself, the developer guide at [docs/REMOTE.md](docs/REMOTE.md) covers the
whole path: local run, tunnel, deployment.

## Host Configuration Examples

Every example below carries only what matters: the two variables. Toolsets default to `core` and the gate keeps its
defaults unless you add `EVOLUTION_MCP_TOOLSETS`, `EVOLUTION_MCP_ALLOW` or the other variables of the table above; the
last section shows a read-only setup. Note the quotes: environment values are strings.

The calls that wait on WhatsApp are the ones a host's tool timeout can cut short: a send waits for its `delay` (1,200 ms
by default), `send_chat_presence` holds the call for up to 20 seconds, `download_message_media`, `view_message_image`
and each attachment of `export_chat` allow Evolution up to 300 seconds, and `send_local_files` up to 600 seconds per
file for a large upload. Where a host lets you set a timeout, the examples raise it; a JSON file allows no comments,
so this note lives here.

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

### omp (oh-my-pi)

omp reads MCP servers from `.omp/mcp.json` in a project, or from `~/.omp/agent/mcp.json` for every project. `${NAME}` and
`${NAME:-default}` are expanded from the environment omp was started in, so exporting the two variables there keeps the
token out of any file you might commit; a literal value works as well:

```json
{
  "mcpServers": {
    "evolution-api-mcp": {
      "type": "stdio",
      "command": "uvx",
      "args": [
        "evolution-api-mcp"
      ],
      "env": {
        "EVOLUTION_API_URL": "${EVOLUTION_API_URL}",
        "EVOLUTION_INSTANCE_TOKEN": "${EVOLUTION_INSTANCE_TOKEN}"
      },
      "timeout": 120000
    }
  }
}
```

A placeholder whose variable is unset stays as literal text, and the server then starts with that text as its URL or
token, so launch omp from a shell that has both. `timeout` is in milliseconds and defaults to 30 seconds;
`OMP_MCP_TIMEOUT_MS` overrides every per-server value. omp also reads the MCP files of Claude Code, Codex, Gemini CLI,
opencode, Cursor, Windsurf and VS Code, so an entry already written for one of those needs no second copy. `/mcp list`
shows which file a server came from, `/mcp reload` rereads the files and `/mcp test evolution-api-mcp` checks the
connection.

Source: https://github.com/can1357/oh-my-pi/blob/main/docs/mcp-config.md

### GitHub Copilot CLI

`copilot mcp add` writes the entry to `~/.copilot/mcp-config.json`; everything after `--` is the command that starts the
server:

```bash
copilot mcp add evolution-api-mcp \
  --env EVOLUTION_API_URL=https://evolution.example.com \
  --env EVOLUTION_INSTANCE_TOKEN=your-instance-token-here \
  --timeout 120000 \
  -- uvx evolution-api-mcp
```

The same entry written out:

```json
{
  "mcpServers": {
    "evolution-api-mcp": {
      "type": "stdio",
      "command": "uvx",
      "args": [
        "evolution-api-mcp"
      ],
      "env": {
        "EVOLUTION_API_URL": "https://evolution.example.com",
        "EVOLUTION_INSTANCE_TOKEN": "your-instance-token-here"
      },
      "tools": [
        "*"
      ]
    }
  }
}
```

Copilot CLI passes a server only `PATH` from your environment, so both variables must sit in this `env` block. `tools`
takes `*` for every tool or a list of names to expose fewer. A project can carry its own servers in `.mcp.json` or
`.github/mcp.json`, but they load only after you trust the folder on first launch and are skipped silently in an
untrusted one; the `.vscode/mcp.json` of VS Code is not read. `/mcp` in a session lists the servers and their state.

Source: https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-mcp-servers

### Antigravity

Antigravity keeps its servers in `~/.gemini/config/mcp_config.json`, or in `.agents/mcp_config.json` in a workspace. In
the IDE, open it from **⋯** at the top of the agent side panel → **MCP Servers** → **Manage MCP Servers** → **View raw
config**; in the CLI, `/mcp` opens the MCP manager, where you can reload servers and read their connection logs:

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

Antigravity 2.0 uses the same format and lists the servers under **Settings → Customizations → Installed MCP Servers**.
The hosted server, for WhatsApp Business Platform instances, is added with the key **`serverUrl`**
(`"serverUrl": "https://evolution-mcp.singleflo.com/mcp"`): the `url` and `httpUrl` of other hosts are not supported.
Tools that are not allowed in your permission policy run in Ask mode, so Antigravity asks before each call.

Source: https://antigravity.google/docs/mcp

### Kiro (IDE, CLI)

Kiro reads `~/.kiro/settings/mcp.json` for every workspace and `.kiro/settings/mcp.json` for one workspace; when both
exist they are merged and the workspace entry wins. In the IDE, the Command Palette opens them as **Kiro: Open user MCP
config (JSON)** and **Kiro: Open workspace MCP config (JSON)**:

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

The CLI, `kiro-cli`, which replaces the Amazon Q Developer CLI, writes the same entry:

```bash
kiro-cli mcp add --name evolution-api-mcp --scope global --command uvx --args evolution-api-mcp \
  --env EVOLUTION_API_URL=https://evolution.example.com \
  --env EVOLUTION_INSTANCE_TOKEN=your-instance-token-here
```

Kiro applies a saved change at the next idle point between turns, without a restart. `autoApprove` lists the tools that
run without asking; leave it out, for the reason given under Cline.

Source: https://kiro.dev/docs/mcp/configuration

### Goose

Goose keeps extensions in **YAML**, under `extensions:` in `~/.config/goose/config.yaml`; `timeout` is in seconds:

```yaml
extensions:
  evolution-api-mcp:
    name: Evolution API Assistant
    type: stdio
    cmd: uvx
    args:
      - evolution-api-mcp
    envs:
      EVOLUTION_API_URL: https://evolution.example.com
      EVOLUTION_INSTANCE_TOKEN: your-instance-token-here
    enabled: true
    timeout: 300
```

In the desktop app, the sidebar's **Extensions** page has **Add custom extension**: type Standard IO, command
`uvx evolution-api-mcp`, and one environment variable per **Add** button. This link opens the same dialog in goose,
nothing is installed until you confirm it:

```text
goose://extension?cmd=uvx&arg=evolution-api-mcp&timeout=300&id=evolution-api-mcp&name=Evolution%20API%20Assistant&description=Operate%20one%20Evolution%20API%20WhatsApp%20instance
```

The link carries no environment, so open the extension's settings afterwards and set `EVOLUTION_API_URL` and
`EVOLUTION_INSTANCE_TOKEN`.

Source: https://goose-docs.ai/docs/getting-started/using-extensions

### LM Studio

LM Studio follows Cursor's `mcp.json` notation. Open the file from the **Program** tab in the right sidebar with
**Install → Edit mcp.json**, and add the entry under `mcpServers`:

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

This link opens LM Studio's install dialog with the entry already filled in; nothing is added until you confirm it:

```text
lmstudio://add_mcp?name=evolution-api-mcp&config=eyJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyJldm9sdXRpb24tYXBpLW1jcCJdLCJlbnYiOnsiRVZPTFVUSU9OX0FQSV9VUkwiOiJodHRwczovL2V2b2x1dGlvbi5leGFtcGxlLmNvbSIsIkVWT0xVVElPTl9JTlNUQU5DRV9UT0tFTiI6InlvdXItaW5zdGFuY2UtdG9rZW4taGVyZSJ9fQ%3D%3D
```

The link carries the two placeholder values, so edit them in `mcp.json` after installing. Its `config` parameter is the
entry as base64-encoded, URL-encoded JSON.

Source: https://lmstudio.ai/docs/app/mcp

Source: https://lmstudio.ai/docs/app/mcp/deeplink

### Kilo Code

Kilo Code keeps MCP servers inside its main config file: `~/.config/kilo/kilo.jsonc` for every project, or
`.kilo/kilo.jsonc` (or `kilo.jsonc` in the project root) for one project, which takes precedence. The shape is
opencode's, not the `mcpServers` of other hosts: the key is `mcp`, `command` is one array and the block is
`environment`:

```json
{
  "mcp": {
    "evolution-api-mcp": {
      "type": "local",
      "command": [
        "uvx",
        "evolution-api-mcp"
      ],
      "environment": {
        "EVOLUTION_API_URL": "https://evolution.example.com",
        "EVOLUTION_INSTANCE_TOKEN": "your-instance-token-here"
      },
      "enabled": true,
      "timeout": 120000
    }
  }
}
```

`timeout` is in milliseconds. Its 10-second default is too short for the first call, when `uvx` may still be downloading
the package, so the example raises it. On Windows the command is `["cmd", "/c", "uvx", "evolution-api-mcp"]`. The VS Code
extension also has **Settings → MCP → Add Server** for the same entry.

Source: https://kilo.ai/docs/automate/mcp/using-in-kilo-code

### Continue

Continue loads one YAML file per server from `.continue/mcpServers/` at the top of your workspace. Save this as
`.continue/mcpServers/evolution-api-mcp.yaml`:

```yaml
name: Evolution API Assistant
version: 0.0.1
schema: v1
mcpServers:
  - name: evolution-api-mcp
    command: uvx
    args:
      - evolution-api-mcp
    env:
      EVOLUTION_API_URL: https://evolution.example.com
      EVOLUTION_INSTANCE_TOKEN: your-instance-token-here
```

MCP tools work only in Continue's agent mode; the chat and edit modes do not call them.

Source: https://docs.continue.dev/customize/deep-dives/mcp

### Qwen Code

`qwen mcp add` writes to the user scope, `~/.qwen/settings.json`, unless you pass `--scope project`, which writes
`.qwen/settings.json` in the project:

```bash
qwen mcp add evolution-api-mcp \
  -e EVOLUTION_API_URL=https://evolution.example.com \
  -e EVOLUTION_INSTANCE_TOKEN=your-instance-token-here \
  uvx evolution-api-mcp
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
      }
    }
  }
}
```

Values in `env` may reference your shell with `$VAR` or `${VAR}`, which keeps the token out of a file you might commit.
`timeout` is in milliseconds and already defaults to 600000. Restart Qwen Code if it was running, then check the server
with `/mcp`.

Source: https://qwenlm.github.io/qwen-code-docs/en/users/features/mcp/

### Amp

Amp keeps local MCP servers in `~/.config/amp/settings.json`, under the dotted key **`amp.mcpServers`**:

```json
{
  "amp.mcpServers": {
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

The same key in a project's `.amp/settings.json` shares the server with your team, but workspace servers wait for your
approval before they run: `amp mcp approve evolution-api-mcp` grants it, and `amp mcp doctor` shows servers that are
still waiting. Servers in the global file need no approval. In configuration files, `${VAR_NAME}` is expanded from the
environment.

Source: https://ampcode.com/docs/markdown/customize/mcp

### Warp

Warp reads `~/.warp/.mcp.json` for every project and `.warp/.mcp.json` at a project's root. You can also open
**Settings → Agents → MCP servers**, press **+ Add** and paste the same JSON:

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

`args` is required for a command server, and Warp rejects an entry without it. Servers from the global Warp file start
on their own; Warp also picks up the MCP files of Claude Code and Codex, but starts those only when its auto-spawn
toggle is on.

Source: https://docs.warp.dev/agents/capabilities/mcp

### Factory Droid

`droid mcp add` takes the server name, then the command as one quoted string; `--type` defaults to `stdio`:

```bash
droid mcp add evolution-api-mcp "uvx evolution-api-mcp" \
  --env EVOLUTION_API_URL=https://evolution.example.com \
  --env EVOLUTION_INSTANCE_TOKEN=your-instance-token-here
```

The entry lands in `~/.factory/mcp.json` for every project; a project's `.factory/mcp.json` is the shared copy:

```json
{
  "mcpServers": {
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

Droid expands `${NAME}` from your shell, but only inside `env` (and `headers`), never in `command` or `args`, and there is
no default-value form. `"EVOLUTION_INSTANCE_TOKEN": "${EVOLUTION_INSTANCE_TOKEN}"` therefore keeps the token out of a
committed file. `/mcp` in a session lists the servers and their tools.

Source: https://docs.factory.com/harness/mcp

### OpenAI Codex CLI

Codex keeps MCP servers in **TOML**, in `~/.codex/config.toml`, or in a project's `.codex/config.toml` once you have
trusted that project. The table is spelled with an underscore: `mcp_servers`, not `mcp.servers`. The ChatGPT desktop app,
the Codex CLI and the IDE extension all read this one file, so configuring it once covers the three. In the desktop app,
**Settings → MCP servers → Add server** writes the same entry.

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

Source: https://learn.chatgpt.com/docs/extend/mcp

Source: https://developers.openai.com/codex/config-file/config-reference

### ChatGPT

ChatGPT reaches the hosted server as a custom MCP server, added on the web. Account and workspace policies decide who
may add one.

1. Open [ChatGPT Plugins](https://chatgpt.com/plugins) and press the plus button, then **Add custom MCP server**.
2. Enter a name and description, and under **Connection** choose the public endpoint and enter the MCP URL
   `https://evolution-mcp.singleflo.com/mcp`, including the `/mcp` path.
3. Configure authentication, review the risk warning, select **I understand and want to continue**, then **Create as a
   plugin**. ChatGPT starts the sign-in, which lands on the server's consent page: enter your Evolution URL and
   instance token, and choose the policy and the toolsets.
4. Start a new conversation, type `@` and select the Evolution API Assistant plugin.

There is deliberately no local snippet here: ChatGPT reaches a custom MCP server through a public HTTPS endpoint (or an
OpenAI Secure MCP Tunnel), so the stdio configuration of the other sections does not apply. A public listing in
ChatGPT's Plugin Directory is planned but has not arrived yet.

What the agent may do is decided once, at sign-in: the policy and the toolsets, and never the irreversible tools,
whatever the conversation asks for.

Source: https://developers.openai.com/plugins/deploy/connect-chatgpt

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

### Open WebUI and Mistral Le Chat

These two clients connect only to remote MCP servers, so they use the hosted server at
`https://evolution-mcp.singleflo.com/mcp`, which accepts WhatsApp Business Platform and Baileys instances. For an
Evolution channel instance, run the local server in one of the hosts above. The sign-in is the one described under
"Hosted server": your Evolution URL, the instance token, and the policy and toolsets, on the server's consent page.

**Open WebUI.** An administrator opens **Settings → Admin → Integrations**, presses **+ Add Connection** under
**External Tool Servers**, sets **Type** to **MCP (Streamable HTTP)**, enters `https://evolution-mcp.singleflo.com/mcp`
as the URL and picks **OAuth 2.1** as the authentication. Make sure the type is not OpenAPI: an MCP entry in an OpenAPI
connection breaks the page. Set `WEBUI_SECRET_KEY` in a Docker setup, or the stored sign-in is lost whenever the
container is recreated.

Source: https://docs.openwebui.com/features/extensibility/mcp

**Mistral Le Chat.** An administrator opens **Connectors**, presses **+ Add Connector**, switches to the **Custom MCP
Connector** tab and fills in a connector name without spaces or special characters (for example `EvolutionAPI`) and
the server URL `https://evolution-mcp.singleflo.com/mcp`. **Connect** starts the sign-in; the platform detects the
authentication method itself. On Free, Pro and Student plans the account owner is the administrator.

Source: https://docs.mistral.ai/vibe/work/connectors/mcp-connectors

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

Values in `config.yaml` may reference `${VAR}` (or `${env:VAR}`), resolved from `~/.hermes/.env` and then the process
environment, which keeps the token out of the file.

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

Cursor interpolates `${env:NAME}` inside `command`, `args`, `env`, `url` and `headers`, so `"EVOLUTION_INSTANCE_TOKEN":
"${env:EVOLUTION_INSTANCE_TOKEN}"` keeps the token out of a file you might commit. When a call fails, the reason is in
the Output panel under **MCP Logs**.

Cursor also installs from a link whose `config` parameter is the entry as base64-encoded JSON. This one carries the two
placeholder values, so edit them in `mcp.json` after installing:

```text
cursor://anysphere.cursor-deeplink/mcp/install?name=evolution-api-mcp&config=eyJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyJldm9sdXRpb24tYXBpLW1jcCJdLCJlbnYiOnsiRVZPTFVUSU9OX0FQSV9VUkwiOiJodHRwczovL2V2b2x1dGlvbi5leGFtcGxlLmNvbSIsIkVWT0xVVElPTl9JTlNUQU5DRV9UT0tFTiI6InlvdXItaW5zdGFuY2UtdG9rZW4taGVyZSJ9fQ==
```

To make one with your own values, encode the entry without line breaks:

```bash
printf '%s' '{"command":"uvx","args":["evolution-api-mcp"],"env":{"EVOLUTION_API_URL":"https://evolution.example.com","EVOLUTION_INSTANCE_TOKEN":"your-instance-token-here"}}' | base64 | tr -d '\n'
```

Source: https://cursor.com/docs/context/mcp

Source: https://cursor.com/docs/mcp/install-links

### Windsurf (Devin Desktop)

Windsurf is now Devin Desktop. Its Cascade agent reads **one global file** on every platform, and the vendor documents
its location as:

* **macOS and Linux**: `~/.config/devin/mcp_config.json` (under `$XDG_CONFIG_HOME/devin/` when that variable is set)
* **Windows**: `%APPDATA%\devin\mcp_config.json`

An install that still runs as Windsurf reads `~/.codeium/windsurf/mcp_config.json` instead. The entry applies to every
workspace you open:

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

Open it from the Cascade panel's `...` menu with the `Open MCP config file` icon, then refresh the server list. The file
interpolates `${env:VAR_NAME}` and `${file:/path/to/file}` in `command`, `args` and `env`, so the token can live outside
it. This file belongs to the Cascade agent. The newer Devin Local agent, the default for new tabs, keeps its servers with
the Devin CLI: the same user file, plus `.devin/mcp_config.json` in a project, and `devin mcp add` writes them. Cascade
caps the agent at 100 tools in total. On a Baileys instance this server shows 32 tools with the default toolsets and 81
with `EVOLUTION_MCP_TOOLSETS=all` (see the table under "Toolsets"), so keep the default, or pick the toolsets you need,
when other servers share the budget.

Source: https://docs.devin.ai/desktop/cascade/mcp

Source: https://docs.devin.ai/cli/extensibility/mcp/configuration

Source: https://code.visualstudio.com/docs/agents/reference/mcp-configuration

### VS Code and GitHub Copilot

VS Code reads MCP servers from several files, and the root key depends on the file. The portable files use
**`mcpServers`**: `.mcp.json` at the root of a workspace, to commit it with the project, or `~/.copilot/mcp-config.json`
(`$COPILOT_HOME/mcp-config.json` when that variable is set) for every workspace, the same file GitHub Copilot CLI reads.
Give the entry `"type": "stdio"`:

```json
{
  "mcpServers": {
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

`.vscode/mcp.json` in a workspace, and the file that **MCP: Open User Configuration** opens in your user profile, are
still read but VS Code marks them deprecated. Their root key is **`servers`**, not `mcpServers`, so an entry copied
between the two formats is not seen:

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

The command line writes an entry to your user profile:

```bash
code --add-mcp "{\"name\":\"evolution-api-mcp\",\"command\":\"uvx\",\"args\":[\"evolution-api-mcp\"]}"
```

The first time VS Code starts a server it asks whether you trust it; decline and the server never runs. Use the code
lenses in `mcp.json`, or **MCP: List Servers** in the Command Palette, to start, stop and restart it and to read its
output. Avoid hardcoding the token in a committed workspace file: VS Code provides input variables for exactly this.

Source: https://code.visualstudio.com/docs/agent-customization/mcp-servers

### Gemini CLI

Since 2026-06-18 Gemini CLI serves only Gemini Code Assist Standard and Enterprise users and paid API-key users; other
users move to Antigravity (above). It reads `mcpServers` from `settings.json`: `~/.gemini/settings.json` for every
session, or `.gemini/settings.json` in a project's root for that project only, which takes precedence.

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

Source: https://developers.googleblog.com/an-important-update-transitioning-gemini-cli-to-antigravity-cli/

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

For the hosted server, whose address you give as a URL, the entry needs `"type": "streamableHttp"`: Cline treats an entry
without a `type` as the legacy `sse` transport.

Source: https://docs.cline.bot/mcp/mcp-overview

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
