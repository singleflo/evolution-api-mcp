#!/usr/bin/env bash
# Stop the Evolution sandbox and delete its volumes.
set -euo pipefail

cd "$(dirname "$0")"

docker compose down -v
