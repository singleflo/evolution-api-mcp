"""Real HTTP against the Docker Evolution sandbox (tests/sandbox): no fake, no transport double.

Start the sandbox with `tests/sandbox/up.sh`, then run `uv run pytest -m sandbox` (optionally with
EVOLUTION_SANDBOX_URL). The suite is skipped when nothing answers at that URL. The instance is never paired with
WhatsApp, so the tests read state, change configuration on the sandbox only and expect sends to fail.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator

import httpx2
import pytest

from evolution_api_mcp import context
from evolution_api_mcp.client import EvolutionClient
from evolution_api_mcp.config import load_local_config
from evolution_api_mcp.discovery import DiscoveryRefused, discover
from evolution_api_mcp.errors import ToolExecutionError
from evolution_api_mcp.registry import BAILEYS
from evolution_api_mcp.tools import chats, contacts, events, instance, messaging, settings

pytestmark = [pytest.mark.sandbox, pytest.mark.anyio]

SANDBOX_URL = os.environ.get("EVOLUTION_SANDBOX_URL", "http://127.0.0.1:18080").rstrip("/")
INSTANCE_TOKEN = "sandbox-instance-token"
GLOBAL_KEY = "sandbox-global-key"
INSTANCE_NAME = "mcp-sandbox"


@pytest.fixture(scope="module")
def sandbox_url() -> str:
    try:
        httpx2.get(f"{SANDBOX_URL}/", timeout=5.0).raise_for_status()
    except httpx2.HTTPError as exc:
        pytest.skip(f"No Evolution sandbox at {SANDBOX_URL} ({exc}); start it with tests/sandbox/up.sh")
    return SANDBOX_URL


@pytest.fixture
def local_server(sandbox_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The local runtime configured like a user would: instance token, every toolset, only post_status denied."""
    config = load_local_config(
        {
            "EVOLUTION_API_URL": sandbox_url,
            "EVOLUTION_INSTANCE_TOKEN": INSTANCE_TOKEN,
            "EVOLUTION_MCP_TOOLSETS": "all",
            "EVOLUTION_MCP_DENY": "post_status",
        }
    )
    monkeypatch.setattr(context, "_local", None)
    context.configure_local(config)
    yield


def sandbox_client(token: str) -> EvolutionClient:
    return EvolutionClient(SANDBOX_URL, token)


async def test_discovery_accepts_the_instance_token(sandbox_url: str) -> None:
    client = sandbox_client(INSTANCE_TOKEN)
    try:
        identity = await discover(client)
    finally:
        await client.aclose()
    assert identity.name == INSTANCE_NAME
    assert identity.integration == BAILEYS


async def test_discovery_refuses_the_global_key(sandbox_url: str) -> None:
    client = sandbox_client(GLOBAL_KEY)
    try:
        with pytest.raises(DiscoveryRefused, match="global AUTHENTICATION_API_KEY"):
            await discover(client)
    finally:
        await client.aclose()


async def test_status_of_an_unpaired_instance(local_server: None) -> None:
    status = json.loads(await instance.get_instance_status())
    assert status["instance"] == INSTANCE_NAME
    assert status["integration"] == BAILEYS
    assert status["state"] == "close"
    assert status["server"] == "local"
    assert "not connected" in status["note"]


async def test_instance_settings_round_trip(local_server: None) -> None:
    changed = json.loads(await settings.update_instance_settings(always_online=False))
    assert changed["always_online"] is False
    assert json.loads(await settings.get_instance_settings())["always_online"] is False

    changed = json.loads(await settings.update_instance_settings(always_online=True))
    assert changed["always_online"] is True
    assert json.loads(await settings.get_instance_settings())["always_online"] is True

    await settings.update_instance_settings(always_online=False)


async def test_webhook_round_trip(local_server: None) -> None:
    saved = json.loads(
        await events.set_webhook(enabled=True, url="https://example.invalid/hook", events=["MESSAGES_UPSERT"])
    )
    assert saved["enabled"] is True
    assert saved["url"] == "https://example.invalid/hook"
    assert saved["events"] == ["MESSAGES_UPSERT"]

    stored = json.loads(await events.get_webhook())
    assert stored["enabled"] is True
    assert stored["url"] == "https://example.invalid/hook"
    assert stored["events"] == ["MESSAGES_UPSERT"]

    await events.set_webhook(enabled=False, url="https://example.invalid/hook", events=[])
    assert json.loads(await events.get_webhook())["enabled"] is False


async def test_a_fresh_instance_has_no_chats_contacts_or_history(local_server: None) -> None:
    listing = json.loads(await chats.list_chats())
    assert listing["chats"] == []
    assert listing["has_more"] is False

    history = json.loads(await chats.read_messages(chat="393331234567"))
    assert history["total"] == 0
    assert history["messages"] == []
    assert "DATABASE_SAVE_DATA_NEW_MESSAGE" in history["note"]

    found = json.loads(await contacts.find_chats(query="xx"))
    assert found["chats"] == []


async def test_sending_from_an_unpaired_instance_fails_as_a_tool_error(local_server: None) -> None:
    with pytest.raises(ToolExecutionError):
        await messaging.send_text_message(chat="393331234567", text="sandbox check", delay_ms=0)
