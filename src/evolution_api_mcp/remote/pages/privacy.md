# Privacy Policy

Last updated: 2 October 2026.

This Privacy Policy explains how {{PUBLISHER}} collects, uses, stores, and protects your information when you connect an Evolution API instance to an AI client (such as Claude or ChatGPT) using our hosted remote server service.

We operate with strict data minimization principles. Our service functions as an authentication and secure transport bridge between your AI client host and your own Evolution API server. Evolution API is the open-source WhatsApp gateway you run yourself; this service is an independent project and is not affiliated with Meta, WhatsApp or the Evolution API project.

## What We Store

To maintain an active connection and handle authorization securely, our remote server stores a small set of operational data in a SQLite database on our server. The database file itself is not encrypted; specific sensitive fields are encrypted individually, as detailed below.

| Category | Description | Storage Method & TTL |
| --- | --- | --- |
| Evolution URL | The base URL of your Evolution API server. This is connection metadata, not a secret. | Stored in plaintext in the SQLite database |
| Instance name and integration | The name of the one instance your token belongs to and its integration type (for example `WHATSAPP-BUSINESS`), as reported by your Evolution server when you connected. Also connection metadata. | Stored in plaintext in the SQLite database |
| Instance token | The token of that one instance, provided during authorization. The server-wide global API key is refused and never stored. | Encrypted with Fernet (AES-128-CBC + HMAC-SHA256, via the cryptography package) |
| Connection fingerprint | A SHA-256 hash of the Evolution URL and the instance token, used only to recognise that you are reconnecting the same instance. | Hashed using SHA-256; the token cannot be read back from it |
| OAuth Client Records | The dynamic client registration records created when your AI host connects through OAuth 2.1 (the host's name and redirect addresses) | Encrypted with Fernet (AES-128-CBC + HMAC-SHA256, via the cryptography package); kept until the operator removes them |
| Policy and toolset selection | The execution policy (read-only or read and act) and the toolsets you ticked during consent | Stored in the SQLite database for as long as the connection exists |
| Hashed Tokens | Cryptographic hashes of OAuth 2.1 authorization codes, access tokens, and refresh tokens | Hashed using SHA-256 (raw tokens are never stored); authorization codes 10 minutes, access tokens 1 hour, refresh tokens 30 days |
| Pending authorizations | The consent request that is waiting for you to press Connect or Refuse | Stored in the SQLite database for 10 minutes at most |
| Tenant References | Generated internal identifiers linking your OAuth subject to your connection settings, with the time the connection was created and last used | Stored in the SQLite database |
| Downloaded media | A received image, video, audio file or document, only when your assistant asks to download it | Stored on disk behind an unguessable link with a strict 15-minute Time-To-Live (TTL) |
| Exported chats | A ZIP of one chat's messages and attachments, only when your assistant asks to export it | Stored on disk behind an unguessable link with a strict 15-minute Time-To-Live (TTL) |

## Why We Store It

Each category above is held for one purpose and for no other:

* The Evolution URL, the instance name, the integration and the encrypted instance token are the connection itself: they are what lets the server reach your instance when your assistant asks it to.
* The policy and toolset selection are what the safety gate reads before every tool call, so that a connection authorised as read-only stays read-only and a toolset you did not tick is never offered.
* Hashed tokens and tenant references are how a request is recognised as yours rather than another tenant's.
* Downloaded media is the output of the tool you asked to run, kept only long enough for you to fetch it.
* An exported chat is the output of the tool you asked to run, kept only long enough for you to fetch it.

None of it is used for analytics, profiling, advertising, or the training of any model. We run no third-party trackers and the pages of this service load nothing from anyone else.

## Where It Is Processed

One server runs this service, and everything described above stays on it: a single SQLite database file and a directory of downloaded files on the same machine. Nothing is copied to another provider, to a second region, or to an analytics service. The only outbound request this server makes on your behalf goes to the Evolution API address you named during consent. The application writes its own log lines to standard error; they can name the host of an Evolution server and the internal identifier of a connection, and never contain a token or the text of a message. The web server and hosting platform in front of the application may keep ordinary access logs (time, path, status and network address of each request).

To show names next to messages and to accept a name where a chat is expected, the server reads contact and group names from your Evolution server and keeps them in memory for at most 10 minutes; they are never written to disk.

## What We Never Store

We design our infrastructure to avoid processing or retaining personal or business records beyond what is strictly necessary to proxy requests. We never store:

* WhatsApp messages, chat lists, contacts, group data or any other content returned by your Evolution server, beyond the 15-minute download and export links and the 10-minute in-memory name list described above. The server hands that content to your AI host and keeps no copy.
* Conversation text, prompts, or messages exchanged between you and your AI host.
* The instance token in plain text, or raw authorization codes, access tokens or refresh tokens.
* The Evolution server's global API key, which the server refuses.

## Data Retention and Automatic Purging

We enforce strict data retention rules to ensure connection details and tokens are erased when no longer in use:

* **Downloaded media**: The download link expires 15 minutes after it is issued; the bytes are removed when an expired link is hit, or by the hourly sweep at the latest.
* **Exported chats**: same 15-minute link and hourly sweep as downloaded media.
* **Access Tokens**: Short-lived tokens expiring after 1 hour.
* **Refresh Tokens**: Expire after 30 days.
* **Revocation & Disconnection**: When the last token family for your connection is revoked, which is what disconnecting the integration in your host application (such as Claude or ChatGPT) triggers, the tenant row holding your Evolution URL and encrypted instance token is deleted.
* **Idle Purge**: A sweep at server startup and hourly thereafter removes tenant rows that have been idle for 90 days and hold no live token.

## Your Controls

You retain total control over your credentials and active connections:

1. **Host Disconnection**: Disconnect or delete the integration directly within your AI host application (e.g., Claude, ChatGPT, or another client). This triggers an automated revocation request to our server.
2. **Explicit Revocation Endpoint**: Send a `POST /revoke` request presenting your active token. Our server immediately revokes the associated token family and erases the tenant row if no active tokens remain.
3. **Rotate the token in Evolution**: an instance token is changeable in your Evolution server, and changing it there ends this server's access immediately without going through us. What remains here is an encrypted string that no longer opens anything, deleted with the rest of the tenant row on disconnection or after the idle window.
4. **Change what the connection may do**: connect again and pick another policy or other toolsets. The most recent choice governs the connection, including tokens issued before it.

To ask what is held for your connection, or to have it erased before the windows above elapse, open an issue or write to the contact below and name the Evolution URL you connected; we answer from the same tenant row this policy describes.

## Data Recipients and Third-Party Sharing

We do not sell, rent, or monetize your data. We do not share your connection details or your instance token with any third parties.

The only recipient of requests forwarded by our service is your own Evolution API server specified during authorization. What your Evolution server does next, including sending a message through WhatsApp, is governed by your own deployment and by WhatsApp's terms.

## Contact Us

If you have questions, feedback, or concerns regarding this Privacy Policy or data handling practices, please open an issue or reach out to {{SUPPORT_EMAIL}}.
