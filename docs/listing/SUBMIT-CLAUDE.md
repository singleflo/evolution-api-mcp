# Submission Guide: Claude Connectors Directory

Step-by-step instructions for submitting Evolution API Assistant to the Anthropic Connectors Directory. All copy and values referenced below map directly to the dossier in `docs/listing/README.md`.

The submission is the **hosted** server at `https://evolution-mcp.singleflo.com/mcp`. Anthropic's directory no longer accepts local MCP servers packaged as desktop extensions (MCPB), so the local `uvx evolution-api-mcp` server is not part of this submission; it is distributed through PyPI, the MCP Registry and the plugin manifests instead.

Portal steps below follow the portal as documented on 2026-09-29 (https://claude.com/docs/directory/publish) and the step order recorded for the reference project. Anthropic changes the forms without notice: every step marked **confirm on submission day** could not be re-verified against the live portal when this guide was written.

## Prerequisites

Before submitting to the Connectors Directory, confirm you have:

1. **A paid Claude plan** with access to the developer portal. Anthropic's publish page states that anyone on a paid plan can submit, with no partner programme to join first. The reference project used a Team organization and an Owner role; **confirm on submission day** which account type and permission the portal asks for.
2. **Prepared materials**: the dossier (`docs/listing/README.md`), the icon file `docs/listing/icon.png`, and a populated reviewer test account (dossier section `Reviewer test account (TEMPLATE — the owner fills this before submitting)`).
3. **The hosted server live**: `https://evolution-mcp.singleflo.com/health` answers, the OAuth protected-resource metadata is served and an anonymous `POST /mcp` answers 401 (`scripts/remote_live_check.sh` runs these three probes); the privacy page loads.

## Portal Path

Navigate to the submission portal:
1. Sign in to [Claude.ai](https://claude.ai).
2. Open the developer portal at `https://claude.ai/directory/manage`.
3. Select **Submit new** and choose **MCP connector** (not a plugin bundle).

## Portal Fields (In Submission Portal Order)

The portal walks through sequential steps. Fill each field using the values from `docs/listing/README.md`:

### Step 1: Introduction
- Select **Remote MCP server**.

### Step 2: Connection
- **Server URL**: `https://evolution-mcp.singleflo.com/mcp`
- **Transport**: Streamable HTTP
- **URL Reach**: Universal URL (the same URL for every user). Each user's own Evolution API server address is typed on the server's consent page, not encoded in the MCP URL.

### Step 3: Tools
- Select **Sync Tools**. The portal reads the tools exposed by `https://evolution-mcp.singleflo.com/mcp` and verifies that every tool carries a title and valid annotations. Claude requires `title` plus the applicable `readOnlyHint` / `destructiveHint`; this server publishes the title in both `Tool.title` and `annotations.title` and all four hints as explicit booleans on every tool, which also satisfies OpenAI's stricter rule.
- Tools are listed per connection: the scan sees the tools of the connection you authorise with. Authorise with the `standard` policy and every toolset ticked (see the reviewer account) so the sync reads all 38 tools. A connection that ticks fewer toolsets, or chooses `read`, shows fewer.

### Step 4: Listing
- **Name**: `Evolution API Assistant` (from dossier section `Identity` -> `Name`, 100 characters max).
- **Tagline**: `Read, search and answer your Evolution API chats` (from dossier section `Identity` -> `Tagline`, 55 characters max; the portal's one-line field allows more, the shorter dossier limit is kept for both stores).
- **Description**: Copy the exact text from dossier section `Identity` -> `Long description` (2,000 characters max).
- **Categories**: Select 1 to 5 categories from the portal dropdown (intended, from dossier section `Categories`: Communication, Productivity; the picker's labels govern — **confirm on submission day**).
- **Documentation URL**: `https://github.com/singleflo/evolution-api-mcp` (from dossier section `URLs` -> `Documentation URL`).
- **Privacy Policy URL**: `https://evolution-mcp.singleflo.com/privacy` (from dossier section `URLs` -> `Privacy URL`).
- **Support Contact / URL**: `https://github.com/singleflo/evolution-api-mcp/issues` (from dossier section `URLs` -> `Support URL`).
- **Country availability**: `worldwide` (from dossier section `Country availability` — the portal asks where the connector is available).
- **Icon**: Upload `docs/listing/icon.png` (from dossier section `Icon`, 512x512 flat PNG).
- **URL Slug**: Set the listing URL slug.
  > **Warning:** The URL slug is permanent once published and cannot be changed later.

### Step 5: Use cases
- Describe primary workflows based on dossier sections `Starter prompts` and `Release notes`. Indicate that the connector performs both read and write operations: writes are limited by the policy and toolsets the user chooses at connection time, and irreversible actions are never available on the hosted server.

### Step 6: Company
- Enter company name (Persevida SL, the publisher named on the hosted legal pages), website (`https://singleflo.com`, as used for the reference project — **confirm on submission day**) and primary contact email.

### Step 7: Authentication
- **Authentication Choice**: OAuth with Dynamic Client Registration (`oauth_dcr`).
  - The OAuth metadata advertises `S256` PKCE and a `registration_endpoint` (asserted in `tests/test_remote_auth.py`).

### Step 8: Data handling
- The portal asks whether the underlying API is your own, proxied from a partner with permission, or a third party's you do not control, and whether the connector handles personal health data or sponsored content. Answer for this connector: the API is the **user's own self-hosted Evolution API server**, chosen by the user on the consent page; the publisher does not operate it and cannot reach it except with the user's instance token. The hosted server accepts only `WHATSAPP-BUSINESS` instances (the official WhatsApp Business Platform channel). It handles no health data and carries no sponsored content. Pick the portal option that matches "the user's own endpoint" — **confirm on submission day** which label the portal offers.

### Step 9: Test & launch
- Paste the completed credentials block from dossier section `Reviewer test account (TEMPLATE — the owner fills this before submitting)`:

| Field | Value |
|---|---|
| Evolution API URL | `<review Evolution server base URL, no trailing slash>` |
| MCP endpoint | `https://evolution-mcp.singleflo.com/mcp` |
| Instance token | `<the review instance's own token, entered through the portal field only>` |
| Integration | `WHATSAPP-BUSINESS` |
| Policy | `standard` |
| Toolsets | every box ticked |

The instance token is entered in the portal's credentials field only; it never appears in this repository.

Include a summary of visible fixture data: the review number, the fixture customer and the direction of their messages, the chat with unread messages, the messages containing the word invoice, and the approved templates.

- Confirm you have exercised the tools with MCP Inspector or a custom connector, starting from the five positive cases in dossier section `Test cases`.

### Step 10: Compliance
- Attest to all seven policy acknowledgments. Per Anthropic's submission page they cover: directory guidelines, first-party API usage, financial transactions, AI media generation, prompt injection, conversation data collection and public documentation. The answers for this connector: no financial transactions and no AI-generated media; tool descriptions carry no instructions to the model and say that message text is data written by other people; the server collects nothing from the conversation beyond the tool arguments; documentation is public at the repository and on the hosted legal pages.

### Step 11: Review & Submit
- Conduct final verification of submitted fields and submit for review.

## What Reviewers Check

During review, Anthropic verifies:
1. **Tool Annotations**: Every tool has a `title`, `readOnlyHint`, `destructiveHint`, and `openWorldHint` matching actual execution behavior (refer to dossier section `Tool annotations`). Sends and changes are marked destructive because a sent message cannot be recalled; the Why column says so per tool.
2. **Privacy Page**: The privacy URL (`https://evolution-mcp.singleflo.com/privacy`) is live, HTTPS, accessible, and discloses data storage practices.
3. **Working Examples**: Test cases exercise real tools (`list_chats`, `read_messages`, `search_messages`, `list_templates`, `send_text_message`).
4. **Test Account**: Reviewer credentials work cleanly without MFA, SMS, email verification, or private network barriers. The review Evolution server must be reachable from the public internet, because the hosted server refuses private addresses.

## What Happens After Submission

Submission is no longer one human gate. An automated scan runs first, and a
server that passes it is listed as a **Community Connector** without anyone
testing it by hand. Anthropic may then escalate a connector it considers
highly useful to **Verified**, a slower review where a person exercises every
tool. The two labels differ in the signal they give a user, not in what the
connector may do once connected — so the listing is live at the first
outcome, and the badge is a second, separate event that is not applied for.

- **Submissions Dashboard**: Track progress and reviewer messages in the developer portal at `https://claude.ai/directory/manage`.
- **Publication**: Once published, your listing slug becomes permanent and public.
- **No domain verification**: unlike OpenAI, Anthropic asks for no DNS record
  and no `.well-known` challenge here. The `/.well-known/openai-apps-challenge`
  route this server carries is for the OpenAI submission alone.
- **Health & Usage Dashboard**: Access server health metrics and invocation volume analytics from the portal, where it offers them.

## Custom Connector Fallback (While Waiting)

While waiting for directory approval, users can connect immediately using the custom connector link (percent-encoded URL):

```text
https://claude.ai/customize/connectors?modal=add-custom-connector&connectorName=Evolution%20API%20Assistant&connectorUrl=https%3A%2F%2Fevolution-mcp.singleflo.com%2Fmcp
```

## Versioning Note

If you modify server tool names, add tools, or alter metadata schemas, you must update the server deployment and submit an updated version through the portal.

---

## Sources

- https://claude.com/docs/directory/publish
- https://claude.com/docs/connectors/building/submission
- https://claude.com/docs/connectors/building/directory-vs-custom
- https://claude.com/docs/connectors/building/authentication
- https://claude.com/docs/connectors/building/review-criteria
