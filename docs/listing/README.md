# Listing dossier: Claude connectors directory and the OpenAI Plugins Directory

Every field the two store submissions ask for, in labelled sections ready to
paste. Copy is plain on purpose — no superlatives, nothing the tools do not
do — because review teams check claims against behaviour. The hard limits
are enforced twice: once by each store, once by
`tests/test_listing_copy.py`, which parses this file and fails on a size
violation, a missing section, or an annotation table that has drifted from
the live server. Change a field here and the suite tells you if it no longer
fits.

Submission pages: Claude
(https://claude.com/docs/connectors/building/submission) and OpenAI
(https://developers.openai.com/plugins/deploy/submission).

What is submitted is the **hosted** server at
`https://evolution-mcp.singleflo.com/mcp`. It accepts only instances whose
integration is `WHATSAPP-BUSINESS` (the official WhatsApp Business Platform
channel), so the surface both portals scan is the one a hosted
`WHATSAPP-BUSINESS` connection with the `standard` policy and every toolset
ticked exposes. The local server (`uvx evolution-api-mcp`) is not part of the
store submissions.

Values that could not be re-verified when this dossier was written are marked
"confirm on submission day": the portals change their forms without notice.

## Identity

### Name

The connector name in Claude's directory (100 characters max).

```text
Evolution API Assistant
```

### Plugin name

The plugin name in OpenAI's portal (64 characters max).

```text
evolution-api-assistant
```

### Display name

OpenAI display name (30 characters max). Already carried by
`plugins/evolution-api-mcp/plugin.json` as `displayName`.

```text
Evolution API Assistant
```

### Tagline

Claude only, 55 characters max.

```text
Read, search and answer your Evolution API chats
```

### Short description

OpenAI only, 30 characters max. Already carried by
`plugins/evolution-api-mcp/plugin.json` as `shortDescription`.

```text
Evolution API messaging tools
```

### Long description

One text for both stores (Claude caps at 2,000 characters, OpenAI at 4,000 —
the shorter limit wins).

```text
Evolution API Assistant connects your AI host to one instance of your own Evolution API server, the open-source gateway you host yourself. It lists chats, reads and searches stored messages, shows delivery status and received media, and sends messages to the people you talk to. What it may do is fixed when you connect.

This hosted connector accepts instances that use the WhatsApp Business Platform (the official Cloud API channel). You sign in once with your Evolution server address and that one instance's own token; the server-wide Evolution API key is refused, and creating, deleting or listing other instances is not offered.

38 tools in 8 toolsets: chats (list, read, search, delivery status, received media), messaging (text, media, voice notes, locations, contact cards, list and button messages, reactions), contacts, templates (list, create, edit and send approved message templates), settings, events and integrations (read webhook, event and chatbot configuration; pause or close chatbot sessions; start a Typebot flow), and instance status. You tick the toolsets to enable and choose read only or standard, which adds sending and changing.

Sends are real and this connector cannot recall them; wording and recipients come from you, and the WhatsApp Business Messaging Policy applies: opt-in, the 24-hour window for free-form replies, templates beyond it. Logging out, deleting messages for everyone, leaving groups and deleting templates, bots or credentials are never available here, and neither are tools that take credentials or repoint webhooks, proxies and event streams; those live in the local server.

Open source under the MIT license.

Evolution API Assistant is an independent project by Persevida SL, not affiliated with or endorsed by Meta, WhatsApp or the Evolution API project.
```

## Categories

Claude asks for one to five categories, picked in the submission portal —
the picker's labels govern. Intended, not yet measured against the picker
(confirm on submission day; if a label is missing, choose the nearest real
one and record it here):

1. Communication
2. Productivity

OpenAI takes one category, already carried by
`plugins/evolution-api-mcp/plugin.json`:

```text
Productivity
```

## Starter prompts

Three prompts, each 128 characters or fewer, no @mentions — the highest-value
workflows, specific enough to show when to reach for the connector. None
names a person, so pressing one on any instance finds something to act on.

```text
Which chats have unread messages, and what do they ask?
```

```text
Search my messages for the word invoice and summarize what you find.
```

```text
Which message templates are approved, and in which languages?
```

## URLs

### Documentation URL

```text
https://github.com/singleflo/evolution-api-mcp
```

### Support URL

```text
https://github.com/singleflo/evolution-api-mcp/issues
```

### Privacy URL

```text
https://evolution-mcp.singleflo.com/privacy
```

### Terms URL

```text
https://evolution-mcp.singleflo.com/terms
```

## Country availability

```text
worldwide
```

## Release notes

Initial submission.

```text
This is the initial submission of the Evolution API Assistant plugin.

Evolution API Assistant connects ChatGPT and Codex to the user's own Evolution API server and operates one WhatsApp Business Platform instance of it. 38 tools list chats, read and search stored messages, report delivery status, show and save received media, send messages and approved templates, and read or adjust instance settings and chatbots.

Every change passes a policy chosen at connection time (read only or standard) and the toolsets the user ticks. Irreversible actions and tools that take credentials are never available on the hosted server. Authentication is OAuth 2.1 with dynamic client registration and PKCE; the hosted server stores each user's Evolution connection details (the instance token encrypted) and session tokens only, described in the privacy policy.

Reviewers: use the test account under the test cases below, with the standard policy and every toolset ticked.
```

## Tool annotations

The portals read `readOnlyHint`, `destructiveHint` and `openWorldHint` per
tool. OpenAI's plugin guidelines (retrieved 2026-09-29) say annotation
justifications are no longer required; earlier portal versions asked for one
per annotation, so the reasoning stays in the `Why` column and the
`openWorldHint` sentence below — confirm on submission day whether the portal
asks for it. Claude's portal checks `readOnlyHint`/`destructiveHint` and the
title against the wire. The rows are the 38 tools a hosted
`WHATSAPP-BUSINESS` connection with policy `standard` and every toolset sees,
listed by toolset then name; the hints are derived from each tool's kind in
the registry (`registry.annotations_for`), never set by hand. The test in
`tests/test_listing_copy.py` lists the tools over the wire for exactly that
connection and fails when the table drifts. To print the live values:

```bash
uv run python - <<'EOF'
from evolution_api_mcp import context, policy, registry, tools, toolsets
tools.load_all()
conn = context.Connection(
    mode="hosted", policy="standard", toolsets=frozenset(toolsets.TOOLSET_ORDER), allow=None,
    deny=policy.DEFAULT_DENY, deny_is_default=True, irreversible_granted=False,
    identity=context.InstanceIdentity("inst", registry.BUSINESS), subject="t_dossier",
    default_delay_ms=1200, max_writes_per_minute=30, max_reads_per_minute=120)
for spec in registry.specs():
    if policy.visible(spec, conn):
        a = registry.annotations_for(spec)
        print(spec.name, a.read_only_hint, a.destructive_hint, a.open_world_hint)
EOF
```

`openWorldHint` is `yes` throughout: every tool acts on the user's own
Evolution API server and, through it, on WhatsApp conversations with other
people — an open-ended external system rather than a bounded workspace owned
by the publisher.

| Tool | readOnlyHint | destructiveHint | openWorldHint | Why |
|---|---|---|---|---|
| `get_instance_status` | yes | no | yes | Reports the connection state, integration, enabled toolsets and tool count; a pure read that works even when the WhatsApp session is closed. |
| `react_to_message` | no | yes | yes | Puts an emoji on a message that the other people in the chat see, and a second reaction replaces the first, so the earlier state is lost. |
| `send_button_message` | no | yes | yes | Delivers a button message to a person immediately; a send this server cannot recall. |
| `send_contact_card` | no | yes | yes | Delivers contact cards, which share phone numbers with the recipient, immediately and irrevocably from this server. |
| `send_list_message` | no | yes | yes | Delivers a selectable list message to a person immediately; a send this server cannot recall. |
| `send_location` | no | yes | yes | Delivers a location pin to a person or group immediately; a send this server cannot recall. |
| `send_media_message` | no | yes | yes | Delivers an image, video, audio file or document fetched from a public URL, immediately and irrevocably from this server. |
| `send_text_message` | no | yes | yes | Delivers a text message to a person or group immediately; a send this server cannot recall. |
| `send_voice_note` | no | yes | yes | Delivers an audio file as a voice message immediately; a send this server cannot recall. |
| `download_message_media` | no | no | yes | Saves a message's attachment as a file the caller can fetch for 15 minutes; not read-only because it creates a downloadable artifact, not destructive because nothing on WhatsApp or in Evolution changes. |
| `get_chat` | yes | no | yes | Describes one chat: name, unread count, labels and the latest message; nothing is altered. |
| `get_message` | yes | no | yes | Returns one stored message in full; a pure read. |
| `get_message_status` | yes | no | yes | Reports how far a message got (sent, delivered, read, played); a pure read. |
| `list_chats` | yes | no | yes | Lists chats with unread counts and a preview of the latest message; a pure read. |
| `read_messages` | yes | no | yes | Returns one chat's stored history page by page; nothing is sent or changed. |
| `search_messages` | yes | no | yes | Scans stored messages for a phrase and returns the matches; a pure read. |
| `view_message_image` | yes | no | yes | Returns a received or sent picture as an inline image; a pure read. |
| `find_contacts` | yes | no | yes | Looks up stored contacts by name or phone number; a pure read. |
| `create_template` | no | no | yes | Submits a new message template to Meta for review; it stays a private draft until approved and nothing is sent to any person, and no existing template is overwritten. |
| `edit_template` | no | yes | yes | Replaces the components or category of an existing template, which goes back through Meta's review, so the previous definition is lost. |
| `list_templates` | yes | no | yes | Lists message templates with their approval status; a pure read. |
| `send_template_message` | no | yes | yes | Delivers an approved template to a phone number immediately, a real message that this server cannot recall. |
| `get_instance_settings` | yes | no | yes | Reads the instance's behaviour settings; a pure read that never returns secrets. |
| `get_proxy` | yes | no | yes | Reads the proxy settings with the password reported only as set or not; a pure read. |
| `update_instance_settings` | no | yes | yes | Overwrites the stored behaviour settings (call rejection, auto-read, always online and others) with the merged values, so the previous configuration is lost. |
| `get_event_channel` | yes | no | yes | Reads one event stream's configuration with secrets reported only as set or not; a pure read. |
| `get_webhook` | yes | no | yes | Reads the webhook configuration with header values never returned; a pure read. |
| `change_chatbot_session` | no | yes | yes | Resumes, pauses, closes or deletes a chat's chatbot sessions, which changes whether a bot answers that person; deleting removes the session records. |
| `get_chatbot` | yes | no | yes | Returns one chatbot's configuration with secrets reported only as set or not; a pure read. |
| `get_chatbot_settings` | yes | no | yes | Returns the session defaults shared by a provider's chatbots; a pure read. |
| `get_chatwoot_config` | yes | no | yes | Returns the Chatwoot integration settings with the token reported only as set or not; a pure read. |
| `list_chatbot_sessions` | yes | no | yes | Lists the conversations a chatbot is handling and their status; a pure read. |
| `list_chatbots` | yes | no | yes | Lists the chatbots configured on the instance without keys or passwords; a pure read. |
| `list_openai_credentials` | yes | no | yes | Lists stored OpenAI credentials by name and id, never the keys; a pure read. |
| `list_openai_models` | yes | no | yes | Lists the model ids a stored OpenAI credential can use; a pure read. |
| `set_chatbot_ignored_chat` | no | yes | yes | Adds a chat to or removes it from the list a provider's chatbots skip, which changes whether bots answer that person. |
| `start_typebot_session` | no | yes | yes | Starts a Typebot flow in a chat, which sends that person the flow's first messages; a send this server cannot recall. |
| `update_chatbot_settings` | no | yes | yes | Overwrites the session defaults shared by a provider's chatbots, which decide how the bots answer people. |

## Test cases

Runnable by a reviewer with the test account below, no internal context
needed. Every case goes through the hosted server, not a local build.

**Fixture values are replaced by observed ones.** The results below describe
what each tool returns; counts, names and message texts of the review
instance are written in after the live suite (`tests/test_live.py`, variables
`EVOLUTION_TEST_*`) has run against it and the five positive cases have been
executed end to end through the hosted server, as the reference dossier did.
Until then no figure appears here, because a figure that disagrees with what
the reviewer sees costs more than none.

The two kinds of case mean different things in the OpenAI portal, and
conflating them is how a submission gets marked down. A **positive** case is
a prompt the plugin should answer. A **negative** case is a prompt the plugin
should **not be invoked for at all** — a near miss the model may think is
relevant. It is not a refusal: refusals happen inside a positive case, when
the plugin is correctly invoked and correctly declines.

### Positive test case 1: unread chats

- Prompt: Which chats have unread messages, and what do they ask?
- Expected tool: `list_chats` with `only_unread` set, then `read_messages` on
  each chat it returns.
- Expected result: a short list of chats — name, unread count, preview of the
  latest message — followed by what the unread messages ask, or an explicit
  "no chats have unread messages" when the list is empty.
- Fixture data: at least one chat whose unread count is above zero, holding a
  question from the fixture customer.

### Positive test case 2: recent history with one customer

- Prompt: Show me the last ten messages in my chat with the test customer.
- Expected tool: `read_messages` with `limit` 10 on the customer's chat,
  after `find_contacts` or `list_chats` when the assistant has only the name.
- Expected result: ten messages, newest first, each with sender, timestamp,
  type and text (text over 1,500 characters is cut and flagged), or fewer when
  the chat holds fewer; both sides of the conversation appear.
- Fixture data: the fixture customer, a test contact with at least ten stored
  messages in both directions.

### Positive test case 3: search by word

- Prompt: Search my messages for the word invoice and summarize what you find.
- Expected tool: `search_messages` with `query` invoice.
- Expected result: the messages whose text or caption contains "invoice",
  newest first, each with its chat, sender and timestamp, then a summary. The
  result states how many messages were scanned; when the scan of the newest
  2,000 stored messages did not cover everything, it says so.
- Fixture data: at least one stored message containing the word invoice.

### Positive test case 4: approved templates

- Prompt: Which message templates are approved, and in which languages?
- Expected tool: `list_templates` with `status` APPROVED.
- Expected result: the approved templates with name, language and category,
  grouped by language when the assistant summarises them; an explicit "no
  approved templates" when there are none.
- Fixture data: at least one approved message template on the WhatsApp
  Business account.

### Positive test case 5: a reply inside the 24-hour window

- Prompt: Send the test customer the message "Thanks, we received your order."
- Expected tool: `send_text_message` on the customer's chat (resolved with
  `find_contacts` or `list_chats` when only the name is given), then
  optionally `get_message_status`.
- Expected result: a confirmation carrying the message id, the chat id, a
  status and a timestamp; `get_message_status` then reports at least
  SERVER_ACK. The text arrives exactly as written. Run this case only with the
  `standard` policy: `read` refuses it by design.
- Fixture data: the fixture customer wrote to the review number within the
  last 24 hours before the case is run. Outside that window Meta refuses a
  free-form message (code 131047) and the tool says so and points to
  `send_template_message`; that is expected behaviour, not a failure.

### Negative test case 1: a draft nobody asked to send

- Prompt: Draft a polite message asking a customer to confirm their delivery
  address. Do not send it.
- Expected tool: none. The prompt is about wording for a WhatsApp message,
  which is what makes it a near miss, but the user asked for text, not for a
  delivery.
- Expected result: the model writes the draft in the conversation and the
  plugin is never invoked; nothing is sent.
- Fixture data: none.
- Why not: sending is a real, unrecallable action and the user explicitly
  declined it. Invoking the plugin would only put a customer's chat into a
  conversation about drafting.

### Negative test case 2: installing WhatsApp

- Prompt: How do I install WhatsApp on a new phone and move my chats over?
- Expected tool: none. It names WhatsApp and chats, which the plugin handles,
  but it asks how to use the app, not about the connected instance.
- Expected result: the model answers from general knowledge and the plugin is
  never invoked.
- Fixture data: none.
- Why not: nothing in the Evolution instance answers a question about
  installing or migrating the phone app.

### Negative test case 3: the Evolution API project itself

- Prompt: What is the Evolution API project, and how do I host it myself?
- Expected tool: none. The prompt names Evolution API, which is what makes it
  a near miss, but it asks about the software, not about the user's instance.
- Expected result: the model answers from general knowledge and the plugin is
  never invoked.
- Fixture data: none.
- Why not: the plugin operates one existing instance. Explaining or installing
  the server is outside it, and invoking it would spend a call to learn that.

## Reviewer test account (TEMPLATE — the owner fills this before submitting)

Fill every placeholder from a dedicated review setup: an Evolution API server
reachable from the public internet (the hosted server refuses private
addresses) with one `WHATSAPP-BUSINESS` instance on a Meta test or sandbox
WhatsApp Business account. Never a production number, never a real customer's
data. Use the instance's own token, not the server-wide key, and rotate the
token in Evolution when the review closes.

**The instance token is deliberately not written down here.** It goes into
the submission portal's credentials field and nowhere else;
`tests/test_listing_copy.py` fails if a UUID or a 40-hex string appears in
this file (Evolution instance tokens are UUIDs).

| Field | Value |
|---|---|
| Evolution API URL | `<review Evolution server base URL, no trailing slash>` |
| MCP endpoint | `https://evolution-mcp.singleflo.com/mcp` |
| Instance token | `<the review instance's own token, entered through the portal field only>` |
| Integration | `WHATSAPP-BUSINESS` |
| Policy | `standard` |
| Toolsets | every box ticked |

**There is no password, and no Evolution login page to visit.** The reviewer
signs in once on the server's own consent page, which their client opens for
them, and types two values: the "Evolution server address" and the "Instance
token". Both the instance name and the integration are discovered from the
token. No
MFA, no SMS, no email confirmation, no private network: the token
authenticates on its own.

At that same consent page the reviewer chooses under "What the assistant may
do". **Pick `standard`** ("Read and act: send messages, manage chats and
configuration") — positive case 5 sends, and `read` refuses it by design — and
under "Which toolsets to enable" **tick every box** (three are ticked when the
page opens), so the scan sees all 38 tools. Irreversible actions and tools
that take credentials are not offered under either choice.

What the account can see: `<one paragraph — the review number, the fixture
customer and the direction of their messages, the chat with unread messages,
the messages containing the word invoice, and the approved templates>`.

Behaviour to expect rather than report as a fault:

- A free-form message to someone who last wrote more than 24 hours ago is
  **refused** with Meta's code 131047 and a pointer to `send_template_message`.
- Received media is available only when the Evolution server has its S3/MinIO
  storage enabled; `view_message_image` and `download_message_media` say so
  when it is not.
- Changes are limited to 30 per minute per connection, reads to 120.
- Pasting Evolution's server-wide API key at the consent page is refused with
  a message naming it.
- Tools for other integrations (for example group management, which needs a
  WhatsApp Web session) do not appear for a `WHATSAPP-BUSINESS` instance.

## Demo video

OpenAI's portal asks for a walkthrough video by URL (confirm on submission
day which tab carries the field). **This is a user action: the recording does
not exist yet.** The file goes under `docs/demo/` in the public repository,
which `pyproject.toml` excludes from the sdist and the wheel, and its public
URL is what the portal takes. Script: sign in through the consent page with
the review credentials, then run the five positive test cases in order.

## Icon

Rendered by `uv run python scripts/make_icon.py` (Pillow, dev dependency
group only): a 512×512 flat PNG with the letters "EA" — no WhatsApp or
Evolution artwork, nothing to inflate the wheel. The script writes
`docs/listing/icon.png` and the two copies
`plugins/evolution-api-mcp/assets/icon.png` and `logo.png`, which
`plugins/evolution-api-mcp/plugin.json` references.

- Claude: the Listing step of the submission portal takes the icon upload —
  https://claude.com/docs/connectors/building/submission
- OpenAI: the Info tab's logo field asks for production-ready brand assets —
  https://developers.openai.com/plugins/deploy/submission

Screenshots are not required: the connector has no user interface beyond the
conversation itself. Claude asks for carousel screenshots only for MCP Apps,
which this connector is not.
