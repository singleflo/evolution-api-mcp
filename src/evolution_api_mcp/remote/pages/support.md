# Support

Welcome to the support page for the evolution-api-mcp remote server integration. {{PUBLISHER}} provides technical assistance and resources for setting up and troubleshooting your integration.

Our hosted remote server acts as an authentication bridge between your AI client host (such as Claude or ChatGPT) and your own Evolution API server. We maintain this service to enable secure OAuth 2.1 access and proxy MCP operations seamlessly.

## Support Resources

If you encounter issues, discover bugs, or have feature requests, please use the following official channels:

* **Issue Tracker**: [https://github.com/singleflo/evolution-api-mcp/issues](https://github.com/singleflo/evolution-api-mcp/issues)
* **Documentation**: [https://github.com/singleflo/evolution-api-mcp#readme](https://github.com/singleflo/evolution-api-mcp#readme)

## Service Response Expectations

Support for this project is provided on a best-effort basis by the open-source maintainers and community contributors. We aim to review incoming issues promptly, but response times may vary depending on maintainer availability and issue severity.

## Reporting Bugs and Seeking Help

When reporting an issue on GitHub, please include:

1. A clear description of the problem or unexpected behavior.
2. The version of the package or host client you are using.
3. Relevant error messages (ensuring no instance tokens, API keys, secrets, phone numbers or confidential Evolution URLs are included).
4. Steps to reproduce the issue.

## Is the Server Up?

`/health` answers `{"status": "ok"}` and the running version, from the server itself, without signing in. If it answers and your assistant still cannot connect, the problem is in the connection rather than in the service, and the questions below are the place to start.

## Frequently Asked Questions

### How do I disconnect my Evolution connection?
You can disconnect the integration directly inside your AI host application (e.g., Claude or ChatGPT). Disconnecting immediately revokes your active token family and purges your stored instance token from our remote database.

### What should I do if authorization fails?
Ensure that your Evolution URL is reachable over HTTPS from the public internet and that you pasted the token of one instance, not the server's global API key, which is refused. Evolution resolves instance tokens only when it runs with `DATABASE_SAVE_DATA_INSTANCE=true`, which is its default. If your instance uses the Evolution channel integration, this hosted server refuses it, because it connects WhatsApp Business Platform and WhatsApp Web (Baileys) instances; the local server (`uvx evolution-api-mcp`) covers every integration.

### Why is a tool missing or refused?
The assistant is offered only the toolsets you ticked on the consent page, and a read-only connection refuses every tool that changes data. Connect again to choose differently. Tools that cannot be undone, and tools that take secrets, are never available on the hosted server.

### Why did a message not arrive?
On WhatsApp Business Platform instances a person receives free-form messages only within 24 hours of their last message to you; outside that window only approved templates are delivered. When Meta rejects a message, the send tool reports the rejection and says that nothing was sent.

## Contact

For direct inquiries or support concerns, please contact {{SUPPORT_EMAIL}}.
