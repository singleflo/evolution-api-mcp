"""The scenarios of docs/qa/SCENARIOS.md, run through an in-memory MCP client against a real instance.

`uv run pytest -m live tests/test_scenarios_live.py`

Each test sends its scenario's expected call (the one in the catalog) through `mcp.Client(build_server(mode="local"))`,
exactly as a host would, and asserts that the call is not an error and that the result has the scenario's keys. It does
not judge an agent's choices; docs/qa/RUNS.md does that, by hand.

Environment (the same conventions as tests/test_live.py):
  EVOLUTION_TEST_API_URL         Evolution server URL
  EVOLUTION_TEST_INSTANCE_TOKEN  that instance's own token (not the global API key)
  EVOLUTION_TEST_CHAT            a phone number or chat id whose owner agreed to receive test messages
  EVOLUTION_TEST_ALLOW_SEND=1    permits S11 to S15 and S20, which send, forward, export or mark read

Names come at run time from `get_chat(EVOLUTION_TEST_CHAT)`: the suite never hard-codes a contact. A scenario whose
instance lacks what it needs (a name, a group, a document, a photo, an @lid chat, a Baileys feature) is skipped with
the reason. Everything that sends goes to EVOLUTION_TEST_CHAT only; downloads, exports and the files S13 sends live in
pytest's tmp_path.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from mcp import Client

from evolution_api_mcp import clock, context, directory, registry
from evolution_api_mcp.config import load_local_config
from evolution_api_mcp.server import build_server

pytestmark = [pytest.mark.live, pytest.mark.anyio]

MESSAGE_KEYS = ("message_id", "from_me", "timestamp", "type")
NO_MEDIA_MARKERS = ("no media", "S3", "MinIO", "Failed to fetch stream")


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} is not set")
    return value


def _require_send() -> None:
    if os.environ.get("EVOLUTION_TEST_ALLOW_SEND") != "1":
        pytest.skip("EVOLUTION_TEST_ALLOW_SEND=1 is required: this scenario sends, forwards, exports or marks read")


@pytest.fixture
def runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """The local runtime against the instance under test; downloads, exports and sendable files sit in tmp_path."""
    files = tmp_path / "files"
    files.mkdir()
    config = load_local_config(
        {
            "EVOLUTION_API_URL": _required("EVOLUTION_TEST_API_URL"),
            "EVOLUTION_INSTANCE_TOKEN": _required("EVOLUTION_TEST_INSTANCE_TOKEN"),
            "EVOLUTION_MCP_TOOLSETS": "all",
            "EVOLUTION_MCP_DOWNLOAD_DIR": str(tmp_path / "downloads"),
            "EVOLUTION_MCP_FILE_ROOTS": str(files),
        }
    )
    monkeypatch.setattr(context, "_local", None)
    context.configure_local(config)
    return tmp_path


# --- the in-memory host --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Reply:
    is_error: bool
    text: str

    @property
    def data(self) -> dict:
        return json.loads(self.text)


@dataclass(frozen=True)
class Subject:
    """The chat under test: `reference` is what a scenario passes as `chat` (its name when that names one chat)."""

    chat_id: str
    name: str | None
    is_group: bool
    integration: str
    by_name: bool

    @property
    def reference(self) -> str:
        return self.name if self.by_name and self.name else self.chat_id

    def needs_name(self) -> str:
        if not self.by_name or self.name is None:
            pytest.skip("the test chat has no name that identifies exactly one chat on this instance")
        return self.name


class Agent:
    """A host: calls tools by name and gets what a model would get."""

    def __init__(self, client: Client) -> None:
        self.client = client

    async def try_call(self, tool: str, **arguments: object) -> Reply:
        result = await self.client.call_tool(tool, arguments)
        text = "".join(block.text for block in result.content if getattr(block, "type", None) == "text")
        return Reply(bool(result.is_error), text)

    async def call(self, tool: str, **arguments: object) -> dict:
        reply = await self.try_call(tool, **arguments)
        assert not reply.is_error, f"{tool} failed: {reply.text}"
        return reply.data

    async def name_is_unique(self, name: str) -> bool:
        """True when exactly one chat of the directory carries this name (what a name parameter needs)."""
        if not 2 <= len(name) <= 100:
            return False
        found = await self.call("find_chats", query=name, limit=50)
        wanted = directory.normalize(name)
        return sum(1 for chat in found["chats"] if directory.normalize(chat.get("name") or "") == wanted) == 1

    async def reference(self, chat_id: str, name: str | None) -> str:
        return name if name and await self.name_is_unique(name) else chat_id

    async def messages_of(self, chat: str, limit: int = 30) -> list[dict]:
        """The newest `limit` messages of a chat that are neither deleted nor reactions."""
        history = await self.call("read_messages", chat=chat, limit=limit)
        return [m for m in history["messages"] if not m.get("deleted") and m["type"] != "reaction"]


def _leaf(exc: BaseException) -> BaseException:
    """The one exception inside the task-group wrappers the MCP client adds around a failing test body."""
    while True:
        inner = getattr(exc, "exceptions", None)
        if not inner:
            return exc
        exc = inner[0]


@asynccontextmanager
async def session(subject_chat: str | None = None) -> AsyncIterator[tuple[Agent, Subject]]:
    """A client on `build_server(mode="local")` plus the chat under test, described by `get_chat`.

    The client wraps whatever the body raises (a `pytest.skip`, a failed assert) in exception groups; the wrapper
    is removed here so skips stay skips and failures keep their own traceback.
    """
    chat = subject_chat or _required("EVOLUTION_TEST_CHAT")
    try:
        async with Client(build_server(mode="local")) as client:  # type: ignore[arg-type]
            agent = Agent(client)
            status = await agent.call("get_instance_status")
            assert status["state"] == "open", f"the instance is {status['state']}, not open"
            card = await agent.call("get_chat", chat=chat)
            name = card.get("name")
            subject = Subject(
                chat_id=card["chat_id"],
                name=name,
                is_group=bool(card["is_group"]),
                integration=status["integration"],
                by_name=bool(name) and await agent.name_is_unique(name),
            )
            yield agent, subject
    except BaseException as exc:
        leaf = _leaf(exc)
        if leaf is exc:
            raise
        raise leaf from None


# --- helpers ---------------------------------------------------------------------------------------------------


def expect_keys(tool: str, data: dict, *keys: str) -> None:
    missing = [key for key in keys if key not in data]
    assert not missing, f"{tool}: missing {missing}; the result has {sorted(data)}"


def _midnight(days_ago: int) -> str:
    """Midnight, `days_ago` days back, in the server's display zone, without a zone suffix (as an owner says it)."""
    zone = clock.zone(context.local_config().timezone)
    return (datetime.now(zone) - timedelta(days=days_ago)).strftime("%Y-%m-%dT00:00:00")


