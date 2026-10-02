# Submission Guide: OpenAI Plugin Directory

Step-by-step instructions for submitting Evolution API Assistant to the universal OpenAI Plugin Directory (serving both ChatGPT and Codex). All copy and values referenced below map directly to the dossier in `docs/listing/README.md`.

The submission is the **hosted** server at `https://evolution-mcp.singleflo.com/mcp`. It accepts only instances whose integration is `WHATSAPP-BUSINESS`, the official WhatsApp Business Platform channel. That scope is deliberate: OpenAI's plugin guidelines (https://developers.openai.com/plugins/plugin-guidelines, retrieved 2026-09-29) say plugins that primarily function as unofficial connectors to third-party services cannot be approved and forbid integrating with third-party APIs without authorization under that party's terms, and `WHATSAPP-BAILEYS` instances are an unofficial WhatsApp Web client. Those instances are served only by the local server, which is not part of this submission.

Portal steps follow the portal as documented on 2026-09-29 and the tab order recorded for the reference project. Every step marked **confirm on submission day** could not be re-verified against the live portal when this guide was written.

## Prerequisites

Before submitting to the Plugin Directory, confirm you have:

1. **Verified Developer Identity**: Completed individual or business verification under organization settings at `https://platform.openai.com/settings/organization/general`. The organization is verified as a **Business**, under the name **Persevida SL**, which is therefore the developer name the directory will display. The same string is what the hosted server prints on its own legal pages (`EVOLUTION_REMOTE_PUBLISHER`, default `Persevida SL`), so portal, privacy notice and terms name one publisher and not three.
   One further prerequisite is decided at the organization level and blocks the submission outright rather than failing review: the project must **not** be on EU data residency, because an EU-residency project cannot submit a plugin carrying an MCP server. Use a global-residency project.
2. **Apps Management Write Role**: Your user role in the OpenAI Platform must have **Apps Management** set to **Write** under `https://platform.openai.com/settings/organization/people/roles` (Organization Owners have this by default).
3. **Prepared Materials**: Have the dossier ready (`docs/listing/README.md`), along with logo assets, starter prompts, test cases and the demo video (dossier section `Demo video`).
4. **The origin is final**: the MCP server's origin (scheme, host and port) can never change between versions of a published plugin, so `https://evolution-mcp.singleflo.com` must be the address you intend to keep.

## Portal Path

