#!/usr/bin/env bash
# Start the Evolution sandbox and create the instance `mcp-sandbox` (token `sandbox-instance-token`).
set -euo pipefail

cd "$(dirname "$0")"

URL="${EVOLUTION_SANDBOX_URL:-http://127.0.0.1:18080}"
GLOBAL_KEY="sandbox-global-key"

docker compose up -d

echo "Waiting for Evolution at ${URL} (up to 120 s)..."
for _ in $(seq 1 60); do
  if curl -fsS -o /dev/null "${URL}/"; then
    break
  fi
  sleep 2
done
if ! curl -fsS -o /dev/null "${URL}/"; then
  echo "Evolution did not answer GET / within 120 s; see: docker compose logs api" >&2
  exit 1
fi

body=$(mktemp)
trap 'rm -f "${body}"' EXIT
status=$(curl -sS -o "${body}" -w '%{http_code}' \
  -X POST "${URL}/instance/create" \
  -H "apikey: ${GLOBAL_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"instanceName": "mcp-sandbox", "integration": "WHATSAPP-BAILEYS", "token": "sandbox-instance-token", "qrcode": false}')

case "${status}" in
  201) echo "Created instance mcp-sandbox." ;;
  403 | 409) echo "Instance mcp-sandbox already exists." ;;
  *)
    echo "POST /instance/create answered ${status}:" >&2
    cat "${body}" >&2
    echo >&2
    exit 1
    ;;
esac

echo "Sandbox ready. Run: EVOLUTION_SANDBOX_URL=${URL} uv run pytest -m sandbox"
