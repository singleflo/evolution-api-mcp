"""Live checks against a real, paired Evolution instance (`uv run pytest -m live`).

Environment:
  EVOLUTION_TEST_API_URL         Evolution server URL
  EVOLUTION_TEST_INSTANCE_TOKEN  that instance's own token (not the global API key)
  EVOLUTION_TEST_CHAT            a phone number or chat id whose owner agreed to receive test messages
  EVOLUTION_TEST_ALLOW_SEND=1    permits the one test that sends a message

Every test is skipped when its variables are missing. Read tests never change anything.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator

import anyio
import pytest

from evolution_api_mcp import context
from evolution_api_mcp.config import load_local_config
from evolution_api_mcp.registry import BUSINESS
from evolution_api_mcp.tools import chats, instance, messaging, templates

pytestmark = [pytest.mark.live, pytest.mark.anyio]

# Status names Evolution stores: Baileys and the first Business record use SERVER_ACK / DELIVERY_ACK / READ / PLAYED;
# Business status callbacks are stored upper-cased as Meta names them (`whatsapp.business.service.ts:809`).
ACKNOWLEDGED = frozenset({"SERVER_ACK", "DELIVERY_ACK", "READ", "PLAYED", "SENT", "DELIVERED"})
ACK_WAIT_SECONDS = 30


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} is not set")
    return value


@pytest.fixture
def live_server(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The local runtime against the instance under test: every toolset, default deny list, default pacing."""
    config = load_local_config(
        {
            "EVOLUTION_API_URL": _required("EVOLUTION_TEST_API_URL"),
            "EVOLUTION_INSTANCE_TOKEN": _required("EVOLUTION_TEST_INSTANCE_TOKEN"),
            "EVOLUTION_MCP_TOOLSETS": "all",
        }
    )
    monkeypatch.setattr(context, "_local", None)
    context.configure_local(config)
    yield


@pytest.fixture
def chat_under_test() -> str:
    return _required("EVOLUTION_TEST_CHAT")


async def test_instance_is_open(live_server: None) -> None:
    status = json.loads(await instance.get_instance_status())
    assert status["state"] == "open", status


async def test_list_chats(live_server: None) -> None:
    listing = json.loads(await chats.list_chats(limit=5))
    assert isinstance(listing["chats"], list)
    assert listing["limit"] == 5
    for chat in listing["chats"]:
        assert chat["chat_id"]


async def test_read_messages_of_the_test_chat(live_server: None, chat_under_test: str) -> None:
    history = json.loads(await chats.read_messages(chat=chat_under_test, limit=10))
    assert history["total"] >= len(history["messages"])
    assert len(history["messages"]) <= 10
    for message in history["messages"]:
        assert message["message_id"]
        assert message["type"]


async def test_search_messages_in_the_test_chat(live_server: None, chat_under_test: str) -> None:
    result = json.loads(await chats.search_messages(query="the", chat=chat_under_test, limit=5))
    assert set(result) >= {"query", "matches", "scanned", "complete"}
    assert len(result["matches"]) <= 5


async def test_list_templates_on_business_instances(live_server: None) -> None:
    status = json.loads(await instance.get_instance_status())
    if status["integration"] != BUSINESS:
        pytest.skip("list_templates needs a WHATSAPP-BUSINESS instance")
    listing = json.loads(await templates.list_templates())
    assert isinstance(listing["templates"], list)


async def test_send_text_and_reach_the_server(live_server: None, chat_under_test: str) -> None:
    if os.environ.get("EVOLUTION_TEST_ALLOW_SEND") != "1":
        pytest.skip("EVOLUTION_TEST_ALLOW_SEND=1 is required to send a message")

    text = f"evolution-api-mcp live check {int(time.time())}"
    sent = json.loads(await messaging.send_text_message(chat=chat_under_test, text=text))
    assert sent["message_id"]

    seen: list[str] = []
    with anyio.move_on_after(ACK_WAIT_SECONDS):
        while True:
            status = json.loads(await chats.get_message_status(chat=chat_under_test, message_id=sent["message_id"]))
            seen = [update["status"] for update in status["updates"]]
            if ACKNOWLEDGED.intersection(seen) or status["latest"] in ACKNOWLEDGED:
                return
            await anyio.sleep(2)
    pytest.fail(f"No acknowledgement within {ACK_WAIT_SECONDS} s for {sent['message_id']}; statuses seen: {seen}")