Navigate to the plugin portal:
1. Open [https://platform.openai.com/plugins](https://platform.openai.com/plugins).
2. Click **Create plugin**.
3. Select **With MCP** (for remote MCP-only or MCP with skills).

The live submission documentation (https://developers.openai.com/plugins/deploy/submission, retrieved 2026-09-29) also describes uploading a plugin package (a ZIP with a `plugin.json`, and the remote server declared in `mcp.json`). If the portal asks for a package instead of the form below, **confirm on submission day** what it expects: the repository's `plugins/evolution-api-mcp/mcp.json` starts the local stdio server (`uvx evolution-api-mcp`), whereas the store listing is the hosted Streamable HTTP endpoint.

## Domain Verification

Plugins with MCP must verify ownership of the host domain (`evolution-mcp.singleflo.com`):

1. Obtain the verification challenge token from the portal when prompted.
2. In your Coolify environment configuration for `evolution-mcp.singleflo.com`, set:
   ```bash
   EVOLUTION_REMOTE_OPENAI_CHALLENGE=<token>
   ```
3. Redeploy the application.
4. Confirm verification by testing the endpoint:
   ```bash
   curl https://evolution-mcp.singleflo.com/.well-known/openai-apps-challenge
   ```
   The endpoint must return exactly `<token>` in plain text, no JSON and no wrapper (this route is covered by `tests/test_remote_app.py`).
5. Click **Verify Domain** in the OpenAI portal.

## Portal Fields (In Portal Order)

### 1. Info Tab

Fill in public listing details from `docs/listing/README.md`:
- **Plugin name**: `evolution-api-assistant` (from dossier section `Identity` -> `Plugin name`, 64 characters max). OpenAI's guidelines forbid appending "MCP", "MCP Server" or "Plugin" to a product name; this name and the display name carry none.
- **Display name**: `Evolution API Assistant` (from dossier section `Identity` -> `Display name`, 30 characters max).
- **Short description**: `Evolution API messaging tools` (from dossier section `Identity` -> `Short description`, 30 characters max).
- **Long description**: Copy exact text from dossier section `Identity` -> `Long description` (4,000 characters max).
- **Developer Identity**: Select your verified developer/business identity.
- **Logo**: Upload production-ready brand assets (from dossier section `Icon`: `docs/listing/icon.png` or `plugins/evolution-api-mcp/assets/logo.png`).
- **Category**: `Productivity` (from dossier section `Categories`; **confirm on submission day** against the portal's list).
- **Website URL**: `https://github.com/singleflo/evolution-api-mcp` (from dossier section `URLs` -> `Documentation URL`).
- **Support URL**: `https://github.com/singleflo/evolution-api-mcp/issues` (from dossier section `URLs` -> `Support URL`).
- **Privacy Policy URL**: `https://evolution-mcp.singleflo.com/privacy` (from dossier section `URLs` -> `Privacy URL`).
- **Terms URL**: `https://evolution-mcp.singleflo.com/terms` (from dossier section `URLs` -> `Terms URL`).

### 2. MCP Tab

- **MCP Server URL Type**: Universal
- **MCP Server URL**: `https://evolution-mcp.singleflo.com/mcp`
- **Authentication**: OAuth 2.0 with PKCE (`oauth_dcr`).
- Authorise the scan with the reviewer connection: policy `standard`, every toolset ticked, an instance whose integration is `WHATSAPP-BUSINESS`. The server lists tools per connection, and only that connection shows all of them.
- Click **Scan Tools**.
  - The scan snapshots the server's tool metadata.
  - Snapshot contents: 38 tools (`get_instance_status`, `react_to_message`, `send_button_message`, `send_contact_card`, `send_list_message`, `send_location`, `send_media_message`, `send_text_message`, `send_voice_note`, `download_message_media`, `get_chat`, `get_message`, `get_message_status`, `list_chats`, `read_messages`, `search_messages`, `view_message_image`, `find_contacts`, `create_template`, `edit_template`, `list_templates`, `send_template_message`, `get_instance_settings`, `get_proxy`, `update_instance_settings`, `get_event_channel`, `get_webhook`, `change_chatbot_session`, `get_chatbot`, `get_chatbot_settings`, `get_chatwoot_config`, `list_chatbot_sessions`, `list_chatbots`, `list_openai_credentials`, `list_openai_models`, `set_chatbot_ignored_chat`, `start_typebot_session`, `update_chatbot_settings`), along with their `title`, `description`, `inputSchema`, `readOnlyHint`, `destructiveHint`, and `openWorldHint` (refer to dossier section `Tool annotations`).
  - Every annotation is an explicit boolean: OpenAI's guidelines require `readOnlyHint`, `destructiveHint` and `openWorldHint` to be set true or false, and treat sending messages that cannot be undone as destructive.
- The guidelines retrieved on 2026-09-29 state that annotation justifications are no longer required; earlier portal versions asked for one written justification per annotation. **Confirm on submission day** whether the portal asks. If it does, the dossier's `Tool annotations` table carries the `readOnlyHint` and `destructiveHint` reasoning in its `Why` column, row by row; the `openWorldHint` justification is stated once above the table, because it is the same sentence for all 38 — every tool acts on the user's own Evolution API server and, through it, on WhatsApp conversations with other people.
- The tool list is not frozen at approval: OpenAI scans the hosted server daily, and a new tool passes automated checks before it becomes available, so a tool added later does not need a new plugin version but is reviewed.

### 3. Prompts Tab

Add 3 starter prompts from dossier section `Starter prompts` (each under 128
chars). ChatGPT prepends the plugin mention itself when it displays them, so
none of the three carries an `@`:

1. `Which chats have unread messages, and what do they ask?`
2. `Search my messages for the word invoice and summarize what you find.`
3. `Which message templates are approved, and in which languages?`

None of the three names a person: a prompt that names "Ana" fails on contact
when a reviewer finds no Ana, and the plugin correctly answers that the
recipient does not exist. These act on whatever chats the connected instance
actually has.

### 4. Testing Tab

The tab has three parts: one free-text **Test credentials** box, **exactly 5**
positive cases with four fields each — Scenario, User prompt, Tool triggered,
Expected output — and **exactly 3** negative cases with two fields each,
Description and User prompt.

**A negative case here is not a refusal.** The portal means a prompt the
plugin should NOT be invoked for at all — a near miss the model may think is
relevant. A refusal is the opposite: the plugin correctly invoked, correctly
declining. Putting refusals in these three boxes answers a question nobody
asked, and the cases below are genuine near misses instead. Refusals this
server is built around (the 24-hour window, the policy, the rate limit) are
described in the credentials box, as behaviour to expect rather than report as
a fault.

The copy is in the dossier's `Test cases` section, which is the source of
truth — `tests/test_submit_guides.py` fails when a prompt here and a prompt
there disagree. Counts and names from the review instance are written into
the dossier after the live suite has run; paste the dossier's final wording,
not this summary.

#### Test credentials (the free-text box)

There is no password and no Evolution login page, so the placeholder's shape
does not fit. Paste this instead, with the placeholders filled:

```text
Sign-in URL: none to visit — your client opens the consent page at
https://evolution-mcp.singleflo.com/consent when you first use the plugin.
Evolution API URL: <review Evolution server base URL, no trailing slash>
Instance token: <the review instance's own token>
Integration: WHATSAPP-BUSINESS (discovered from the token)
Password: none — this server takes an instance token only, never a password.

Sign-in steps:
1. Start any of the test cases below. The client opens the consent page.
2. Type the Evolution API URL into "Evolution server address" and the
   instance token into "Instance token".
3. Under "What the assistant may do" choose the STANDARD option ("Read and
   act"). Test case 5 sends a message, and the read-only choice refuses it by
   design.
4. Under "Which toolsets to enable" tick every box (three are ticked when the
   page opens), so the scan and the cases see all 38 tools.
5. Press Connect. There is no MFA, no SMS, no email confirmation and no
   private network: the token authenticates on its own.

Behaviour to expect rather than report as a fault:
- A free-form message to a person who last wrote more than 24 hours ago is
  REFUSED with Meta's code 131047 and a pointer to send_template_message.
  Test case 5 uses the fixture customer, who wrote within the last 24 hours.
- Received media is available only when the Evolution server has S3/MinIO
  storage enabled; the media tools say so when it is not.
- Changes are limited to 30 per minute per connection, reads to 120.
- The server-wide Evolution API key is refused at the consent page.
```

#### Positive test cases

1. **Unread chats** — prompt `Which chats have unread messages, and what do they ask?` → `list_chats` with `only_unread`, then `read_messages` on each chat returned → a short list with name, unread count and preview, then what the unread messages ask; an explicit statement when none are unread.
2. **Recent history with one customer** — prompt `Show me the last ten messages in my chat with the test customer.` → `read_messages` with `limit` 10 (after `find_contacts` or `list_chats` to resolve the chat) → ten messages newest first with sender, timestamp, type and text, both directions.
3. **Search by word** — prompt `Search my messages for the word invoice and summarize what you find.` → `search_messages` with `query` invoice → the matching messages with chat, sender and timestamp and a summary, plus how many messages were scanned.
4. **Approved templates** — prompt `Which message templates are approved, and in which languages?` → `list_templates` with `status` APPROVED → the approved templates with name, language and category.
5. **A reply inside the 24-hour window** — prompt `Send the test customer the message "Thanks, we received your order."` → `send_text_message` on the customer's chat, optionally `get_message_status` → a confirmation with message id, chat id, status and timestamp; the status reaches at least SERVER_ACK.

#### Negative test cases

1. **A draft nobody asked to send** — prompt `Draft a polite message asking a customer to confirm their delivery address. Do not send it.` The prompt is about the wording of a WhatsApp message, which makes it a near miss, but the user asked for text and explicitly declined the send.
2. **Installing WhatsApp** — prompt `How do I install WhatsApp on a new phone and move my chats over?` It names WhatsApp and chats, which the plugin handles, but it asks how to use the phone app, not about the connected instance.
3. **The Evolution API project itself** — prompt `What is the Evolution API project, and how do I host it myself?` The prompt names Evolution API, which makes it a near miss, but it asks about the software, not about the user's instance.

### 5. Global Tab

- **Country Availability**: `worldwide` (from dossier section `Country availability`).

### 6. Submit Tab

- **Demo video**: the portal takes a walkthrough as a URL (confirm on submission day which tab carries the field). This is a user action — the recording does not exist yet. Record the sign-in through the consent page and the five positive cases, place the file under `docs/demo/` in the public repository (excluded from the sdist and the wheel by `pyproject.toml`) and paste its public URL. See dossier section `Demo video`.
- **Release Notes**: Copy text from dossier section `Release notes` (Initial submission summary).
- Complete policy attestations and click **Submit for Review**.

## After Approval & Publishing

1. **Review**: OpenAI reviews the submission.
2. **Publish**: Once approved, click **Publish** in the portal.
3. **Universal Directory**: The published plugin appears in the directory for **BOTH ChatGPT and Codex** users automatically.

## Versioning Note

Remote MCP plugins publish a snapshot of reviewed server metadata. If you rename a tool, add tools, or modify schema signatures, you must re-scan the server in the portal, submit a new version for review, and publish the update upon approval.

---

## Sources

- https://developers.openai.com/plugins/deploy/submission
- https://developers.openai.com/plugins/deploy/app-review
- https://developers.openai.com/plugins/plugin-guidelines
- https://developers.openai.com/api/docs/guides/developer-mode
