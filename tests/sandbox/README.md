# Evolution sandbox

A throwaway Evolution API v2.3.7 (with Postgres and Redis) on `127.0.0.1:18080`, used by `tests/test_sandbox.py`.
The WhatsApp session is never paired, so nothing reaches WhatsApp.

```bash
tests/sandbox/up.sh                      # start, wait for GET /, create instance mcp-sandbox
uv run pytest -m sandbox                 # EVOLUTION_SANDBOX_URL overrides http://127.0.0.1:18080
tests/sandbox/down.sh                    # stop and delete the volumes
```

| Value | Content |
| --- | --- |
| Global API key | `sandbox-global-key` (must be refused by the MCP server) |
| Instance | `mcp-sandbox`, integration `WHATSAPP-BAILEYS` |
| Instance token | `sandbox-instance-token` |

The suite is skipped when nothing answers at the sandbox URL. If `evoapicloud/evolution-api:v2.3.7` cannot be
pulled, build the image from an Evolution checkout (`docker build -t evolution/api:local <checkout>`) and set that
tag as `image` of the `api` service in `docker-compose.yaml`.
