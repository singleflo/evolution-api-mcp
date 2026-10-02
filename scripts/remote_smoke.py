#!/usr/bin/env python3
"""Plain JSON-RPC smoke test of a hosted evolution-api-mcp endpoint.

Usage:
    scripts/remote_smoke.py <public_url> <access_token>

The access token comes from scripts/remote_token.py. The script speaks MCP over Streamable HTTP with nothing but
httpx2: `initialize`, `tools/list`, then `tools/call get_instance_status`. It prints the number of tools this
connection was granted and the instance status the server reported.

Exit status: 0 when every step answers; 1 on any HTTP, JSON-RPC or tool error, with the reason on stderr.
"""

from __future__ import annotations

import argparse
import json
import sys

import httpx2

PROTOCOL_VERSION = "2025-06-18"


class SmokeError(Exception):
    pass


def _payload(response: httpx2.Response) -> dict:
    """The JSON-RPC message of an answer: plain JSON, or the first `data:` line of an event stream."""
    if response.status_code != 200:
        raise SmokeError(f"HTTP {response.status_code}: {response.text[:300]}")
    if "text/event-stream" in response.headers.get("content-type", ""):
        for line in response.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[len("data:") :].strip())
        raise SmokeError(f"no data frame in the event stream: {response.text[:300]!r}")
    return response.json()


def _result(message: dict) -> dict:
    if "error" in message:
        raise SmokeError(f"JSON-RPC error: {message['error']}")
    return message["result"]


def run(public_url: str, token: str) -> tuple[int, object]:
    url = public_url.rstrip("/") + "/mcp"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    with httpx2.Client(timeout=60) as http:

        def rpc(method: str, params: dict, id: int, extra: dict[str, str] | None = None) -> dict:
            body = {"jsonrpc": "2.0", "id": id, "method": method, "params": params}
            return _result(_payload(http.post(url, headers=headers | (extra or {}), json=body)))

        initialized = rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "remote_smoke", "version": "1"},
            },
            1,
        )
        session = {"MCP-Protocol-Version": initialized["protocolVersion"]}

        tools = rpc("tools/list", {}, 2, session)["tools"]

        called = rpc("tools/call", {"name": "get_instance_status", "arguments": {}}, 3, session)
        text = called["content"][0]["text"]
        if called.get("isError"):
            raise SmokeError(f"get_instance_status failed: {text}")
        return len(tools), json.loads(text)


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test a hosted evolution-api-mcp endpoint.")
    parser.add_argument("public_url", help="base URL of the hosted server")
    parser.add_argument("access_token", help="OAuth access token from scripts/remote_token.py")
    args = parser.parse_args()
    try:
        count, status = run(args.public_url, args.access_token)
    except (SmokeError, httpx2.HTTPError, KeyError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"{count} tools")
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
