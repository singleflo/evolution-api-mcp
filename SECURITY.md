# Security Policy

## What this server can do

Evolution API Assistant is a bridge between an MCP host and one Evolution API instance. It authenticates with that
instance's own token, so it can do whatever the token allows on that instance: read every stored chat and message, send
messages as the connected WhatsApp number, manage groups and contacts, and change the instance's configuration. The
server's own gate (below) narrows this; it does not replace limiting the token.

We recommend pointing the server at an instance that holds only the conversations the assistant should reach, and
starting read-only (`EVOLUTION_MCP_ALLOW=none`) until you trust the setup.

## Threat model

### Prompt injection from inbound messages

Every message, contact name, group subject and caption an assistant reads was written by someone else, possibly to
steer the assistant into sending, forwarding or reconfiguring something. The mitigations are layered:

* The server's `instructions` state that message content is data, and that a message asking to send, forward or change
  something is not a request from the user.
* Sends never happen implicitly. Every send is its own tool; recipients and wording come from the user. Tools that send
  or expose content, replace configuration or remove people declare `destructiveHint`, so a host can ask for approval,
  and the six tools that cannot be undone need an explicit operator grant.
* The gate, not the model, decides what may run. Toolsets, the read-only mode, the allow and deny lists and the
  irreversible grant are set by a human in the host configuration, out of band; the model cannot change them, and
  a tool outside them is neither listed nor callable.
* Outputs are projections: text is length-capped, and only known fields are returned.

None of this makes a send tool safe against a host that approves every call automatically. Keep approval on for tools
that send, or run read-only.

### Token scope and the global API key

* The server accepts **an instance's own token only**. On the first call (local) or at sign-in (hosted) it checks that
  the URL answers like an Evolution 2.x server, calls `POST /verify-creds` and reads `GET /instance/fetchInstances`;
  Evolution's server-wide `AUTHENTICATION_API_KEY`, which controls every instance, is refused in both the local and
  the hosted server, as is a token that belongs to more than one instance.
* Tokens are read from the environment (local) or the consent page (hosted). They are never written to source, packages
  or configuration files by this server, never logged, and never returned by a tool.
* Configuration reads pass through a redaction step (`redact.py`): passwords, API keys, tokens and secrets in proxy,
  webhook, event-channel, chatbot, Chatwoot and settings data are replaced by `[redacted]`, and the projections show only
  whether a secret is set. A test asserts that these never appear in a result.
* Tools whose inputs carry secrets (proxy password, webhook headers, Pusher secret, chatbot API keys, Chatwoot token,
  OpenAI credentials) are **local-only**: the hosted server does not register them.
* The instance token in the host configuration file sits in plain text. Anyone who can read that file, or a backup or
  repository it reaches, has the token. Rotate it in Evolution if it leaks.

### SSRF (hosted server)

The hosted server connects to a URL a stranger types. It refuses non-public targets twice: the consent page resolves the
host and refuses it unless every address is public, and every connection is made through a guarded transport that
resolves the host, checks each address (including IPv4-mapped IPv6 addresses) and connects to the vetted address, so a
DNS answer that changes between the check and the connection cannot reach an internal address. The verification runs in
a subprocess with a 20-second deadline. Private-network targets are accepted only when the operator of a private
deployment sets `EVOLUTION_REMOTE_ALLOW_PRIVATE_TARGETS=1`.

### Local files (local server)

`send_local_files` is the only tool that reads your disk. It reads only inside the folders in `EVOLUTION_MCP_FILE_ROOTS`
(default: your `Desktop`, `Documents`, `Downloads`, `Pictures`, `Movies` and `Music` folders that exist, plus the download
folder), resolves symlinks before checking containment, never sends a hidden file or a file inside a hidden folder, and
refuses empty files, files over 100 MiB, more than 10 files and more than 300 MiB in one call.
`download_message_media` and `export_chat` write only into `EVOLUTION_MCP_DOWNLOAD_DIR` (exports under its `exports/`
folder, replacing an earlier export of the same chat and period), with sanitised file names.

### Hosted-server restrictions

* The public hosted server connects only to instances whose integration is `WHATSAPP-BUSINESS`.
* It never offers the irreversible tools, the eight local-only tools, or the tools on the default deny list; the policy
  is `read` or `standard`, chosen by the user on the consent page.
* It rate limits tool calls per connection: 30 changes and 120 reads per minute.
* The OAuth 2.1 authorization server supports dynamic client registration, PKCE with S256, refresh-token rotation and
  revocation. Authorization codes, access tokens and refresh tokens are stored only as SHA-256 hashes; the instance token
  and client registrations are encrypted at rest with a key supplied by the operator. A connection stops working when
  its tenant is removed, and tenants unused for 90 days are deleted.
* Files a tool produces are served from an unguessable link that expires after 15 minutes and is deleted afterwards.

### Public client registration

Registration (`POST /register`) is open by design: the MCP authorization specification requires it, and Claude.ai and
ChatGPT register themselves this way. Registering gives a stranger nothing on its own, because every connection still
needs a person to open the consent page, which names the client, shows its redirect address and asks for that person's
own instance token. What is validated and limited:

* The request body is at most 16 KiB. `redirect_uris` holds 1 to 5 entries of at most 2048 characters, with no
  fragment. Allowed schemes are `https`, `http` only for `localhost`, `127.0.0.1` and `[::1]`, and custom application
  schemes such as `cursor://` or `vscode://`; `javascript`, `data`, `file`, `vbscript`, `about`, `blob`, `ftp`, `ws`,
  `wss` and `http` to any other host are refused. `client_name` is at most 200 characters and `client_uri`, `logo_uri`,
  `tos_uri` and `policy_uri` at most 2048 each. Refusals are RFC 7591 errors and store nothing.
* At most 10 registrations per minute per client address (the address the single proxy in front of the server
  appended to `X-Forwarded-For`) and 200 per hour for the whole server; beyond that the answer is 429 with
  `Retry-After`.
* At most 5,000 stored registrations (503 beyond), and registrations that no pending authorisation, code or token uses
  are deleted after 30 days by the hourly sweep.
* The consent page warns when the client is not one of the assistants this server knows, and shortens very long
  client names.
* A redirect must match a registered one exactly at `/authorize`, and PKCE with S256 is required, so a registered
  client cannot receive another client's authorization code.

### Sending limits

Every send passes a delay and a per-connection rate limit (30 changes per minute by default). These reduce accidental
floods; they do not make bulk or unsolicited messaging acceptable. WhatsApp bans numbers that break its rules, and this
server cannot undo a ban.

## Authority of this server

The gate limits what this server will do on the model's behalf. An agent with shell access can always call Evolution
directly with the token it finds in its own configuration. A limit that must hold regardless of the client belongs in
what the token can reach: a dedicated instance, and Evolution's own network and access controls.

## Reporting a security issue

Please do not open a public issue for a vulnerability. Report it privately through GitHub's security advisories:
<https://github.com/singleflo/evolution-api-mcp/security/advisories/new>. Include the version, the steps to reproduce
and what an attacker gains.

Non-security bugs go to <https://github.com/singleflo/evolution-api-mcp/issues>.
