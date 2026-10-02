#!/usr/bin/env python3
"""Mint an access token from a hosted evolution-api-mcp server.

Drives the whole flow an MCP host walks: dynamic client registration, PKCE, /authorize, the consent page (the step
that stores YOUR Evolution connection as a tenant) and the code exchange. Prints the access token on stdout, ready
for an `Authorization: Bearer` header.

The Evolution side of the consent form comes from --evolution-url and --instance-token (defaults: the environment
variables EVOLUTION_TEST_API_URL and EVOLUTION_TEST_INSTANCE_TOKEN, which keeps the token out of shell history and
the process list). The redirect URI points at port 1 on purpose: the code is exchanged directly, no local listener
exists.

Usage:
    scripts/remote_token.py <public_url> --evolution-url URL --instance-token TOKEN [--policy standard]
                            [--toolsets messaging,chats]

Exit status: 0 with the token on stdout; 1 with the reason on stderr (for example the consent page refusing an
instance whose integration this deployment does not accept).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import os
import re
import secrets
import sys
from urllib.parse import parse_qs, urlparse

import httpx2

from evolution_api_mcp.config import ConfigError
from evolution_api_mcp.toolsets import TOOLSET_ORDER, parse_toolsets

REDIRECT_URI = "http://localhost:1/callback"
SCOPE = "evolution"


def _consent_error(page: str) -> str:
    """The message a re-rendered consent form shows, or the start of the page when none can be found."""
    match = re.search(r'role="alert"[^>]*>(.*?)</', page, re.S)
    text = match.group(1) if match else re.sub(r"<[^>]+>", " ", page)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()[:400]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Register a client, consent with your Evolution connection and print the OAuth access token."
    )
    parser.add_argument("public_url", help="base URL of the hosted server, e.g. https://evolution-mcp.singleflo.com")
    parser.add_argument(
        "--evolution-url",
        default=os.environ.get("EVOLUTION_TEST_API_URL"),
        help="address of your Evolution API server (default: $EVOLUTION_TEST_API_URL)",
    )
    parser.add_argument(
        "--instance-token",
        default=os.environ.get("EVOLUTION_TEST_INSTANCE_TOKEN"),
        help="the token of the one instance (default: $EVOLUTION_TEST_INSTANCE_TOKEN)",
    )
    parser.add_argument(
        "--policy",
        default="standard",
        choices=["read", "standard"],
        help="what the assistant may do on the instance (default: standard)",
    )
    parser.add_argument(
        "--toolsets",
        default=None,
        help=f"comma-separated toolsets, or core / all (default: core). Toolsets: {', '.join(TOOLSET_ORDER)}",
    )
    args = parser.parse_args()
    if not args.evolution_url:
        parser.error("no Evolution address: pass --evolution-url or set EVOLUTION_TEST_API_URL")
    if not args.instance_token:
        parser.error("no instance token: pass --instance-token or set EVOLUTION_TEST_INSTANCE_TOKEN")
    try:
        toolsets = [name for name in TOOLSET_ORDER if name in parse_toolsets(args.toolsets)]
    except ConfigError as exc:
        parser.error(str(exc))
    base = args.public_url.rstrip("/")

    http = httpx2.Client(base_url=base, follow_redirects=False, timeout=60)

    registered = http.post(
        "/register",
        json={
            "redirect_uris": [REDIRECT_URI],
            "client_name": "remote_token",
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        },
    )
    registered.raise_for_status()
    client_id = registered.json()["client_id"]

    # /authorize and /consent answer 302 on success, which raise_for_status() would reject on a client that does not
    # follow redirects: assert the redirect explicitly instead.
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")

    asked = http.get(
        "/authorize",
        params={
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "remote-token",
            "scope": SCOPE,
            "resource": f"{base}/mcp",
        },
    )
    if asked.status_code != 302:
        print(f"/authorize answered {asked.status_code}: {asked.text[:200]}", file=sys.stderr)
        return 1
    req = parse_qs(urlparse(asked.headers["location"]).query)["req"][0]

    consented = http.post(
        "/consent",
        data={
            "req": req,
            "base_url": args.evolution_url,
            "instance_token": args.instance_token,
            "policy": args.policy,
            "toolsets": toolsets,
        },
    )
    if consented.status_code != 302:
        # 200 = the consent form was re-rendered with an error (refused token, unreachable server, integration this
        # deployment does not accept); anything else is a protocol fault.
        print(f"consent refused ({consented.status_code}): {_consent_error(consented.text)}", file=sys.stderr)
        return 1
    code = parse_qs(urlparse(consented.headers["location"]).query)["code"][0]

    token = http.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "code_verifier": verifier,
            "redirect_uri": REDIRECT_URI,
        },
    )
    token.raise_for_status()
    print(token.json()["access_token"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