def _moment(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _is_file(path: str) -> bool:
    return Path(path).is_file()


def _is_dir(path: str) -> bool:
    return Path(path).is_dir()


def _size(path: str) -> int:
    return Path(path).stat().st_size


async def _a_group(agent: Agent) -> tuple[str, str]:
    """A group of the instance as `(chat_id, how a scenario names it)`; skips when there is none."""
    listing = await agent.call("list_chats", kind="groups", limit=20)
    for chat in listing["chats"]:
        if chat.get("name"):
            return chat["chat_id"], await agent.reference(chat["chat_id"], chat["name"])
    pytest.skip("the instance has no named group chat")


def _skip_without_media(reply: Reply, what: str) -> None:
    if reply.is_error and any(marker in reply.text for marker in NO_MEDIA_MARKERS):
        pytest.skip(
            f"Evolution cannot fetch the file for the {what} (no S3/MinIO storage, or WhatsApp no longer serves it)"
        )


# --- reading -------------------------------------------------------------------------------------------------


async def test_s01_what_came_in_today(runtime: Path) -> None:
    async with session() as (agent, _):
        result = await agent.call("list_recent_messages", since=_midnight(0))
    expect_keys("list_recent_messages", result, "since", "until", "chats", "messages", "scanned", "complete")
    for chat in result["chats"]:
        expect_keys("list_recent_messages", chat, "chat_id", "is_group", "messages")
        assert chat["messages"]
        for message in chat["messages"]:
            expect_keys("list_recent_messages", message, *MESSAGE_KEYS)
    assert sum(len(chat["messages"]) for chat in result["chats"]) <= result["messages"]


async def test_s02_chats_waiting_for_a_reply(runtime: Path) -> None:
    async with session() as (agent, _):
        result = await agent.call("list_chats", waiting_for_reply=True)
    expect_keys("list_chats", result, "chats", "offset", "limit", "has_more", "scanned")
    for chat in result["chats"]:
        expect_keys("list_chats", chat, "chat_id", "is_group", "unread_count", "last_message")
        assert chat["last_message"] is not None
        assert chat["last_message"]["from_me"] is False


async def test_s03_unread_messages(runtime: Path) -> None:
    async with session() as (agent, _):
        result = await agent.call("list_recent_messages", only_unread=True)
    expect_keys("list_recent_messages", result, "chats", "messages", "scanned", "complete")
    assert "since" not in result, "only_unread ignores the period, so the result carries none"
    for chat in result["chats"]:
        expect_keys("list_recent_messages", chat, "chat_id", "is_group", "unread_count", "messages")
        assert chat["unread_count"] > 0
        assert chat["messages"]


async def test_s04_read_a_conversation_by_name(runtime: Path) -> None:
    async with session() as (agent, subject):
        subject.needs_name()
        result = await agent.call("read_messages", chat=subject.reference)
    expect_keys("read_messages", result, "chat_id", "page", "pages", "total", "messages")
    assert result["chat_id"] == subject.chat_id
    assert len(result["messages"]) <= 30
    for message in result["messages"]:
        expect_keys("read_messages", message, *MESSAGE_KEYS)


async def test_s05_who_is_this_lid(runtime: Path) -> None:
    async with session() as (agent, _):
        lid = None
        for offset in range(0, 400, 100):
            listing = await agent.call("list_chats", limit=100, offset=offset)
            lid = next((chat["chat_id"] for chat in listing["chats"] if chat["chat_id"].endswith("@lid")), None)
            if lid is not None or not listing["has_more"]:
                break
        if lid is None:
            pytest.skip("no @lid chat among the newest 400 chats of the instance")
        result = await agent.call("get_chat", chat=lid)
    expect_keys("get_chat", result, "chat_id", "is_group", "known", "unread_count", "labels", "contact_saved")
    assert result["chat_id"] == lid
    assert result["is_group"] is False


async def test_s06_groups_in_common(runtime: Path) -> None:
    async with session() as (agent, subject):
        if subject.integration != registry.BAILEYS:
            pytest.skip("groups in common need a WhatsApp Web (Baileys) instance")
        if subject.is_group:
            pytest.skip("the test chat is a group; groups in common describe a person")
        result = await agent.call("get_chat", chat=subject.needs_name())
    expect_keys("get_chat", result, "chat_id", "name")
    if "groups_in_common" not in result:
        pytest.skip(f"Evolution did not return its group list: {result.get('note')}")
    assert isinstance(result["groups_in_common"], list)
    for group in result["groups_in_common"]:
        expect_keys("get_chat", group, "group_id", "subject")
        assert group["group_id"].endswith("@g.us")


async def test_s07_find_the_pdf_a_contact_sent(runtime: Path) -> None:
    async with session() as (agent, subject):
        if subject.is_group:
            pytest.skip("the test chat is a group; a sender is a person")
        result = await agent.call("search_messages", sender=subject.needs_name(), message_type="document")
    expect_keys("search_messages", result, "query", "matches", "scanned", "complete")
    for match in result["matches"]:
        expect_keys("search_messages", match, *MESSAGE_KEYS)
        assert match["type"] == "document"
        assert match["from_me"] is False


async def test_s08_download_that_pdf(runtime: Path) -> None:
    async with session() as (agent, _):
        found = await agent.call("search_messages", message_type="document", direction="incoming", limit=5)
        if not found["matches"]:
            pytest.skip("the instance has no received document")
        last = None
        for match in found["matches"]:
            last = await agent.try_call("download_message_media", message_id=match["message_id"])
            if not last.is_error:
                break
        assert last is not None
        _skip_without_media(last, "documents")
        assert not last.is_error, f"download_message_media failed: {last.text}"
    result = last.data
    expect_keys("download_message_media", result, "message_id", "file_name", "mimetype", "size_bytes", "saved_to")
    assert _is_file(result["saved_to"])
    assert _size(result["saved_to"]) == result["size_bytes"]
    assert str(runtime / "downloads") in result["saved_to"]


async def test_s09_search_a_word_in_a_group(runtime: Path) -> None:
    async with session() as (agent, _):
        _, group = await _a_group(agent)
        result = await agent.call("search_messages", chat=group, query="fattura")
    expect_keys("search_messages", result, "query", "matches", "scanned", "complete")
    assert result["query"] == "fattura"
    for match in result["matches"]:
        expect_keys("search_messages", match, *MESSAGE_KEYS)


async def test_s10_context_around_a_hit(runtime: Path) -> None:
    async with session() as (agent, subject):
        history = await agent.messages_of(subject.reference, limit=10)
        if not history:
            pytest.skip("the test chat has no stored message to centre on")
        anchor = history[len(history) // 2]["message_id"]
        result = await agent.call("read_messages", chat=subject.reference, around_message_id=anchor, context=3)
    expect_keys("read_messages", result, "chat_id", "anchor", "messages", "before", "after")
    assert result["anchor"] == anchor
    flagged = [message for message in result["messages"] if message.get("anchor")]
    assert [message["message_id"] for message in flagged] == [anchor]
    assert len(result["messages"]) == result["before"] + result["after"] + 1


# --- acting ----------------------------------------------------------------------------------------------------


async def test_s11_reply_to_a_message_without_a_chat(runtime: Path) -> None:
    _require_send()
    async with session() as (agent, subject):
        history = await agent.messages_of(subject.chat_id, limit=20)
        if not history:
            pytest.skip("the test chat has no stored message to reply to")
        target = next((message for message in history if message["from_me"] is False), history[0])
        text = f"evolution-api-mcp scenario S11 {int(time.time())}"
        result = await agent.call("send_text_message", reply_to_message_id=target["message_id"], text=text)
    expect_keys("send_text_message", result, "message_id", "chat_id", "status")
    assert result["chat_id"] in {target["chat_id"], subject.chat_id}


async def test_s12_forward_a_photo(runtime: Path) -> None:
    _require_send()
    async with session() as (agent, subject):
        found = await agent.call("search_messages", chat=subject.chat_id, message_type="image", limit=1)
        if not found["matches"]:
            pytest.skip("the test chat holds no photo to forward")
        source = found["matches"][0]["message_id"]
        reply = await agent.try_call("forward_message", message_id=source, to=[subject.reference])
        _skip_without_media(reply, "photo")
        assert not reply.is_error, f"forward_message failed: {reply.text}"
    result = reply.data
    expect_keys("forward_message", result, "source_message_id", "type", "forwarded", "note")
    assert result["source_message_id"] == source
    assert result["type"] == "image"
    assert len(result["forwarded"]) == 1
    expect_keys("forward_message", result["forwarded"][0], "chat_id", "message_id", "status")
    assert result["forwarded"][0]["chat_id"] == subject.chat_id


async def test_s13_send_three_files(runtime: Path) -> None:
    _require_send()
    paths = []
    for number in (1, 2, 3):
        path = runtime / "files" / f"scenario-s13-{number}.txt"
        path.write_text(f"evolution-api-mcp scenario S13, file {number}\n", encoding="utf-8")
        paths.append(str(path))
    async with session() as (agent, subject):
        result = await agent.call("send_local_files", paths=paths, chat=subject.needs_name())
    expect_keys("send_local_files", result, "chat_id", "files")
    assert result["chat_id"] == subject.chat_id
    assert [entry["file"] for entry in result["files"]] == [Path(path).name for path in paths]
    for entry in result["files"]:
        expect_keys("send_local_files", entry, "file", "size_bytes", "message_id", "status")


async def test_s14_export_a_period_with_attachments(runtime: Path) -> None:
    _require_send()
    async with session() as (agent, subject):
        history = await agent.call("read_messages", chat=subject.chat_id, limit=1)
        if not history["messages"]:
            pytest.skip("the test chat has no stored message to export")
        since = (_moment(history["messages"][0]["timestamp"]) - timedelta(days=7)).isoformat()
        result = await agent.call("export_chat", chat=subject.reference, since=since)
    expect_keys(
        "export_chat",
        result,
        "chat_id",
        "folder",
        "transcript",
        "messages",
        "media_files",
        "media_skipped",
        "media_skipped_count",
        "size_bytes",
    )
    assert result["chat_id"] == subject.chat_id
    assert result["messages"] >= 1
    assert str(runtime / "downloads" / "exports") in result["folder"]
    assert _is_file(result["transcript"])
    assert result["media_skipped_count"] >= len(result["media_skipped"])


async def test_s15_every_document_of_a_chat(runtime: Path) -> None:
    _require_send()
    async with session() as (agent, subject):
        result = await agent.call("export_chat", chat=subject.reference, content="media", media_types=["document"])
    if result.get("messages") == 0:
        pytest.skip("the test chat has no stored message to export")
    expect_keys("export_chat", result, "chat_id", "folder", "messages", "media_files", "media_skipped_count")
    assert "transcript" not in result, "content=media writes no transcript"
    assert _is_dir(result["folder"])


# --- finding ---------------------------------------------------------------------------------------------------


async def test_s16_who_mentioned_me_in_the_groups(runtime: Path) -> None:
    async with session() as (agent, _):
        reply = await agent.try_call("list_recent_messages", since=_midnight(0), kind="groups", mentions_me=True)
    if reply.is_error and "mentions_me needs" in reply.text:
        pytest.skip("Evolution did not report this number's own id on this instance")
    assert not reply.is_error, f"list_recent_messages failed: {reply.text}"
    result = reply.data
    expect_keys("list_recent_messages", result, "chats", "messages", "scanned", "complete")
    for chat in result["chats"]:
        assert chat["is_group"] is True
        for message in chat["messages"]:
            assert message["mentions_me"] is True


async def test_s17_yesterdays_photos_in_a_group(runtime: Path) -> None:
    async with session() as (agent, _):
        _, group = await _a_group(agent)
        result = await agent.call(
            "search_messages", chat=group, message_type="image", since=_midnight(1), until=_midnight(0)
        )
    expect_keys("search_messages", result, "query", "matches", "scanned", "complete")
    for match in result["matches"]:
        assert match["type"] == "image"


async def test_s18_the_latest_links_i_received(runtime: Path) -> None:
    async with session() as (agent, _):
        result = await agent.call("search_messages", message_type="link", direction="incoming")
    expect_keys("search_messages", result, "query", "matches", "scanned", "complete")
    for match in result["matches"]:
        assert match["type"] == "text"
        assert match["from_me"] is False


async def test_s19_who_reacted_to_my_last_message(runtime: Path) -> None:
    async with session() as (agent, subject):
        subject.needs_name()
        result = await agent.call("read_messages", chat=subject.reference, limit=5)
    expect_keys("read_messages", result, "chat_id", "messages")
    assert len(result["messages"]) <= 5
    for message in result["messages"]:
        for reaction in message.get("reactions", []):
            expect_keys("read_messages", reaction, "emoji", "by")


async def test_s20_mark_a_chat_as_read(runtime: Path) -> None:
    _require_send()
    async with session() as (agent, subject):
        if subject.integration != registry.BAILEYS:
            pytest.skip("mark_chat_read needs a WhatsApp Web (Baileys) instance")
        result = await agent.call("mark_chat_read", chat=subject.needs_name())
    expect_keys("mark_chat_read", result, "chat_id", "marked")
    assert result["chat_id"] == subject.chat_id
    assert isinstance(result["marked"], int)


async def test_s21_someones_phone_number(runtime: Path) -> None:
    async with session() as (agent, subject):
        name = subject.needs_name()
        result = await agent.call("find_chats", query=name)
    expect_keys("find_chats", result, "query", "chats", "total")
    assert result["total"] >= len(result["chats"])
    for chat in result["chats"]:
        expect_keys("find_chats", chat, "chat_id", "kind")
        assert chat["kind"] in {"person", "group"}
    found = [chat for chat in result["chats"] if chat["chat_id"] == subject.chat_id]
    if not found:
        pytest.skip("the test chat's name is not in the name directory under its chat_id")
    if not subject.is_group:
        assert "phone" in found[0] or subject.chat_id.endswith("@lid")
