import base64
import json
import re
import zipfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx2
import pytest
from mcp_types import ImageContent, TextContent

from evolution_api_mcp import context, media, paths, tenant
from evolution_api_mcp.errors import ToolExecutionError
from evolution_api_mcp.registry import BUSINESS
from evolution_api_mcp.tools import chats
from tests.conftest import INSTANCE
from tests.fakes import program_directory

PERSON = "393331234567@s.whatsapp.net"
OTHER = "393339876543@s.whatsapp.net"
GROUP = "120363012345678901@g.us"
KEY = {"remoteJid": PERSON, "remoteJidAlt": PERSON}
TS = 1759180000
TS_ISO = "2025-09-29T21:06:40Z"

FIND_CHATS = f"/chat/findChats/{INSTANCE}"
FIND_CHAT = f"/chat/findChatByRemoteJid/{INSTANCE}"
FIND_CONTACTS = f"/chat/findContacts/{INSTANCE}"
FIND_MESSAGES = f"/chat/findMessages/{INSTANCE}"
FIND_STATUS = f"/chat/findStatusMessage/{INSTANCE}"
GET_BASE64 = f"/chat/getBase64FromMediaMessage/{INSTANCE}"
MARK_READ = f"/chat/markMessageAsRead/{INSTANCE}"
MARK_UNREAD = f"/chat/markChatUnread/{INSTANCE}"
ARCHIVE = f"/chat/archiveChat/{INSTANCE}"

NOT_FOUND_HINT = "read_messages, search_messages and list_recent_messages show message ids."


def _page(*records, total=None, pages=1, current=1):
    return {
        "messages": {
            "total": len(records) if total is None else total,
            "pages": pages,
            "currentPage": current,
            "records": list(records),
        }
    }


def _row(message_id, *, from_me=False, text="Hello", remote=PERSON, ts=TS, status="READ", push="Ana"):
    return {
        "id": f"row-{message_id}",
        "key": {"id": message_id, "remoteJid": remote, "fromMe": from_me},
        "pushName": push,
        "messageType": "conversation",
        "message": {"conversation": text},
        "messageTimestamp": ts,
        "MessageUpdate": [{"status": status}],
    }


def _image_row(message_id="3EB0IMG001", *, caption="Look at this", remote=PERSON):
    return {
        "id": f"row-{message_id}",
        "key": {"id": message_id, "remoteJid": remote, "fromMe": False},
        "pushName": "Ana",
        "messageType": "imageMessage",
        "message": {"imageMessage": {"mimetype": "image/jpeg", "caption": caption, "fileLength": 1234}},
        "messageTimestamp": TS,
    }


def _chat_row(jid, *, unread=0, name="Ana", updated="2026-09-29T21:04:05.000Z", last=None):
    row = {
        "id": "cuid",
        "remoteJid": jid,
        "pushName": name,
        "profilePicUrl": None,
        "updatedAt": updated,
        "windowStart": None,
        "windowExpires": None,
        "windowActive": False,
        "unreadCount": unread,
        "isSaved": True,
    }
    if last is not None:
        row["lastMessage"] = last
    return row


def _paged_chats(rows):
    def handler(request):
        skip = request.json.get("skip", 0)
        return 200, rows[skip : skip + request.json["take"]]

    return handler


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


@pytest.fixture(autouse=True)
def _empty_directory(evo):
    """Name-aware reads build the directory first; unless a test programs names, Evolution knows none."""
    program_directory(evo, INSTANCE)


# --- list_chats ------------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_chats_scans_in_pages_of_100_and_projects_rows(evo, bound):
    last = _row("3EB0LAST01", text="See you tomorrow", from_me=True)
    evo.on(
        "POST",
        FIND_CHATS,
        json=[_chat_row(PERSON, unread=3, last=last), _chat_row(GROUP, name="Family", updated=TS)],
    )
    with bound(evo):
        result = json.loads(await chats.list_chats(limit=20, offset=0))
    assert evo.last("POST", FIND_CHATS).json == {"take": 100, "skip": 0}
    assert result == {
        "chats": [
            {
                "chat_id": PERSON,
                "name": "Ana",
                "is_group": False,
                "unread_count": 3,
                "last_activity": "2026-09-29T21:04:05Z",
                "last_message": {
                    "message_id": "3EB0LAST01",
                    "from_me": True,
                    "type": "text",
                    "text": "See you tomorrow",
                    "timestamp": TS_ISO,
                },
            },
            {
                "chat_id": GROUP,
                "name": "Family",
                "is_group": True,
                "unread_count": 0,
                "last_activity": TS_ISO,
                "last_message": None,
            },
        ],
        "offset": 0,
        "limit": 20,
        "has_more": False,
        "scanned": 2,
    }


@pytest.mark.anyio
async def test_list_chats_shows_times_in_the_connection_zone(evo, bound):
    last = _row("3EB0LAST01", text="See you tomorrow", from_me=True)
    evo.on("POST", FIND_CHATS, json=[_chat_row(PERSON, last=last), _chat_row(GROUP, name="Family", updated=TS)])
    with bound(evo, timezone="Europe/Rome"):
        result = json.loads(await chats.list_chats(limit=20, offset=0))
    assert result["chats"][0]["last_activity"] == "2026-09-29T23:04:05+02:00"
    assert result["chats"][0]["last_message"]["timestamp"] == "2025-09-29T23:06:40+02:00"
    assert result["chats"][1]["last_activity"] == "2025-09-29T23:06:40+02:00"


@pytest.mark.anyio
async def test_list_chats_pages_with_offset_and_reports_has_more(evo, bound):
    rows = [_chat_row(f"39333000000{i}@s.whatsapp.net") for i in range(8)]
    evo.on("POST", FIND_CHATS, handler=_paged_chats(rows))
    with bound(evo):
        result = json.loads(await chats.list_chats(limit=2, offset=4))
    assert [r.json for r in evo.requests if r.path == FIND_CHATS] == [{"take": 100, "skip": 0}]
    assert [c["chat_id"] for c in result["chats"]] == [r["remoteJid"] for r in rows[4:6]]
    assert result["has_more"] is True
    assert result["offset"] == 4
    assert result["scanned"] == 8


@pytest.mark.anyio
async def test_list_chats_skips_noise_chats_but_counts_them_as_scanned(evo, bound):
    noise = ["status@broadcast", "0@s.whatsapp.net", "120363@newsletter", "1234@broadcast"]
    evo.on("POST", FIND_CHATS, json=[*(_chat_row(jid) for jid in noise), _chat_row(PERSON)])
    with bound(evo):
        result = json.loads(await chats.list_chats())
    assert [c["chat_id"] for c in result["chats"]] == [PERSON]
    assert result["scanned"] == 5
    assert result["has_more"] is False


@pytest.mark.anyio
async def test_list_chats_names_a_chat_from_the_directory_when_the_row_has_no_name(evo, bound):
    program_directory(evo, INSTANCE, contacts=[{"remoteJid": PERSON, "pushName": "Mario Rossi", "isSaved": True}])
    evo.on("POST", FIND_CHATS, json=[_chat_row(PERSON, name=""), _chat_row(OTHER, name="Luca")])
    with bound(evo):
        result = json.loads(await chats.list_chats())
    assert [c["name"] for c in result["chats"]] == ["Mario Rossi", "Luca"]


@pytest.mark.anyio
async def test_list_chats_waiting_for_reply_keeps_chats_whose_last_message_is_theirs(evo, bound):
    theirs = _row("3EB0THEIRS", text="Are you there?")
    mine = _row("3EB0MINE01", text="Yes", from_me=True)
    reaction = {**_row("3EB0REACT1"), "messageType": "reactionMessage", "message": {"reactionMessage": {"text": "x"}}}
    system = {**_row("3EB0SYS001"), "messageType": "protocolMessage", "message": {"protocolMessage": {}}}
    rows = [
        _chat_row(PERSON, last=theirs),
        _chat_row(OTHER, last=mine),
        _chat_row("393330000003@s.whatsapp.net", last=reaction),
        _chat_row("393330000004@s.whatsapp.net", last=system),
        _chat_row("393330000005@s.whatsapp.net"),
    ]
    evo.on("POST", FIND_CHATS, handler=_paged_chats(rows))
    with bound(evo):
        result = json.loads(await chats.list_chats(waiting_for_reply=True))
    assert [c["chat_id"] for c in result["chats"]] == [PERSON]
    assert result["scanned"] == 5


@pytest.mark.anyio
async def test_list_chats_active_since_sends_both_window_ends(evo, bound):
    evo.on("POST", FIND_CHATS, json=[])
    with bound(evo):
        result = json.loads(await chats.list_chats(active_since=datetime(2026, 9, 1, tzinfo=timezone.utc)))
    window = evo.last("POST", FIND_CHATS).json["where"]["messageTimestamp"]
    assert window["gte"] == "2026-09-01T00:00:00Z"
    assert window["lte"].endswith("Z")
    assert result["chats"] == []
    assert result["has_more"] is False


@pytest.mark.anyio
async def test_list_chats_empty_answer_is_an_empty_list(evo, bound):
    evo.on("POST", FIND_CHATS, json=None)
    with bound(evo):
        result = json.loads(await chats.list_chats())
    assert result["chats"] == []


@pytest.mark.anyio
async def test_list_chats_only_unread_scans_pages_until_enough_matches(evo, bound):
    rows = [_chat_row(f"39333{i:07d}@s.whatsapp.net", unread=1 if i % 5 == 0 else 0) for i in range(250)]
    evo.on("POST", FIND_CHATS, handler=_paged_chats(rows))
    with bound(evo):
        result = json.loads(await chats.list_chats(limit=5, only_unread=True))
    bodies = [r.json for r in evo.requests if r.path == FIND_CHATS]
    assert bodies == [{"take": 100, "skip": 0}]
    assert [c["chat_id"] for c in result["chats"]] == [rows[i]["remoteJid"] for i in (0, 5, 10, 15, 20)]
    assert result["has_more"] is True
    assert result["scanned"] == 100
    assert "note" not in result


@pytest.mark.anyio
async def test_list_chats_kind_filter_applies_before_offset_and_limit(evo, bound):
    # Even positions are groups: 250 chats hold 125 groups.
    rows = [_chat_row(f"1203630{i:08d}@g.us" if i % 2 == 0 else f"39333{i:07d}@s.whatsapp.net") for i in range(250)]
    evo.on("POST", FIND_CHATS, handler=_paged_chats(rows))
    with bound(evo):
        result = json.loads(await chats.list_chats(limit=10, offset=45, kind="groups"))
    bodies = [r.json for r in evo.requests if r.path == FIND_CHATS]
    assert bodies == [{"take": 100, "skip": 0}, {"take": 100, "skip": 100}]
    groups = [r["remoteJid"] for r in rows if r["remoteJid"].endswith("@g.us")]
    assert [c["chat_id"] for c in result["chats"]] == groups[45:55]
    assert all(c["is_group"] for c in result["chats"])
    assert result["has_more"] is True
    assert result["scanned"] == 200


@pytest.mark.anyio
async def test_list_chats_people_filter_drops_groups(evo, bound):
    evo.on("POST", FIND_CHATS, json=[_chat_row(GROUP, name="Family"), _chat_row(PERSON)])
    with bound(evo):
        result = json.loads(await chats.list_chats(kind="people"))
    assert [c["chat_id"] for c in result["chats"]] == [PERSON]
    assert result["has_more"] is False
    assert result["scanned"] == 2


@pytest.mark.anyio
async def test_list_chats_filtered_scan_stops_at_2000_rows_with_a_note(evo, bound):
    rows = [_chat_row(f"39333{i:07d}@s.whatsapp.net") for i in range(2500)]
    evo.on("POST", FIND_CHATS, handler=_paged_chats(rows))
    with bound(evo):
        result = json.loads(await chats.list_chats(only_unread=True))
    assert len([r for r in evo.requests if r.path == FIND_CHATS]) == 20
    assert result["chats"] == []
    assert result["scanned"] == 2000
    assert result["has_more"] is False
    assert result["note"] == "Only the newest 2000 chats were scanned; narrow with active_since to look further back."


@pytest.mark.anyio
async def test_list_chats_filtered_scan_ends_on_a_short_page_without_a_note(evo, bound):
    rows = [_chat_row(f"39333{i:07d}@s.whatsapp.net", unread=1) for i in range(90)]
    evo.on("POST", FIND_CHATS, handler=_paged_chats(rows))
    with bound(evo):
        result = json.loads(await chats.list_chats(limit=40, only_unread=True))
    assert len([r for r in evo.requests if r.path == FIND_CHATS]) == 1
    assert len(result["chats"]) == 40
    assert result["has_more"] is True
    assert result["scanned"] == 90
    assert "note" not in result


@pytest.mark.anyio
async def test_list_chats_unreachable_server_is_a_tool_error(evo, bound):
    evo.fail("POST", FIND_CHATS, httpx2.ConnectError("refused"))
    with bound(evo), pytest.raises(ToolExecutionError, match="Could not reach Evolution"):
        await chats.list_chats()


def _group_row(message_id, *, participant=PERSON, text="Hi all", ts=TS, from_me=False, mentioned=(), push="Ana"):
    row = _row(message_id, text=text, remote=GROUP, ts=ts, from_me=from_me, push=push)
    row["key"]["participant"] = participant
    if mentioned:
        row["contextInfo"] = {"mentionedJid": list(mentioned)}
    return row


def _reaction_row(message_id, target, emoji, *, participant, push, ts, remote=GROUP):
    row = _row(message_id, remote=remote, ts=ts, push=push)
    row["key"]["participant"] = participant
    row["messageType"] = "reactionMessage"
    row["message"] = {"reactionMessage": {"text": emoji, "key": {"id": target}}}
    return row


def _recent_handler(rows):
    """findMessages over `rows` (newest first), paged by the request; the directory's own sample stays empty."""

    def handler(request):
        if not request.json["where"]:
            return 200, _page()
        page, size = request.json["page"], request.json["offset"]
        start = (page - 1) * size
        return 200, _page(*rows[start : start + size], total=len(rows), pages=-(-len(rows) // size), current=page)

    return handler


@pytest.mark.anyio
async def test_list_recent_messages_defaults_to_the_last_24_hours_with_both_window_ends(evo, bound):
    evo.on("POST", FIND_MESSAGES, handler=_recent_handler([]))
    with bound(evo):
        result = json.loads(await chats.list_recent_messages())
    (body,) = _message_calls(evo)
    window = body["where"]["messageTimestamp"]
    wire = "%Y-%m-%dT%H:%M:%SZ"
    assert datetime.strptime(window["lte"], wire) - datetime.strptime(window["gte"], wire) == timedelta(hours=24)
    assert "key" not in body["where"]
    assert (body["offset"], body["page"]) == (100, 1)
    assert result == {
        "since": window["gte"],
        "until": window["lte"],
        "chats": [],
        "messages": 0,
        "scanned": 0,
        "complete": True,
    }


@pytest.mark.anyio
async def test_list_recent_messages_reads_a_period_without_a_zone_in_the_display_zone(evo, bound):
    evo.on("POST", FIND_MESSAGES, handler=_recent_handler([_row("3EB0AAAA01", ts=1790000000)]))
    with bound(evo, timezone="Europe/Rome"):
        result = json.loads(
            await chats.list_recent_messages(since=datetime(2026, 10, 2, 0, 0), until=datetime(2026, 10, 2, 12, 0))
        )
    (body,) = _message_calls(evo)
    assert body["where"]["messageTimestamp"] == {"gte": "2026-10-01T22:00:00Z", "lte": "2026-10-02T10:00:00Z"}
    assert (result["since"], result["until"]) == ("2026-10-02T00:00:00+02:00", "2026-10-02T12:00:00+02:00")
    assert result["chats"][0]["messages"][0]["timestamp"].endswith("+02:00")


@pytest.mark.anyio
async def test_list_recent_messages_groups_by_chat_in_first_seen_order_and_caps_each_chat(evo, bound):
    program_directory(evo, INSTANCE, contacts=[{"remoteJid": PERSON, "pushName": "Mario Rossi", "isSaved": True}])
    third = "393330000009@s.whatsapp.net"
    rows = [
        _row("3EB0A000001", remote=PERSON, ts=TS + 9),
        _row("3EB0B000001", remote=OTHER, ts=TS + 8),
        _row("3EB0A000002", remote=PERSON, ts=TS + 7),
        _row("3EB0A000003", remote=PERSON, ts=TS + 6),
        _row("3EB0B000002", remote=OTHER, ts=TS + 5),
        _row("3EB0C000001", remote=third, ts=TS + 4, push=""),
    ]
    evo.on("POST", FIND_MESSAGES, handler=_recent_handler(rows))
    with bound(evo):
        result = json.loads(await chats.list_recent_messages(per_chat=2))
    assert [c["chat_id"] for c in result["chats"]] == [PERSON, OTHER, third]
    assert [m["message_id"] for m in result["chats"][0]["messages"]] == ["3EB0A000001", "3EB0A000002"]
    assert result["chats"][0]["chat_name"] == "Mario Rossi"
    assert "chat_name" not in result["chats"][2]
    assert result["chats"][0]["is_group"] is False
    assert all("chat_id" not in m for chat in result["chats"] for m in chat["messages"])
    assert result["messages"] == 5
    assert result["scanned"] == 6
    assert result["complete"] is True


@pytest.mark.anyio
async def test_list_recent_messages_stops_at_the_limit_without_reading_more_pages(evo, bound):
    rows = [_row(f"3EB0{i:07d}", remote=PERSON, ts=TS + 500 - i) for i in range(300)]
    evo.on("POST", FIND_MESSAGES, handler=_recent_handler(rows))
    with bound(evo):
        result = json.loads(await chats.list_recent_messages(limit=3, per_chat=20))
    assert len(_message_calls(evo)) == 1
    assert result["messages"] == 3
    assert [m["message_id"] for m in result["chats"][0]["messages"]] == [f"3EB0{i:07d}" for i in range(3)]
    assert result["complete"] is True


@pytest.mark.anyio
async def test_list_recent_messages_skips_noise_chats_and_folds_reactions(evo, bound):
    rows = [
        _row("3EB0STATUS1", remote="status@broadcast", ts=TS + 9),
        _reaction_row(
            "3EB0REACT01", "3EB0AAAA01", "\U0001f44d", participant=PERSON, push="Ana", ts=TS + 5, remote=PERSON
        ),
        _row("3EB0AAAA01", remote=PERSON, ts=TS, from_me=True),
    ]
    rows[1]["key"].pop("participant")
    evo.on("POST", FIND_MESSAGES, handler=_recent_handler(rows))
    with bound(evo):
        result = json.loads(await chats.list_recent_messages())
    assert [c["chat_id"] for c in result["chats"]] == [PERSON]
    (message,) = result["chats"][0]["messages"]
    assert message["message_id"] == "3EB0AAAA01"
    assert message["reactions"] == [{"emoji": "\U0001f44d", "by": "Ana"}]
    assert result["messages"] == 1
    assert result["scanned"] == 3


@pytest.mark.anyio
async def test_list_recent_messages_outgoing_pushes_from_me_to_evolution(evo, bound):
    evo.on("POST", FIND_MESSAGES, handler=_recent_handler([_row("3EB0MINE01", from_me=True)]))
    with bound(evo):
        result = json.loads(await chats.list_recent_messages(direction="outgoing"))
    (body,) = _message_calls(evo)
    assert body["where"]["key"] == {"fromMe": True}
    assert result["chats"][0]["messages"][0]["from_me"] is True


@pytest.mark.anyio
async def test_list_recent_messages_incoming_filters_on_the_client_side(evo, bound):
    rows = [_row("3EB0MINE01", from_me=True, ts=TS + 2), _row("3EB0THEIRS1", ts=TS + 1)]
    evo.on("POST", FIND_MESSAGES, handler=_recent_handler(rows))
    with bound(evo):
        result = json.loads(await chats.list_recent_messages(direction="incoming"))
    (body,) = _message_calls(evo)
    assert "key" not in body["where"]
    assert [m["message_id"] for c in result["chats"] for m in c["messages"]] == ["3EB0THEIRS1"]
    assert result["scanned"] == 2


@pytest.mark.anyio
async def test_list_recent_messages_kind_keeps_people_or_groups(evo, bound):
    rows = [_group_row("3EB0GROUP01", ts=TS + 1), _row("3EB0PERSON1", ts=TS)]
    evo.on("POST", FIND_MESSAGES, handler=_recent_handler(rows))
    with bound(evo):
        groups = json.loads(await chats.list_recent_messages(kind="groups"))
        people = json.loads(await chats.list_recent_messages(kind="people"))
    assert [c["chat_id"] for c in groups["chats"]] == [GROUP]
    assert groups["chats"][0]["is_group"] is True
    assert [c["chat_id"] for c in people["chats"]] == [PERSON]


@pytest.mark.anyio
async def test_list_recent_messages_mentions_me_keeps_group_messages_that_mention_this_number(evo, bound):
    me = "393930000000@s.whatsapp.net"
    rows = [
        _group_row("3EB0MENTION1", ts=TS + 3, mentioned=[me, OTHER]),
        _group_row("3EB0MENTION2", ts=TS + 2, mentioned=[OTHER]),
        _group_row("3EB0PLAIN001", ts=TS + 1),
        _row("3EB0PERSON1", ts=TS),
    ]
    evo.on("POST", FIND_MESSAGES, handler=_recent_handler(rows))
    with bound(evo):
        result = json.loads(await chats.list_recent_messages(mentions_me=True))
    (message,) = [m for c in result["chats"] for m in c["messages"]]
    assert message["message_id"] == "3EB0MENTION1"
    assert message["mentions_me"] is True
    assert result["scanned"] == 4


@pytest.mark.anyio
async def test_list_recent_messages_mentions_me_needs_this_numbers_own_id(evo, bound):
    program_directory(evo, INSTANCE, instance_row={"name": INSTANCE})
    evo.on("POST", FIND_MESSAGES, handler=_recent_handler([]))
    with bound(evo), pytest.raises(ToolExecutionError, match="mentions_me needs this number's own id"):
        await chats.list_recent_messages(mentions_me=True)
    assert _message_calls(evo) == []


@pytest.mark.anyio
async def test_list_recent_messages_refuses_an_empty_or_reversed_period(evo, bound):
    with bound(evo):
        with pytest.raises(ToolExecutionError, match=r"^since must be earlier than until\.$"):
            await chats.list_recent_messages(
                since=datetime(2026, 10, 2, tzinfo=timezone.utc), until=datetime(2026, 10, 1, tzinfo=timezone.utc)
            )
        with pytest.raises(ToolExecutionError, match=r"^since must be earlier than until\.$"):
            await chats.list_recent_messages(since=datetime.now(timezone.utc) + timedelta(days=1))
    assert evo.requests == []


@pytest.mark.anyio
async def test_list_recent_messages_scan_stops_at_1000_rows_with_a_note(evo, bound):
    rows = [_row(f"3EB0{i:07d}", remote=PERSON, ts=TS + 5000 - i) for i in range(1200)]
    evo.on("POST", FIND_MESSAGES, handler=_recent_handler(rows))
    with bound(evo):
        result = json.loads(await chats.list_recent_messages(kind="groups"))
    assert len(_message_calls(evo)) == 10
    assert result["chats"] == []
    assert result["scanned"] == 1000
    assert result["complete"] is False
    assert result["note"] == "Stopped after scanning 1000 messages; narrow since, until or kind to see older ones."


@pytest.mark.anyio
async def test_list_recent_messages_scanning_exactly_the_cap_with_nothing_left_is_complete(evo, bound):
    rows = [_row(f"3EB0{i:07d}", remote=PERSON, ts=TS + 5000 - i) for i in range(1000)]
    evo.on("POST", FIND_MESSAGES, handler=_recent_handler(rows))
    with bound(evo):
        result = json.loads(await chats.list_recent_messages(kind="groups"))
    assert result["scanned"] == 1000
    assert result["complete"] is True
    assert "note" not in result


@pytest.mark.anyio
async def test_list_recent_messages_only_unread_reads_the_newest_unread_messages_of_each_chat(evo, bound):
    chat_rows = [
        _chat_row(PERSON, unread=3),
        _chat_row(OTHER, unread=0),
        _chat_row("status@broadcast", unread=2),
        _chat_row(GROUP, unread=40, name="Family"),
    ]
    evo.on("POST", FIND_CHATS, handler=_paged_chats(chat_rows))

    def by_chat(request):
        where = request.json["where"]
        if not where:
            return 200, _page()
        chat = where["key"]["remoteJid"]
        size = request.json["offset"]
        pool = (
            [_row(f"3EB0P{i:06d}", remote=PERSON, ts=TS - i) for i in range(10)]
            if chat == PERSON
            else [_group_row(f"3EB0G{i:06d}", ts=TS - i) for i in range(10)]
        )
        return 200, _page(*pool[:size], total=len(pool))

    evo.on("POST", FIND_MESSAGES, handler=by_chat)
    with bound(evo):
        result = json.loads(
            await chats.list_recent_messages(
                only_unread=True, since=datetime(2020, 1, 1), until=datetime(2020, 1, 2), per_chat=5
            )
        )
    assert [r.json for r in evo.requests if r.path == FIND_CHATS] == [{"take": 100, "skip": 0}]
    key = lambda jid: {"remoteJid": jid, "remoteJidAlt": jid}  # noqa: E731
    assert _message_calls(evo) == [
        {"where": {"key": key(PERSON)}, "offset": 3, "page": 1},
        {"where": {"key": key(GROUP)}, "offset": 5, "page": 1},
    ]
    assert "since" not in result and "until" not in result
    assert [(c["chat_id"], c["unread_count"], len(c["messages"])) for c in result["chats"]] == [
        (PERSON, 3, 3),
        (GROUP, 40, 5),
    ]
    assert result["messages"] == 8
    assert result["scanned"] == 4
    assert result["complete"] is True


@pytest.mark.anyio
async def test_list_recent_messages_only_unread_takes_at_most_20_chats(evo, bound):
    chat_rows = [_chat_row(f"39333{i:07d}@s.whatsapp.net", unread=1) for i in range(25)]
    evo.on("POST", FIND_CHATS, handler=_paged_chats(chat_rows))
    evo.on(
        "POST",
        FIND_MESSAGES,
        handler=lambda request: (
            200,
            _page()
            if not request.json["where"]
            else _page(_row("3EB0X0000001", remote=request.json["where"]["key"]["remoteJid"])),
        ),
    )
    with bound(evo):
        result = json.loads(await chats.list_recent_messages(only_unread=True, limit=200, per_chat=1))
    assert len(_message_calls(evo)) == 20
    assert len(result["chats"]) == 20
    assert result["complete"] is False
    assert result["note"] == "More than 20 chats have unread messages; the 20 most recently active are shown."


# --- get_chat --------------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_chat_combines_chat_contact_and_last_message(evo, bound):
    evo.on(
        "GET",
        FIND_CHAT,
        json={"remoteJid": PERSON, "name": "Ana Chat", "labels": ["1", "4"], "unreadMessages": 2},
    )
    evo.on("POST", FIND_CONTACTS, json=[{"remoteJid": PERSON, "pushName": "Ana", "isSaved": True, "isGroup": False}])
    evo.on("POST", FIND_MESSAGES, json=_page(_row("3EB0LAST01", text="Ciao", ts=TS)))
    with bound(evo):
        result = json.loads(await chats.get_chat("+39 333 123 4567"))
    assert evo.last("GET", FIND_CHAT).params == {"remoteJid": PERSON}
    assert evo.last("POST", FIND_CONTACTS).json == {"where": {"remoteJid": PERSON}}
    assert evo.last("POST", FIND_MESSAGES).json == {"where": {"key": KEY}, "offset": 1, "page": 1}
    assert result == {
        "chat_id": PERSON,
        "is_group": False,
        "known": True,
        "name": "Ana Chat",
        "phone": "393331234567",
        "unread_count": 2,
        "labels": ["1", "4"],
        "contact_saved": True,
        "last_message": {
            "message_id": "3EB0LAST01",
            "from_me": False,
            "type": "text",
            "text": "Ciao",
            "timestamp": TS_ISO,
        },
        "groups_in_common": [],
    }


@pytest.mark.anyio
async def test_get_chat_unknown_chat_is_reported_as_not_known(evo, bound):
    evo.on("GET", FIND_CHAT, text="null")
    evo.on("POST", FIND_CONTACTS, json=[])
    evo.on("POST", FIND_MESSAGES, json=_page())
    with bound(evo):
        result = json.loads(await chats.get_chat(GROUP))
    assert result == {
        "chat_id": GROUP,
        "is_group": True,
        "known": False,
        "name": None,
        "unread_count": 0,
        "labels": [],
        "contact_saved": False,
        "last_message": None,
    }


@pytest.mark.anyio
async def test_get_chat_falls_back_to_the_contact_name_and_knows_a_chat_with_messages(evo, bound):
    evo.on("GET", FIND_CHAT, text="null")
    evo.on("POST", FIND_CONTACTS, json=[{"remoteJid": PERSON, "pushName": "Ana", "isSaved": False}])
    evo.on("POST", FIND_MESSAGES, json=_page(_row("3EB0LAST01")))
    with bound(evo):
        result = json.loads(await chats.get_chat(PERSON))
    assert result["known"] is True
    assert result["name"] == "Ana"
    assert result["contact_saved"] is False


FETCH_GROUPS = f"/group/fetchAllGroups/{INSTANCE}"


def _group_with(group_id, subject, *participants):
    return {"id": group_id, "subject": subject, "participants": list(participants)}


def _groups_with_participants(groups):
    """fetchAllGroups: the groups with their participants when asked for them, none otherwise (the directory's call)."""
    return lambda request: (200, groups if request.params.get("getParticipants") == "true" else [])


@pytest.mark.anyio
async def test_get_chat_lists_the_groups_a_person_shares_with_this_number(evo, bound):
    _linked_directory(evo)
    evo.on("GET", FIND_CHAT, text="null")
    groups = [
        _group_with("120363000000000001@g.us", "Via phone number", {"id": LID, "phoneNumber": PERSON}),
        _group_with("120363000000000002@g.us", "Someone else", {"id": OTHER}),
        _group_with("120363000000000003@g.us", "Via phone id", {"id": PERSON}),
        _group_with("120363000000000004@g.us", "Via lid", {"id": LID}),
    ]
    evo.on("GET", FETCH_GROUPS, handler=_groups_with_participants(groups))
    with bound(evo):
        result = json.loads(await chats.get_chat("Ana Rossi"))
    assert evo.last("GET", FETCH_GROUPS).params == {"getParticipants": "true"}
    assert result["chat_id"] == PERSON
    assert result["name"] == "Ana Rossi"
    assert result["phone"] == "393331234567"
    assert result["groups_in_common"] == [
        {"group_id": "120363000000000001@g.us", "subject": "Via phone number"},
        {"group_id": "120363000000000003@g.us", "subject": "Via phone id"},
        {"group_id": "120363000000000004@g.us", "subject": "Via lid"},
    ]
    assert "groups_in_common_truncated" not in result


@pytest.mark.anyio
async def test_get_chat_of_an_lid_shows_the_phone_and_name_the_directory_learned(evo, bound):
    _linked_directory(evo)
    evo.on("GET", FIND_CHAT, text="null")
    evo.on("POST", FIND_CONTACTS, handler=lambda request: (200, [] if request.json["where"] else _contacts_of_ana()))
    evo.on(
        "GET",
        FETCH_GROUPS,
        handler=_groups_with_participants([_group_with("120363000000000001@g.us", "Shared", {"id": PERSON})]),
    )
    with bound(evo):
        result = json.loads(await chats.get_chat(LID))
    assert result["chat_id"] == LID
    assert result["name"] == "Ana Rossi"
    assert result["phone"] == "393331234567"
    assert result["groups_in_common"] == [{"group_id": "120363000000000001@g.us", "subject": "Shared"}]


def _contacts_of_ana():
    return [{"remoteJid": PERSON, "pushName": "Ana Rossi", "isSaved": True}]


@pytest.mark.anyio
async def test_get_chat_caps_groups_in_common_at_50_and_says_so(evo, bound):
    evo.on("GET", FIND_CHAT, text="null")
    groups = [_group_with(f"1203630000000{i:05d}@g.us", f"Group {i}", {"id": PERSON}) for i in range(51)]
    evo.on("GET", FETCH_GROUPS, handler=_groups_with_participants(groups))
    with bound(evo):
        result = json.loads(await chats.get_chat(PERSON))
    assert len(result["groups_in_common"]) == 50
    assert result["groups_in_common"][-1]["subject"] == "Group 49"
    assert result["groups_in_common_truncated"] is True


@pytest.mark.anyio
async def test_get_chat_without_the_group_list_omits_groups_in_common_and_says_why(evo, bound):
    evo.on("GET", FIND_CHAT, text="null")

    def refuse_participants(request):
        return (400, {"message": "no groups"}) if request.params.get("getParticipants") == "true" else (200, [])

    evo.on("GET", FETCH_GROUPS, handler=refuse_participants)
    with bound(evo):
        result = json.loads(await chats.get_chat(PERSON))
    assert "groups_in_common" not in result
    assert result["note"] == "Evolution did not return the group list, so groups in common are missing."


@pytest.mark.anyio
async def test_get_chat_asks_for_groups_in_common_only_for_a_person_on_baileys(evo, bound, make_identity):
    evo.on("GET", FIND_CHAT, text="null")
    with bound(evo, identity=make_identity(BUSINESS)):
        business = json.loads(await chats.get_chat(PERSON))
    with bound(evo):
        group = json.loads(await chats.get_chat(GROUP))
    assert "groups_in_common" not in business
    assert "groups_in_common" not in group
    assert not [r for r in evo.requests if r.path == FETCH_GROUPS and r.params.get("getParticipants") == "true"]


# --- read_messages ---------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_read_messages_maps_limit_and_page_and_filters_by_both_jid_keys(evo, bound):
    evo.on(
        "POST",
        FIND_MESSAGES,
        json=_page(
            _row("3EB0AAAA02", text="Second", ts=TS + 60, from_me=True),
            _row("3EB0AAAA01", text="First", ts=TS),
            total=75,
            pages=3,
            current=2,
        ),
    )
    with bound(evo):
        result = json.loads(await chats.read_messages("393331234567", limit=30, page=2))
    assert evo.last("POST", FIND_MESSAGES).json == {"where": {"key": KEY}, "offset": 30, "page": 2}
    assert result["chat_id"] == PERSON
    assert (result["page"], result["pages"], result["total"]) == (2, 3, 75)
    assert result["messages"] == [
        {
            "message_id": "3EB0AAAA02",
            "chat_id": PERSON,
            "from_me": True,
            "sender": "me",
            "timestamp": "2025-09-29T21:07:40Z",
            "type": "text",
            "text": "Second",
            "status": "READ",
        },
        {
            "message_id": "3EB0AAAA01",
            "chat_id": PERSON,
            "from_me": False,
            "sender": PERSON,
            "sender_name": "Ana",
            "timestamp": TS_ISO,
            "type": "text",
            "text": "First",
            "status": "READ",
        },
    ]
    assert "note" not in result


@pytest.mark.anyio
async def test_read_messages_takes_a_contact_name_and_a_unique_partial_name(evo, bound):
    program_directory(evo, INSTANCE, contacts=[{"remoteJid": PERSON, "pushName": "Mario Rossi", "isSaved": True}])
    evo.on("POST", FIND_MESSAGES, json=_page(_row("3EB0AAAA01", text="First", ts=TS, from_me=False)))
    with bound(evo):
        exact = json.loads(await chats.read_messages("mario rossi"))
        partial = json.loads(await chats.read_messages("rossi"))
    assert exact["chat_id"] == partial["chat_id"] == PERSON
    assert evo.last("POST", FIND_MESSAGES).json["where"] == {"key": KEY}


@pytest.mark.anyio
async def test_mark_chat_read_refuses_a_partial_name_and_changes_nothing(evo, bound):
    program_directory(evo, INSTANCE, contacts=[{"remoteJid": PERSON, "pushName": "Mario Rossi", "isSaved": True}])
    with bound(evo), pytest.raises(ToolExecutionError, match=r"named exactly 'Mario'.*Nothing was changed\.$"):
        await chats.mark_chat_read("Mario")
    assert MARK_READ not in [request.path for request in evo.requests]


@pytest.mark.anyio
async def test_read_messages_time_window_sends_both_ends(evo, bound):
    evo.on("POST", FIND_MESSAGES, json=_page(_row("3EB0AAAA01")))
    with bound(evo):
        await chats.read_messages(PERSON, since=datetime(2026, 9, 1, tzinfo=timezone.utc))
        first = evo.last("POST", FIND_MESSAGES).json["where"]
        await chats.read_messages(
            PERSON,
            since=datetime(2026, 9, 1, tzinfo=timezone.utc),
            until=datetime(2026, 9, 2, 12, 30, tzinfo=timezone.utc),
        )
        second = evo.last("POST", FIND_MESSAGES).json["where"]
    assert first["key"] == KEY
    assert first["messageTimestamp"]["gte"] == "2026-09-01T00:00:00Z"
    assert first["messageTimestamp"]["lte"].endswith("Z")
    assert second["messageTimestamp"] == {"gte": "2026-09-01T00:00:00Z", "lte": "2026-09-02T12:30:00Z"}


@pytest.mark.anyio
async def test_read_messages_without_history_explains_why(evo, bound):
    evo.on("POST", FIND_MESSAGES, json=_page(total=0, pages=0))
    with bound(evo):
        result = json.loads(await chats.read_messages(PERSON))
    assert result["messages"] == []
    assert result["total"] == 0
    assert result["note"] == (
        "No stored messages for this chat. Evolution keeps history only when DATABASE_SAVE_DATA_NEW_MESSAGE is "
        "enabled; older history needs DATABASE_SAVE_DATA_HISTORIC."
    )


@pytest.mark.anyio
async def test_read_messages_cuts_long_text_and_flags_it(evo, bound):
    evo.on("POST", FIND_MESSAGES, json=_page(_row("3EB0AAAA01", text="x" * 1600)))
    with bound(evo):
        result = json.loads(await chats.read_messages(PERSON))
    message = result["messages"][0]
    assert len(message["text"]) == 1500
    assert message["text_truncated"] is True


@pytest.mark.anyio
async def test_read_messages_names_senders_shows_quotes_and_folds_reactions(evo, bound):
    program_directory(
        evo,
        INSTANCE,
        contacts=[
            {"remoteJid": PERSON, "pushName": "Mario Rossi", "isSaved": True},
            {"remoteJid": OTHER, "pushName": "Marco Neri", "isSaved": True},
        ],
    )
    reaction = {
        "key": {"id": "3EB0REACT01", "remoteJid": PERSON, "fromMe": False},
        "pushName": "Ana",
        "messageType": "reactionMessage",
        "message": {"reactionMessage": {"text": "👍", "key": {"id": "3EB0AAAA03", "remoteJid": PERSON}}},
        "messageTimestamp": TS + 200,
    }
    answer = _row("3EB0AAAA03", text="Sure, 5pm", ts=TS + 120, from_me=True)
    answer["contextInfo"] = {
        "stanzaId": "3EB0AAAA01",
        "participant": OTHER,
        "quotedMessage": {"conversation": "Meet at 5?"},
    }
    evo.on(
        "POST",
        FIND_MESSAGES,
        json=_page(reaction, answer, _row("3EB0AAAA01", text="Meet at 5?", ts=TS, push="")),
    )
    with bound(evo):
        result = json.loads(await chats.read_messages(PERSON))
    assert [m["message_id"] for m in result["messages"]] == ["3EB0AAAA03", "3EB0AAAA01"]
    sent, received = result["messages"]
    assert sent["quoted"] == {"message_id": "3EB0AAAA01", "sender": "Marco Neri", "text": "Meet at 5?"}
    assert sent["reactions"] == [{"emoji": "👍", "by": "Ana"}]
    assert received["sender_name"] == "Mario Rossi"
    assert "chat_name" not in received


@pytest.mark.anyio
async def test_read_messages_server_error_is_a_read_failure(evo, bound):
    evo.on(
        "POST",
        FIND_MESSAGES,
        status=500,
        json={"status": 500, "error": "Internal Server Error", "response": {"message": "db down"}},
    )
    expected = r"Evolution failed \(HTTP 500\): db down\. Nothing was changed\."
    with bound(evo), pytest.raises(ToolExecutionError, match=expected):
        await chats.read_messages(PERSON)


@pytest.mark.anyio
async def test_read_messages_rejects_a_bad_chat_before_calling_evolution(evo, bound):
    with bound(evo), pytest.raises(ToolExecutionError, match="Unsupported chat id"):
        await chats.read_messages("x@foo.com")
    assert evo.requests == []


def _timeline(count):
    """`count` stored messages of PERSON; message i has timestamp TS + i, so a higher i is newer."""
    return [_row(f"3EB0T{i:05d}", text=f"message {i}", ts=TS + i) for i in range(count)]


def _timeline_handler(rows):
    """findMessages over `rows`: honours key.id, the messageTimestamp window and paging, newest first."""

    def handler(request):
        where, size, page = request.json["where"], request.json["offset"], request.json["page"]
        found = sorted(rows, key=lambda row: row["messageTimestamp"], reverse=True)
        wanted = where.get("key", {}).get("id")
        if wanted:
            found = [row for row in found if row["key"]["id"] == wanted]
        window = where.get("messageTimestamp")
        if window:

            def seconds(text):
                return int(datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())

            found = [
                row for row in found if seconds(window["gte"]) <= row["messageTimestamp"] <= seconds(window["lte"])
            ]
        start = (page - 1) * size
        return 200, _page(*found[start : start + size], total=len(found), pages=-(-len(found) // size), current=page)

    return handler


def _message_calls(evo):
    return [r.json for r in evo.requests if r.path == FIND_MESSAGES and r.json["where"]]


@pytest.mark.anyio
async def test_read_messages_around_a_message_shows_both_sides_newest_first_with_the_anchor_flagged(evo, bound):
    program_directory(evo, INSTANCE, contacts=[{"remoteJid": PERSON, "pushName": "Mario Rossi", "isSaved": True}])
    evo.on("POST", FIND_MESSAGES, handler=_timeline_handler(_timeline(30)))
    with bound(evo):
        result = json.loads(await chats.read_messages(PERSON, around_message_id="3EB0T00010", context=3))
    assert [m["message_id"] for m in result["messages"]] == [f"3EB0T{i:05d}" for i in (13, 12, 11, 10, 9, 8, 7)]
    assert [m.get("anchor") for m in result["messages"]] == [None, None, None, True, None, None, None]
    assert {k: v for k, v in result.items() if k != "messages"} == {
        "chat_id": PERSON,
        "chat_name": "Mario Rossi",
        "anchor": "3EB0T00010",
        "before": 3,
        "after": 3,
    }
    anchor_seconds = TS + 10
    stamp = datetime.fromtimestamp(anchor_seconds, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    key = {"remoteJid": PERSON, "remoteJidAlt": PERSON}
    lookup, older, newer = _message_calls(evo)
    assert lookup == {"where": {"key": {"id": "3EB0T00010", **key}}, "offset": 1, "page": 1}
    assert older["where"]["key"] == key
    assert older["where"]["messageTimestamp"] == {"gte": "1970-01-01T00:00:00Z", "lte": stamp}
    assert (older["offset"], older["page"]) == (4, 1)
    assert newer["where"]["messageTimestamp"]["gte"] == stamp
    assert (newer["offset"], newer["page"]) == (100, 1)


@pytest.mark.anyio
async def test_read_messages_around_reads_the_last_page_of_a_long_newer_side(evo, bound):
    evo.on("POST", FIND_MESSAGES, handler=_timeline_handler(_timeline(250)))
    with bound(evo):
        result = json.loads(await chats.read_messages(PERSON, around_message_id="3EB0T00005", context=3))
    assert [m["message_id"] for m in result["messages"]] == [f"3EB0T{i:05d}" for i in (8, 7, 6, 5, 4, 3, 2)]
    newer_pages = [c["page"] for c in _message_calls(evo) if c["offset"] == 100]
    assert newer_pages == [1, 3]


@pytest.mark.anyio
async def test_read_messages_around_adds_the_page_before_a_nearly_empty_last_page(evo, bound):
    # 201 messages are at or after the anchor: the last page holds only the anchor itself.
    evo.on("POST", FIND_MESSAGES, handler=_timeline_handler(_timeline(250)))
    with bound(evo):
        result = json.loads(await chats.read_messages(PERSON, around_message_id="3EB0T00049", context=3))
    assert [m["message_id"] for m in result["messages"]] == [f"3EB0T{i:05d}" for i in (52, 51, 50, 49, 48, 47, 46)]
    assert result["after"] == 3
    newer_pages = [c["page"] for c in _message_calls(evo) if c["offset"] == 100]
    assert newer_pages == [1, 3, 2]


@pytest.mark.anyio
async def test_read_messages_around_the_newest_message_has_only_older_neighbours(evo, bound):
    evo.on("POST", FIND_MESSAGES, handler=_timeline_handler(_timeline(10)))
    with bound(evo):
        result = json.loads(await chats.read_messages(PERSON, around_message_id="3EB0T00009", context=2))
    assert [m["message_id"] for m in result["messages"]] == ["3EB0T00009", "3EB0T00008", "3EB0T00007"]
    assert (result["after"], result["before"]) == (0, 2)


@pytest.mark.anyio
async def test_read_messages_around_deduplicates_messages_sharing_the_anchors_second(evo, bound):
    rows = [_row("3EB0SAME01", ts=TS), _row("3EB0SAME02", ts=TS), _row("3EB0SAME03", ts=TS)]
    evo.on("POST", FIND_MESSAGES, handler=_timeline_handler(rows))
    with bound(evo):
        result = json.loads(await chats.read_messages(PERSON, around_message_id="3EB0SAME02", context=5))
    ids = [m["message_id"] for m in result["messages"]]
    assert sorted(ids) == ["3EB0SAME01", "3EB0SAME02", "3EB0SAME03"]
    assert len(ids) == len(set(ids))


@pytest.mark.anyio
@pytest.mark.parametrize("extra", [{"page": 2}, {"since": datetime(2026, 9, 1)}, {"until": datetime(2026, 9, 1)}])
async def test_read_messages_around_cannot_be_combined_with_paging_or_a_period(evo, bound, extra):
    with (
        bound(evo),
        pytest.raises(ToolExecutionError, match="around_message_id cannot be combined with page, since or until."),
    ):
        await chats.read_messages(PERSON, around_message_id="3EB0T00001", **extra)
    assert evo.requests == []


@pytest.mark.anyio
async def test_read_messages_around_an_unknown_message_is_not_found_in_this_chat(evo, bound):
    evo.on("POST", FIND_MESSAGES, handler=_timeline_handler(_timeline(3)))
    with bound(evo), pytest.raises(ToolExecutionError, match="Message 3EB0NOPE01 was not found in this chat."):
        await chats.read_messages(PERSON, around_message_id="3EB0NOPE01")


# --- search_messages -------------------------------------------------------------------------------------------


def _search_pages(total, needles):
    """Evolution's findMessages over `total` stored messages; `needles` maps message position to its text."""

    def handler(request):
        page, size = request.json["page"], request.json["offset"]
        start = (page - 1) * size
        records = [
            _row(f"3EB0{i:06d}", text=needles.get(i, "nothing to see"), ts=TS - i)
            for i in range(start, min(start + size, total))
        ]
        return 200, _page(*records, total=total, pages=-(-total // size), current=page)

    return handler


@pytest.mark.anyio
async def test_search_messages_matches_text_and_captions_ignoring_case(evo, bound):
    image = _image_row("3EB0IMG001", caption="INVOICE photo")
    evo.on(
        "POST",
        FIND_MESSAGES,
        json=_page(_row("3EB0AAAA01", text="Please send the Invoice"), _row("3EB0AAAA02", text="hello"), image),
    )
    with bound(evo):
        result = json.loads(await chats.search_messages("invoice"))
    assert evo.last("POST", FIND_MESSAGES).json == {"where": {}, "offset": 100, "page": 1}
    assert [m["message_id"] for m in result["matches"]] == ["3EB0AAAA01", "3EB0IMG001"]
    assert result["matches"][1]["type"] == "image"
    assert result["scanned"] == 3
    assert result["complete"] is True
    assert "note" not in result
    assert result["query"] == "invoice"


@pytest.mark.anyio
async def test_search_messages_scopes_to_one_chat_and_time_window(evo, bound):
    evo.on("POST", FIND_MESSAGES, json=_page())
    with bound(evo):
        result = json.loads(
            await chats.search_messages(
                "invoice",
                chat="393331234567",
                since=datetime(2026, 9, 1, tzinfo=timezone.utc),
                until=datetime(2026, 9, 30, tzinfo=timezone.utc),
            )
        )
    assert evo.last("POST", FIND_MESSAGES).json["where"] == {
        "key": KEY,
        "messageTimestamp": {"gte": "2026-09-01T00:00:00Z", "lte": "2026-09-30T00:00:00Z"},
    }
    assert result["matches"] == []
    assert result["complete"] is True


@pytest.mark.anyio
async def test_search_messages_pages_forward_and_stops_when_the_limit_is_reached(evo, bound):
    needles = {5: "invoice one", 150: "invoice two", 320: "invoice three"}
    evo.on("POST", FIND_MESSAGES, handler=_search_pages(1000, needles))
    with bound(evo):
        result = json.loads(await chats.search_messages("invoice", limit=2))
    scans = [r.json["page"] for r in evo.requests if r.path == FIND_MESSAGES]
    scans = scans[1:]  # the first request builds the name directory
    assert scans == [1, 2]
    assert [m["message_id"] for m in result["matches"]] == ["3EB0000005", "3EB0000150"]
    assert result["scanned"] == 151
    assert result["complete"] is True


@pytest.mark.anyio
async def test_search_messages_scan_stops_at_2000_messages_with_a_note(evo, bound):
    evo.on("POST", FIND_MESSAGES, handler=_search_pages(5000, {4000: "invoice far back"}))
    with bound(evo):
        result = json.loads(await chats.search_messages("invoice"))
    assert len([r for r in evo.requests if r.path == FIND_MESSAGES]) == 21  # 20 scan pages and the directory's own
    assert result["matches"] == []
    assert result["scanned"] == 2000
    assert result["complete"] is False
    assert result["note"] == (
        "Scanned the newest 2000 candidate messages; narrow with chat, sender, message_type, since or until to "
        "search further back."
    )


@pytest.mark.anyio
async def test_search_messages_scanning_exactly_everything_is_complete(evo, bound):
    evo.on("POST", FIND_MESSAGES, handler=_search_pages(2000, {}))
    with bound(evo):
        result = json.loads(await chats.search_messages("invoice"))
    assert result["scanned"] == 2000
    assert result["complete"] is True
    assert "note" not in result


@pytest.mark.anyio
async def test_search_messages_cuts_matched_text_at_500_characters(evo, bound):
    evo.on("POST", FIND_MESSAGES, json=_page(_row("3EB0AAAA01", text="invoice " + "x" * 600)))
    with bound(evo):
        result = json.loads(await chats.search_messages("invoice"))
    match = result["matches"][0]
    assert len(match["text"]) == 500
    assert match["text_truncated"] is True


LID = "55512345678901@lid"


def _doc(message_id, name, ts, *, remote=PERSON, from_me=False, caption=None):
    document = {"mimetype": "application/pdf", "fileName": name, "fileLength": 9}
    if caption is not None:
        document["caption"] = caption
    return {
        "id": f"row-{message_id}",
        "key": {"id": message_id, "remoteJid": remote, "fromMe": from_me},
        "pushName": "Ana",
        "messageType": "documentMessage",
        "message": {"documentMessage": document},
        "messageTimestamp": ts,
    }


def _audio(message_id, ts, *, ptt):
    row = _row(message_id, ts=ts)
    row["messageType"] = "audioMessage"
    row["message"] = {"audioMessage": {"mimetype": "audio/ogg", "ptt": ptt}}
    return row


def _streams_handler(streams):
    """findMessages over named filters: `streams` pairs a `where` with its rows (newest first); others are empty."""
    table = {json.dumps(where, sort_keys=True): rows for where, rows in streams}

    def handler(request):
        rows = table.get(json.dumps(request.json["where"], sort_keys=True), [])
        size, page = request.json["offset"], request.json["page"]
        start = (page - 1) * size
        return 200, _page(*rows[start : start + size], total=len(rows), pages=-(-len(rows) // size), current=page)

    return handler


def _ids(result):
    return [m["message_id"] for m in result["matches"]]


@pytest.mark.anyio
async def test_search_messages_without_any_filter_is_refused(evo, bound):
    with bound(evo), pytest.raises(ToolExecutionError) as excinfo:
        await chats.search_messages()
    assert str(excinfo.value) == (
        "Give at least one of query, sender, message_type or file_name. "
        "list_recent_messages shows the newest messages without a filter."
    )
    assert not evo.requests


@pytest.mark.anyio
async def test_search_messages_file_name_applies_to_documents_only(evo, bound):
    with bound(evo), pytest.raises(ToolExecutionError) as excinfo:
        await chats.search_messages(file_name="fattura", message_type="image")
    assert str(excinfo.value) == "file_name applies to documents."


@pytest.mark.anyio
async def test_search_messages_sender_cannot_be_combined_with_outgoing(evo, bound):
    with bound(evo), pytest.raises(ToolExecutionError) as excinfo:
        await chats.search_messages(sender="393331234567", direction="outgoing")
    assert str(excinfo.value) == "sender names another person, and outgoing messages are this number's own."


@pytest.mark.anyio
async def test_search_messages_documents_read_both_raw_types_and_merge_newest_first(evo, bound):
    plain = {"messageType": "documentMessage"}
    wrapped = {"messageType": "documentWithCaptionMessage"}
    evo.on(
        "POST",
        FIND_MESSAGES,
        handler=_streams_handler(
            [
                (plain, [_doc("3EB0DOC300", "a.pdf", TS + 300), _doc("3EB0DOC100", "c.pdf", TS + 100)]),
                (wrapped, [_doc("3EB0DOC200", "b.pdf", TS + 200)]),
            ]
        ),
    )
    with bound(evo):
        result = json.loads(await chats.search_messages(message_type="document"))
    assert _message_calls(evo) == [
        {"where": plain, "offset": 100, "page": 1},
        {"where": wrapped, "offset": 100, "page": 1},
    ]
    assert _ids(result) == ["3EB0DOC300", "3EB0DOC200", "3EB0DOC100"]
    assert result["query"] is None
    assert result["scanned"] == 3
    assert result["complete"] is True


@pytest.mark.anyio
async def test_search_messages_file_name_implies_documents_and_matches_the_name_only(evo, bound):
    invoice = _doc("3EB0DOC001", "Fattura 2026.pdf", TS + 2, caption="see fattura")
    contract = _doc("3EB0DOC002", "Contratto.pdf", TS + 1, caption="fattura attached")
    evo.on(
        "POST",
        FIND_MESSAGES,
        handler=_streams_handler([({"messageType": "documentMessage"}, [invoice, contract])]),
    )
    with bound(evo):
        result = json.loads(await chats.search_messages(file_name="FATT"))
    assert [call["where"] for call in _message_calls(evo)] == [
        {"messageType": "documentMessage"},
        {"messageType": "documentWithCaptionMessage"},
    ]
    assert _ids(result) == ["3EB0DOC001"]
    assert result["matches"][0]["media"]["file_name"] == "Fattura 2026.pdf"


@pytest.mark.anyio
async def test_search_messages_query_also_matches_a_document_file_name(evo, bound):
    contract = _doc("3EB0DOC002", "Contratto.pdf", TS + 1)
    evo.on(
        "POST",
        FIND_MESSAGES,
        handler=_streams_handler([({"messageType": "documentMessage"}, [contract])]),
    )
    with bound(evo):
        result = json.loads(await chats.search_messages(query="contratto", message_type="document"))
    assert _ids(result) == ["3EB0DOC002"]


@pytest.mark.anyio
async def test_search_messages_splits_voice_notes_from_audio_by_the_ptt_flag(evo, bound):
    rows = [_audio("3EB0AUD003", TS + 3, ptt=True), _audio("3EB0AUD002", TS + 2, ptt=False)]
    evo.on("POST", FIND_MESSAGES, handler=_streams_handler([({"messageType": "audioMessage"}, rows)]))
    with bound(evo):
        voice = json.loads(await chats.search_messages(message_type="voice_note"))
        audio = json.loads(await chats.search_messages(message_type="audio"))
    assert {c["where"]["messageType"] for c in _message_calls(evo)} == {"audioMessage"}
    assert _ids(voice) == ["3EB0AUD003"]
    assert _ids(audio) == ["3EB0AUD002"]


@pytest.mark.anyio
async def test_search_messages_link_keeps_text_with_a_web_address(evo, bound):
    rows = [
        _row("3EB0LINK02", text="read https://example.com/a today", ts=TS + 2),
        _row("3EB0PLAIN1", text="no address here", ts=TS + 1),
    ]
    evo.on("POST", FIND_MESSAGES, handler=_streams_handler([({"messageType": "conversation"}, rows)]))
    with bound(evo):
        result = json.loads(await chats.search_messages(message_type="link"))
    assert [call["where"] for call in _message_calls(evo)] == [
        {"messageType": "conversation"},
        {"messageType": "extendedTextMessage"},
    ]
    assert _ids(result) == ["3EB0LINK02"]


@pytest.mark.anyio
async def test_search_messages_outgoing_is_pushed_to_evolution_and_incoming_is_filtered_here(evo, bound):
    mine = _row("3EB0MINE01", text="ciao from me", from_me=True, ts=TS + 2)
    theirs = _row("3EB0THEIR1", text="ciao from them", ts=TS + 1)
    evo.on(
        "POST",
        FIND_MESSAGES,
        handler=_streams_handler([({"key": {"fromMe": True}}, [mine]), ({}, [mine, theirs])]),
    )
    with bound(evo):
        outgoing = json.loads(await chats.search_messages(query="ciao", direction="outgoing"))
        incoming = json.loads(await chats.search_messages(query="ciao", direction="incoming"))
    assert _message_calls(evo)[0]["where"] == {"key": {"fromMe": True}}
    assert _ids(outgoing) == ["3EB0MINE01"]
    assert _ids(incoming) == ["3EB0THEIR1"]


@pytest.mark.anyio
async def test_search_messages_combines_chat_type_and_period_in_one_where(evo, bound):
    evo.on("POST", FIND_MESSAGES, json=_page())
    with bound(evo):
        await chats.search_messages(
            message_type="image",
            chat="393331234567",
            since=datetime(2026, 9, 1, tzinfo=timezone.utc),
            until=datetime(2026, 9, 30, tzinfo=timezone.utc),
        )
    assert _message_calls(evo) == [
        {
            "where": {
                "key": KEY,
                "messageType": "imageMessage",
                "messageTimestamp": {"gte": "2026-09-01T00:00:00Z", "lte": "2026-09-30T00:00:00Z"},
            },
            "offset": 100,
            "page": 1,
        }
    ]


def _linked_directory(evo):
    """Ana Rossi, known by her phone JID and, from a stored message, by an @lid id."""
    link = _row("3EB0LINK01", remote=LID, push=None)
    link["key"]["remoteJidAlt"] = PERSON
    program_directory(
        evo, INSTANCE, contacts=[{"remoteJid": PERSON, "pushName": "Ana Rossi", "isSaved": True}], rows=[link]
    )
    return link


@pytest.mark.anyio
async def test_search_messages_sender_reads_her_private_chat_and_her_group_messages_under_both_ids(evo, bound):
    link = _linked_directory(evo)
    private = {"key": KEY}
    as_phone = {"key": {"participant": PERSON}}
    as_lid = {"key": {"participant": LID}}
    evo.on(
        "POST",
        FIND_MESSAGES,
        handler=_streams_handler(
            [
                ({}, [link]),
                (private, [_row("3EB0PRIV01", ts=TS + 300), _row("3EB0MINE01", ts=TS + 250, from_me=True)]),
                (as_phone, [_group_row("3EB0GRP002", participant=PERSON, ts=TS + 200)]),
                (as_lid, [_group_row("3EB0GRP001", participant=LID, ts=TS + 280, push=None)]),
            ]
        ),
    )
    with bound(evo):
        result = json.loads(await chats.search_messages(sender="Ana Rossi"))
    assert [call["where"] for call in _message_calls(evo)] == [private, as_phone, as_lid]
    assert _ids(result) == ["3EB0PRIV01", "3EB0GRP001", "3EB0GRP002"]
    assert result["scanned"] == 4


@pytest.mark.anyio
async def test_search_messages_sender_in_a_group_is_pushed_as_the_participant_filter(evo, bound):
    link = _linked_directory(evo)
    evo.on("POST", FIND_MESSAGES, handler=_streams_handler([({}, [link])]))
    with bound(evo):
        await chats.search_messages(sender="Ana Rossi", chat=GROUP, query="hello")
    group = {"remoteJid": GROUP, "remoteJidAlt": GROUP}
    assert [call["where"] for call in _message_calls(evo)] == [
        {"key": group | {"participant": PERSON}},
        {"key": group | {"participant": LID}},
    ]


@pytest.mark.anyio
async def test_search_messages_on_business_filters_the_group_sender_here(evo, bound, make_identity):
    rows = [
        _group_row("3EB0GRP001", participant=PERSON, text="hello", ts=TS + 2),
        _group_row("3EB0GRP002", participant=OTHER, text="hello", ts=TS + 1),
    ]
    group = {"key": {"remoteJid": GROUP, "remoteJidAlt": GROUP}}
    evo.on("POST", FIND_MESSAGES, handler=_streams_handler([(group, rows)]))
    with bound(evo, identity=make_identity(BUSINESS)):
        result = json.loads(await chats.search_messages(sender="393331234567", chat=GROUP))
    assert [call["where"] for call in _message_calls(evo)] == [group]
    assert _ids(result) == ["3EB0GRP001"]


@pytest.mark.anyio
async def test_search_messages_sender_in_her_own_private_chat_keeps_only_her_messages(evo, bound):
    rows = [_row("3EB0THEIR1", ts=TS + 2), _row("3EB0MINE01", ts=TS + 1, from_me=True)]
    evo.on("POST", FIND_MESSAGES, handler=_streams_handler([({"key": KEY}, rows)]))
    with bound(evo):
        result = json.loads(await chats.search_messages(sender="393331234567", chat=PERSON))
    assert [call["where"] for call in _message_calls(evo)] == [{"key": KEY}]
    assert _ids(result) == ["3EB0THEIR1"]


@pytest.mark.anyio
async def test_search_messages_sender_in_another_private_chat_is_refused(evo, bound):
    with bound(evo), pytest.raises(ToolExecutionError) as excinfo:
        await chats.search_messages(sender="393331234567", chat=OTHER)
    assert str(excinfo.value) == "In a one-to-one chat the sender is that person or this number."


@pytest.mark.anyio
async def test_search_messages_streams_share_one_scan_budget_of_2000_messages(evo, bound):
    def handler(request):
        raw = request.json["where"].get("messageType")
        parity = {"documentMessage": 0, "documentWithCaptionMessage": 1}.get(raw)
        if parity is None:
            return 200, _page()
        page, size = request.json["page"], request.json["offset"]
        rows = [
            _doc(f"3EB0{parity}{i:06d}", "report.pdf", TS - 2 * i - parity)
            for i in range((page - 1) * size, min(page * size, 5000))
        ]
        return 200, _page(*rows, total=5000, pages=50, current=page)

    evo.on("POST", FIND_MESSAGES, handler=handler)
    with bound(evo):
        result = json.loads(await chats.search_messages(query="invoice", message_type="document"))
    assert result["matches"] == []
    assert result["scanned"] == 2000
    assert result["complete"] is False
    assert result["note"] == (
        "Scanned the newest 2000 candidate messages; narrow with chat, sender, message_type, since or until to "
        "search further back."
    )
    # Each stream is read page by page as the merge needs it: 10 pages for one, 11 for the other, and the directory's.
    assert len([r for r in evo.requests if r.path == FIND_MESSAGES]) == 22


# --- get_message -----------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_message_returns_the_full_text_and_the_raw_type(evo, bound):
    evo.on("POST", FIND_MESSAGES, json=_page(_row("3EB0AAAA01", text="y" * 5000)))
    with bound(evo):
        result = json.loads(await chats.get_message("3EB0AAAA01"))
    assert _message_calls(evo)[0] == {"where": {"key": {"id": "3EB0AAAA01"}}, "offset": 2, "page": 1}
    assert len(result["text"]) == 5000
    assert "text_truncated" not in result
    assert result["raw_type"] == "conversation"
    assert result["message_id"] == "3EB0AAAA01"


@pytest.mark.anyio
async def test_get_message_cuts_text_at_8000_characters(evo, bound):
    evo.on("POST", FIND_MESSAGES, json=_page(_row("3EB0AAAA01", text="y" * 8001)))
    with bound(evo):
        result = json.loads(await chats.get_message("3EB0AAAA01"))
    assert len(result["text"]) == 8000
    assert result["text_truncated"] is True


@pytest.mark.anyio
async def test_get_message_with_a_chat_filters_on_both_jid_keys(evo, bound):
    evo.on("POST", FIND_MESSAGES, json=_page(_row("3EB0AAAA01")))
    with bound(evo):
        await chats.get_message("3EB0AAAA01", chat=PERSON)
    assert _message_calls(evo)[0]["where"] == {"key": {"id": "3EB0AAAA01", **KEY}}


@pytest.mark.anyio
async def test_get_message_not_found_names_the_chat_scope(evo, bound):
    evo.on("POST", FIND_MESSAGES, json=_page())
    with bound(evo):
        with pytest.raises(ToolExecutionError) as anywhere:
            await chats.get_message("3EB0MISSING")
        with pytest.raises(ToolExecutionError) as in_chat:
            await chats.get_message("3EB0MISSING", chat=PERSON)
    hint = "read_messages, search_messages and list_recent_messages show message ids."
    assert str(anywhere.value) == f"Message 3EB0MISSING was not found. {hint}"
    assert str(in_chat.value) == f"Message 3EB0MISSING was not found in this chat. {hint}"


@pytest.mark.anyio
async def test_get_message_names_the_sender_from_the_directory(evo, bound):
    program_directory(evo, INSTANCE, contacts=[{"remoteJid": PERSON, "pushName": "Mario Rossi", "isSaved": True}])
    evo.on("POST", FIND_MESSAGES, json=_page(_row("3EB0AAAA01", push="")))
    with bound(evo):
        result = json.loads(await chats.get_message("3EB0AAAA01"))
    assert result["sender_name"] == "Mario Rossi"


@pytest.mark.anyio
async def test_get_message_refuses_an_id_shared_by_two_chats_until_a_chat_picks_one(evo, bound):
    evo.on("POST", FIND_MESSAGES, json=_page(_row("3EB0AAAA01"), _row("3EB0AAAA01", remote=OTHER)))
    with bound(evo):
        with pytest.raises(ToolExecutionError) as caught:
            await chats.get_message("3EB0AAAA01")
        evo.on("POST", FIND_MESSAGES, json=_page(_row("3EB0AAAA01")))
        picked = json.loads(await chats.get_message("3EB0AAAA01", chat=PERSON))
    assert str(caught.value) == f"Message 3EB0AAAA01 exists in 2 chats ({PERSON}, {OTHER}). Pass chat to pick one."
    assert picked["chat_id"] == PERSON


@pytest.mark.anyio
async def test_get_message_lists_the_newest_reaction_of_each_person(evo, bound):
    target = _group_row("3EB0TARGET", participant=PERSON, text="Dinner at 8?")
    reactions = [
        _reaction_row("3EB0R000003", "3EB0TARGET", "\u2764", participant=PERSON, push="Ana", ts=TS + 20),
        _reaction_row("3EB0R000002", "3EB0TARGET", "\U0001f602", participant=OTHER, push="Luca", ts=TS + 15),
        _reaction_row(
            "3EB0R000004", "3EB0TARGET", "", participant="393330000009@s.whatsapp.net", push="Bob", ts=TS + 12
        ),
        _reaction_row("3EB0R000001", "3EB0TARGET", "\U0001f44d", participant=PERSON, push="Ana", ts=TS + 10),
        _reaction_row("3EB0R000005", "3EB0OTHER01", "\U0001f525", participant=OTHER, push="Luca", ts=TS + 9),
    ]

    def lookup(request):
        where = request.json["where"]
        if where.get("messageType") == "reactionMessage":
            return 200, _page(*reactions)
        if where.get("key", {}).get("id") == "3EB0TARGET":
            return 200, _page(target)
        return 200, _page()

    evo.on("POST", FIND_MESSAGES, handler=lookup)
    with bound(evo):
        result = json.loads(await chats.get_message("3EB0TARGET"))
    assert evo.last("POST", FIND_MESSAGES).json == {
        "where": {"key": {"remoteJid": GROUP, "remoteJidAlt": GROUP}, "messageType": "reactionMessage"},
        "offset": 100,
        "page": 1,
    }
    assert result["reactions"] == [{"emoji": "\u2764", "by": "Ana"}, {"emoji": "\U0001f602", "by": "Luca"}]
    assert result["text"] == "Dinner at 8?"


@pytest.mark.anyio
async def test_get_message_without_reactions_has_no_reactions_key(evo, bound):
    evo.on("POST", FIND_MESSAGES, json=_page(_row("3EB0AAAA01")))
    with bound(evo):
        result = json.loads(await chats.get_message("3EB0AAAA01"))
    assert "reactions" not in result


@pytest.mark.anyio
async def test_get_message_reaction_lookup_failure_is_a_read_error(evo, bound):
    def lookup(request):
        if request.json["where"].get("messageType") == "reactionMessage":
            return 500, {"message": "boom"}
        return 200, _page(_row("3EB0AAAA01"))

    evo.on("POST", FIND_MESSAGES, handler=lookup)
    with bound(evo), pytest.raises(ToolExecutionError):
        await chats.get_message("3EB0AAAA01")


# --- get_message_status ----------------------------------------------------------------------------------------


def _status(status, participant=None):
    return {"keyId": "3EB0AAAA01", "remoteJid": GROUP, "fromMe": True, "participant": participant, "status": status}


@pytest.mark.anyio
async def test_get_message_status_reports_the_furthest_status(evo, bound):
    evo.on(
        "POST",
        FIND_STATUS,
        json=[
            _status("READ", "391110001111@s.whatsapp.net"),
            _status("PLAYED", "391110002222@s.whatsapp.net"),
            _status("DELIVERY_ACK"),
            _status("SERVER_ACK"),
        ],
    )
    with bound(evo):
        result = json.loads(await chats.get_message_status("3EB0AAAA01", chat=GROUP))
    assert evo.last("POST", FIND_STATUS).json == {
        "where": {"remoteJid": GROUP, "id": "3EB0AAAA01"},
        "offset": 100,
        "page": 1,
    }
    assert result["latest"] == "PLAYED"
    assert result["updates"] == [
        {"status": "READ", "participant": "391110001111@s.whatsapp.net"},
        {"status": "PLAYED", "participant": "391110002222@s.whatsapp.net"},
        {"status": "DELIVERY_ACK"},
        {"status": "SERVER_ACK"},
    ]
    assert result["message_id"] == "3EB0AAAA01"
    assert result["legend"].startswith("SERVER_ACK = reached WhatsApp;")


@pytest.mark.anyio
async def test_get_message_status_ranks_error_below_pending_and_pending_below_ack(evo, bound):
    evo.on("POST", FIND_STATUS, json=[_status("ERROR"), _status("PENDING")])
    with bound(evo):
        assert json.loads(await chats.get_message_status("3EB0AAAA01", chat=PERSON))["latest"] == "PENDING"
        evo.on("POST", FIND_STATUS, json=[_status("PENDING"), _status("ERROR"), _status("SERVER_ACK")])
        assert json.loads(await chats.get_message_status("3EB0AAAA01", chat=PERSON))["latest"] == "SERVER_ACK"


@pytest.mark.anyio
async def test_get_message_status_deleted_wins_over_read(evo, bound):
    evo.on("POST", FIND_STATUS, json=[_status("READ"), _status("DELETED")])
    with bound(evo):
        result = json.loads(await chats.get_message_status("3EB0AAAA01", chat=PERSON))
    assert result["latest"] == "DELETED"


@pytest.mark.anyio
async def test_get_message_status_without_records_uses_the_stored_message_status(evo, bound):
    evo.on("POST", FIND_STATUS, json=[])
    evo.on("POST", FIND_MESSAGES, json=_page(_row("3EB0AAAA01", from_me=True, status="DELIVERY_ACK")))
    with bound(evo):
        result = json.loads(await chats.get_message_status("3EB0AAAA01", chat=PERSON))
    assert evo.last("POST", FIND_MESSAGES).json["where"] == {"key": {"id": "3EB0AAAA01", **KEY}}
    assert result["latest"] == "DELIVERY_ACK"
    assert result["updates"] == []


@pytest.mark.anyio
async def test_get_message_status_of_an_unknown_message_is_unknown(evo, bound):
    evo.on("POST", FIND_STATUS, json=[])
    evo.on("POST", FIND_MESSAGES, json=_page())
    with bound(evo):
        result = json.loads(await chats.get_message_status("3EB0AAAA01", chat=PERSON))
    assert result["latest"] == "unknown"


@pytest.mark.anyio
async def test_get_message_status_without_a_chat_filters_by_message_id_only(evo, bound):
    evo.on("POST", FIND_STATUS, json=[_status("DELIVERY_ACK")])
    with bound(evo):
        result = json.loads(await chats.get_message_status("3EB0AAAA01"))
    assert evo.last("POST", FIND_STATUS).json == {"where": {"id": "3EB0AAAA01"}, "offset": 100, "page": 1}
    assert result["latest"] == "DELIVERY_ACK"


@pytest.mark.anyio
async def test_get_message_status_without_records_refuses_an_id_shared_by_two_chats(evo, bound):
    evo.on("POST", FIND_STATUS, json=[])
    evo.on("POST", FIND_MESSAGES, json=_page(_row("3EB0AAAA01"), _row("3EB0AAAA01", remote=GROUP)))
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await chats.get_message_status("3EB0AAAA01")
    assert str(caught.value) == f"Message 3EB0AAAA01 exists in 2 chats ({PERSON}, {GROUP}). Pass chat to pick one."


# --- view_message_image ----------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_view_message_image_on_baileys_sends_only_the_message_id(evo, bound):
    evo.on("POST", FIND_MESSAGES, json=_page(_image_row()))
    evo.on(
        "POST",
        GET_BASE64,
        json={
            "mediaType": "imageMessage",
            "fileName": "photo.jpg",
            "caption": "Look at this",
            "mimetype": "image/jpeg",
            "base64": _b64(b"jpeg-bytes"),
        },
    )
    with bound(evo):
        result = await chats.view_message_image("3EB0IMG001")
    assert evo.last("POST", GET_BASE64).json == {"message": {"key": {"id": "3EB0IMG001"}}}
    assert isinstance(result[0], TextContent)
    assert json.loads(result[0].text) == {
        "message_id": "3EB0IMG001",
        "mimetype": "image/jpeg",
        "size_bytes": len(b"jpeg-bytes"),
        "caption": "Look at this",
    }
    assert isinstance(result[1], ImageContent)
    assert result[1].data == _b64(b"jpeg-bytes")
    assert result[1].mime_type == "image/jpeg"
    assert len(result) == 2


@pytest.mark.anyio
async def test_view_message_image_on_business_sends_the_stored_row(evo, bound, make_identity):
    row = _image_row()
    evo.on("POST", FIND_MESSAGES, json=_page(row))
    evo.on("POST", GET_BASE64, json={"mediaType": "imageMessage", "mimetype": "image/jpeg", "base64": _b64(b"jpeg")})
    with bound(evo, identity=make_identity(BUSINESS)):
        result = await chats.view_message_image("3EB0IMG001", chat=PERSON)
    assert evo.last("POST", GET_BASE64).json == {
        "message": {"key": row["key"], "messageType": "imageMessage", "message": row["message"]}
    }
    assert json.loads(result[0].text)["caption"] == "Look at this"
    assert result[1].data == _b64(b"jpeg")


@pytest.mark.anyio
async def test_view_message_image_refuses_a_message_that_is_not_an_image(evo, bound):
    evo.on("POST", FIND_MESSAGES, json=_page(_row("3EB0AAAA01")))
    with bound(evo), pytest.raises(ToolExecutionError) as excinfo:
        await chats.view_message_image("3EB0AAAA01")
    assert str(excinfo.value) == "Message 3EB0AAAA01 is a text, not an image; download_message_media saves any media."
    assert all(r.path != GET_BASE64 for r in evo.requests)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "answer", [{"text": "null"}, {"json": None}, {"json": {"base64": None}}, {"json": {"base64": ""}}]
)
async def test_view_message_image_refuses_an_answer_without_media(evo, bound, answer):
    evo.on("POST", FIND_MESSAGES, json=_page(_image_row()))
    evo.on("POST", GET_BASE64, status=201, **answer)
    with bound(evo), pytest.raises(ToolExecutionError) as excinfo:
        await chats.view_message_image("3EB0IMG001")
    assert str(excinfo.value) == "Evolution returned no media for message 3EB0IMG001."


@pytest.mark.anyio
async def test_view_message_image_refuses_images_above_4_mib(evo, bound):
    evo.on("POST", FIND_MESSAGES, json=_page(_image_row()))
    big = b"\0" * (media.MAX_INLINE_IMAGE_BYTES + 1)
    evo.on("POST", GET_BASE64, json={"mimetype": "image/jpeg", "base64": _b64(big)})
    with bound(evo), pytest.raises(ToolExecutionError) as excinfo:
        await chats.view_message_image("3EB0IMG001")
    assert str(excinfo.value) == (
        f"The image is {len(big)} bytes, above the 4 MiB inline limit; download_message_media saves it instead."
    )


@pytest.mark.anyio
async def test_view_message_image_accepts_exactly_4_mib(evo, bound):
    evo.on("POST", FIND_MESSAGES, json=_page(_image_row()))
    exact = b"\0" * media.MAX_INLINE_IMAGE_BYTES
    evo.on("POST", GET_BASE64, json={"mimetype": "image/jpeg", "base64": _b64(exact)})
    with bound(evo):
        result = await chats.view_message_image("3EB0IMG001")
    assert json.loads(result[0].text)["size_bytes"] == media.MAX_INLINE_IMAGE_BYTES


@pytest.mark.anyio
async def test_view_message_image_not_found_on_business_mentions_media_storage(evo, bound, make_identity):
    evo.on("POST", FIND_MESSAGES, json=_page())
    with bound(evo, identity=make_identity(BUSINESS)), pytest.raises(ToolExecutionError) as excinfo:
        await chats.view_message_image("3EB0IMG001")
    assert str(excinfo.value) == (
        f"Message 3EB0IMG001 was not found. {NOT_FOUND_HINT} "
        "On WhatsApp Business Platform instances Evolution stores received media only when its S3/MinIO "
        "storage is enabled."
    )


@pytest.mark.anyio
async def test_view_message_image_not_found_on_baileys_has_no_storage_hint(evo, bound):
    evo.on("POST", FIND_MESSAGES, json=_page())
    with bound(evo), pytest.raises(ToolExecutionError) as excinfo:
        await chats.view_message_image("3EB0IMG001")
    assert str(excinfo.value) == f"Message 3EB0IMG001 was not found. {NOT_FOUND_HINT}"


# --- download_message_media ------------------------------------------------------------------------------------


def _document_row(message_id="3EB0DOC001"):
    return {
        "id": f"row-{message_id}",
        "key": {"id": message_id, "remoteJid": PERSON, "fromMe": False},
        "messageType": "documentMessage",
        "message": {"documentMessage": {"mimetype": "application/pdf", "fileName": "invoice.pdf", "fileLength": 9}},
        "messageTimestamp": TS,
    }


@pytest.mark.anyio
async def test_download_message_media_writes_the_file_locally(evo, bound, monkeypatch, tmp_path):
    monkeypatch.setattr(context, "local_config", lambda: SimpleNamespace(download_dir=tmp_path / "downloads"))
    evo.on("POST", FIND_MESSAGES, json=_page(_document_row()))
    evo.on(
        "POST",
        GET_BASE64,
        json={
            "mediaType": "documentMessage",
            "fileName": "invoice.pdf",
            "mimetype": "application/pdf",
            "base64": _b64(b"%PDF-1.4 x"),
        },
    )
    with bound(evo):
        result = json.loads(await chats.download_message_media("3EB0DOC001"))
    assert evo.last("POST", GET_BASE64).json == {"message": {"key": {"id": "3EB0DOC001"}}}
    saved = tmp_path / "downloads" / "393331234567_3EB0DOC001_invoice.pdf"
    assert saved.read_bytes() == b"%PDF-1.4 x"
    assert result == {
        "message_id": "3EB0DOC001",
        "file_name": "393331234567_3EB0DOC001_invoice.pdf",
        "mimetype": "application/pdf",
        "size_bytes": 10,
        "saved_to": str(saved),
    }


@pytest.mark.anyio
async def test_download_message_media_names_a_file_without_a_name_after_its_mimetype(evo, bound, monkeypatch, tmp_path):
    monkeypatch.setattr(context, "local_config", lambda: SimpleNamespace(download_dir=tmp_path))
    evo.on("POST", FIND_MESSAGES, json=_page(_image_row()))
    evo.on("POST", GET_BASE64, json={"mimetype": "image/jpeg", "base64": _b64(b"jpeg")})
    with bound(evo):
        result = json.loads(await chats.download_message_media("3EB0IMG001"))
    saved = tmp_path / result["file_name"]
    assert saved.read_bytes() == b"jpeg"
    assert result["file_name"].startswith("393331234567_3EB0IMG001_media.")


@pytest.mark.anyio
async def test_download_message_media_on_business_sends_the_stored_row(
    evo, bound, make_identity, monkeypatch, tmp_path
):
    monkeypatch.setattr(context, "local_config", lambda: SimpleNamespace(download_dir=tmp_path))
    row = _document_row()
    evo.on("POST", FIND_MESSAGES, json=_page(row))
    evo.on("POST", GET_BASE64, json={"mimetype": "application/pdf", "base64": _b64(b"pdf")})
    with bound(evo, identity=make_identity(BUSINESS)):
        await chats.download_message_media("3EB0DOC001", chat=PERSON)
    assert evo.last("POST", GET_BASE64).json == {
        "message": {"key": row["key"], "messageType": "documentMessage", "message": row["message"]}
    }


@pytest.mark.anyio
async def test_download_message_media_hosted_publishes_a_link_after_writing_the_file(evo, bound, monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "_data_dir_override", tmp_path)
    monkeypatch.setattr(tenant, "_public_url", "https://mcp.example.test")
    evo.on("POST", FIND_MESSAGES, json=_page(_document_row()))
    evo.on(
        "POST",
        GET_BASE64,
        json={"mimetype": "application/pdf", "fileName": "invoice.pdf", "base64": _b64(b"%PDF-1.4 x")},
    )
    with bound(evo, mode="hosted", subject="t_abc123"):
        result = json.loads(await chats.download_message_media("3EB0DOC001"))
    assert result["message_id"] == "3EB0DOC001"
    assert result["file_name"] == "393331234567_3EB0DOC001_invoice.pdf"
    assert result["size_bytes"] == 10
    assert result["download_url"].startswith("https://mcp.example.test/files/")
    assert result["expires_at"]
    assert "saved_to" not in result
    token = result["download_url"].rsplit("/", 1)[1]
    published = tmp_path / "files" / "t_abc123" / token / result["file_name"]
    assert published.read_bytes() == b"%PDF-1.4 x"
    # The scratch directory is gone once the file has moved.
    assert list((tmp_path / "files" / "t_abc123" / "tmp").iterdir()) == []


@pytest.mark.anyio
async def test_download_message_media_refuses_types_without_media(evo, bound, monkeypatch, tmp_path):
    monkeypatch.setattr(context, "local_config", lambda: SimpleNamespace(download_dir=tmp_path))
    evo.on("POST", FIND_MESSAGES, json=_page(_row("3EB0AAAA01")))
    with bound(evo), pytest.raises(ToolExecutionError) as excinfo:
        await chats.download_message_media("3EB0AAAA01")
    assert str(excinfo.value) == "Message 3EB0AAAA01 is a text message and carries no downloadable media."
    assert all(r.path != GET_BASE64 for r in evo.requests)


@pytest.mark.anyio
async def test_download_message_media_refuses_an_empty_answer(evo, bound, monkeypatch, tmp_path):
    monkeypatch.setattr(context, "local_config", lambda: SimpleNamespace(download_dir=tmp_path))
    evo.on("POST", FIND_MESSAGES, json=_page(_document_row()))
    evo.on("POST", GET_BASE64, status=201, text="null")
    with bound(evo), pytest.raises(ToolExecutionError) as excinfo:
        await chats.download_message_media("3EB0DOC001")
    assert str(excinfo.value) == "Evolution returned no media for message 3EB0DOC001."
    assert list(tmp_path.iterdir()) == []


@pytest.mark.anyio
async def test_download_message_media_refuses_files_above_the_limit(evo, bound, monkeypatch, tmp_path):
    monkeypatch.setattr(context, "local_config", lambda: SimpleNamespace(download_dir=tmp_path))
    monkeypatch.setattr(media, "MAX_FILE_BYTES", 8)
    evo.on("POST", FIND_MESSAGES, json=_page(_document_row()))
    evo.on("POST", GET_BASE64, json={"mimetype": "application/pdf", "base64": _b64(b"123456789")})
    with bound(evo), pytest.raises(ToolExecutionError) as excinfo:
        await chats.download_message_media("3EB0DOC001")
    assert str(excinfo.value) == "The file is 9 bytes; the limit is 104857600 bytes (100 MiB)."
    assert list(tmp_path.iterdir()) == []


@pytest.mark.anyio
async def test_download_message_media_refuses_unreadable_base64(evo, bound, monkeypatch, tmp_path):
    monkeypatch.setattr(context, "local_config", lambda: SimpleNamespace(download_dir=tmp_path))
    evo.on("POST", FIND_MESSAGES, json=_page(_document_row()))
    evo.on("POST", GET_BASE64, json={"mimetype": "application/pdf", "base64": "not base64 !!"})
    with bound(evo), pytest.raises(ToolExecutionError, match="unreadable media for message 3EB0DOC001"):
        await chats.download_message_media("3EB0DOC001")


# --- mark_chat_read --------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_mark_chat_read_marks_the_given_received_messages(evo, bound):
    def lookup(request):
        message_id = request.json["where"]["key"]["id"]
        return 200, _page(_row(message_id))

    evo.on("POST", FIND_MESSAGES, handler=lookup)
    evo.on("POST", MARK_READ, status=201, json={"message": "Read messages", "read": "success"})
    with bound(evo):
        result = json.loads(await chats.mark_chat_read(PERSON, message_ids=["3EB0AAAA01", "3EB0AAAA02", "3EB0AAAA01"]))
    assert evo.last("POST", MARK_READ).json == {
        "readMessages": [
            {"id": "3EB0AAAA01", "fromMe": False, "remoteJid": PERSON},
            {"id": "3EB0AAAA02", "fromMe": False, "remoteJid": PERSON},
        ]
    }
    assert result == {"chat_id": PERSON, "marked": 2}


@pytest.mark.anyio
async def test_mark_chat_read_refuses_a_message_sent_from_this_number(evo, bound):
    evo.on("POST", FIND_MESSAGES, json=_page(_row("3EB0AAAA01", from_me=True)))
    with bound(evo), pytest.raises(ToolExecutionError) as excinfo:
        await chats.mark_chat_read(PERSON, message_ids=["3EB0AAAA01"])
    assert str(excinfo.value) == "Only received messages can be marked read."
    assert all(r.path != MARK_READ for r in evo.requests)


@pytest.mark.anyio
async def test_mark_chat_read_refuses_an_unknown_message_id(evo, bound):
    evo.on("POST", FIND_MESSAGES, json=_page())
    with bound(evo), pytest.raises(ToolExecutionError) as excinfo:
        await chats.mark_chat_read(PERSON, message_ids=["3EB0MISSING"])
    assert str(excinfo.value) == (
        f"Message 3EB0MISSING was not found in this chat. {NOT_FOUND_HINT} Nothing was changed."
    )


@pytest.mark.anyio
async def test_mark_chat_read_without_ids_marks_the_received_ones_among_the_newest_50(evo, bound):
    evo.on(
        "POST",
        FIND_MESSAGES,
        json=_page(
            _row("3EB0AAAA03", from_me=True),
            _row("3EB0AAAA02"),
            _row("3EB0AAAA01"),
        ),
    )
    evo.on("POST", MARK_READ, status=201, json={"message": "Read messages", "read": "success"})
    with bound(evo):
        result = json.loads(await chats.mark_chat_read(PERSON))
    assert evo.last("POST", FIND_MESSAGES).json == {"where": {"key": KEY}, "offset": 50, "page": 1}
    assert evo.last("POST", MARK_READ).json == {
        "readMessages": [
            {"id": "3EB0AAAA02", "fromMe": False, "remoteJid": PERSON},
            {"id": "3EB0AAAA01", "fromMe": False, "remoteJid": PERSON},
        ]
    }
    assert result == {"chat_id": PERSON, "marked": 2}


@pytest.mark.anyio
async def test_mark_chat_read_with_nothing_received_does_not_call_evolution(evo, bound):
    evo.on("POST", FIND_MESSAGES, json=_page(_row("3EB0AAAA01", from_me=True)))
    with bound(evo):
        result = json.loads(await chats.mark_chat_read(PERSON))
    assert result == {"chat_id": PERSON, "marked": 0}
    assert all(r.path != MARK_READ for r in evo.requests)


@pytest.mark.anyio
async def test_mark_chat_read_lost_answer_is_uncertain(evo, bound):
    evo.on("POST", FIND_MESSAGES, json=_page(_row("3EB0AAAA01")))
    evo.fail("POST", MARK_READ, httpx2.ReadTimeout("slow"))
    with bound(evo), pytest.raises(ToolExecutionError, match="UNCERTAIN: Evolution did not confirm the result"):
        await chats.mark_chat_read(PERSON)


# --- mark_chat_unread ------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_mark_chat_unread_sends_the_latest_message_key_and_timestamp(evo, bound):
    evo.on("POST", FIND_MESSAGES, json=_page(_row("3EB0AAAA09", from_me=True, ts={"low": TS, "high": 0})))
    evo.on("POST", MARK_UNREAD, status=201, json={"chatId": PERSON, "markedChatUnread": True})
    with bound(evo):
        result = json.loads(await chats.mark_chat_unread("393331234567"))
    assert evo.last("POST", FIND_MESSAGES).json == {"where": {"key": KEY}, "offset": 1, "page": 1}
    assert evo.last("POST", MARK_UNREAD).json == {
        "chat": PERSON,
        "lastMessage": {"key": {"id": "3EB0AAAA09", "remoteJid": PERSON, "fromMe": True}, "messageTimestamp": TS},
    }
    assert result == {"chat_id": PERSON, "unread": True}


@pytest.mark.anyio
async def test_mark_chat_unread_without_messages_is_refused_before_calling_evolution(evo, bound):
    evo.on("POST", FIND_MESSAGES, json=_page())
    with bound(evo), pytest.raises(ToolExecutionError) as excinfo:
        await chats.mark_chat_unread(PERSON)
    assert str(excinfo.value) == "This chat has no stored messages, so there is nothing to mark unread."
    assert all(r.path != MARK_UNREAD for r in evo.requests)


# --- set_chat_archived -----------------------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize("archived", [True, False])
async def test_set_chat_archived_sends_the_flag_and_the_chat(evo, bound, archived):
    evo.on("POST", ARCHIVE, status=201, json={"chatId": PERSON, "archived": True})
    with bound(evo):
        result = json.loads(await chats.set_chat_archived("+39 333 123 4567", archived=archived))
    assert evo.last("POST", ARCHIVE).json == {"archive": archived, "chat": PERSON}
    assert result == {"chat_id": PERSON, "archived": archived}


@pytest.mark.anyio
async def test_set_chat_archived_without_stored_messages_is_a_clean_refusal(evo, bound):
    evo.on(
        "POST",
        ARCHIVE,
        status=500,
        json={
            "status": 500,
            "error": "Internal Server Error",
            "response": {
                "message": [
                    {
                        "archived": False,
                        "message": [
                            "An error occurred while archiving the chat. Open a calling.",
                            "NotFoundException: Messages not found",
                        ],
                    }
                ],
            },
        },
    )
    with bound(evo), pytest.raises(ToolExecutionError) as excinfo:
        await chats.set_chat_archived(PERSON, archived=True)
    assert str(excinfo.value) == "This chat has no stored messages, so it cannot be archived. Nothing was changed."


@pytest.mark.anyio
async def test_set_chat_archived_other_server_errors_are_uncertain(evo, bound):
    evo.on(
        "POST",
        ARCHIVE,
        status=500,
        json={"status": 500, "error": "Internal Server Error", "response": {"message": "boom"}},
    )
    with bound(evo), pytest.raises(ToolExecutionError, match="UNCERTAIN: Evolution did not confirm the result"):
        await chats.set_chat_archived(PERSON, archived=True)


# --- export_chat -----------------------------------------------------------------------------------------------

ME = "393930000000@s.whatsapp.net"
EXPORT_FOLDER = "Mario Rossi_start_now"
EXPORT_PHOTO = "20260701-120130_3EB0EXP002_menu.jpg"
EXPORT_MARKDOWN = (
    "# Mario Rossi\n"
    "\n"
    "Chat 393331234567@s.whatsapp.net \u00b7 exported <now> \u00b7 2026-07-01T12:00:00+02:00 \u2013 "
    "2026-07-02T00:30:00+02:00 \u00b7 3 messages\n"
    "\n"
    "## 2026-07-01\n"
    "\n"
    "**12:00** Mario Rossi: Dinner at 8?\n"
    "Bring the wine\n"
    "> Me: Table booked\n"
    "\n"
    "**12:01** Me: Menu\n"
    f"Attachment: [{EXPORT_PHOTO}](media/{EXPORT_PHOTO})\n"
    "\n"
    "## 2026-07-02\n"
    "\n"
    "**00:30** Me: (message deleted)\n"
)
EXPORT_TXT = (
    "[01/07/26, 12:00:00] Mario Rossi: Dinner at 8?\n"
    "Bring the wine\n"
    f"[01/07/26, 12:01:30] Giulia: \u200e<attached: {EXPORT_PHOTO}>\n"
    "Menu\n"
    "[02/07/26, 00:30:00] Giulia: \u200eThis message was deleted.\n"
)


def _export_rows():
    """Three stored messages of PERSON, oldest first: a quoted text, an image with a caption, a deleted message."""
    quoted = _row("3EB0EXP001", text="Dinner at 8?\nBring the wine", ts=1782900000, push="Mario Rossi")
    quoted["contextInfo"] = {
        "stanzaId": "3EB0OLD001",
        "participant": ME,
        "quotedMessage": {"conversation": "Table booked"},
    }
    photo = {
        "id": "row-3EB0EXP002",
        "key": {"id": "3EB0EXP002", "remoteJid": PERSON, "fromMe": True},
        "messageType": "imageMessage",
        "message": {"imageMessage": {"mimetype": "image/jpeg", "caption": "Menu"}},
        "messageTimestamp": 1782900090,
    }
    deleted = _row("3EB0EXP003", from_me=True, text="oops", ts=1782945000)
    deleted["key"]["deleted"] = True
    return [quoted, photo, deleted]


def _document(message_id, ts, *, name="report.pdf"):
    return {
        "id": f"row-{message_id}",
        "key": {"id": message_id, "remoteJid": PERSON, "fromMe": False},
        "messageType": "documentMessage",
        "message": {"documentMessage": {"mimetype": "application/pdf", "fileName": name}},
        "messageTimestamp": ts,
    }


def _export_handler(rows):
    """findMessages over `rows`; the directory's own unfiltered sample stays empty."""
    stored = _timeline_handler(rows)
    return lambda request: (200, _page()) if not request.json["where"] else stored(request)


def _export_setup(evo, monkeypatch, tmp_path, rows, *, media=True):
    monkeypatch.setattr(context, "local_config", lambda: SimpleNamespace(download_dir=tmp_path / "dl"))
    program_directory(
        evo,
        INSTANCE,
        contacts=[{"remoteJid": PERSON, "pushName": "Mario Rossi", "isSaved": True}],
        instance_row={"name": INSTANCE, "ownerJid": "393930000000:7@s.whatsapp.net", "profileName": "Giulia"},
    )
    evo.on("POST", FIND_MESSAGES, handler=_export_handler(rows))
    if media:
        evo.on("POST", GET_BASE64, json={"fileName": "menu.jpg", "mimetype": "image/jpeg", "base64": _b64(b"JPEG")})


def _folder(tmp_path, name=EXPORT_FOLDER):
    return tmp_path / "dl" / "exports" / name


def _tree(root):
    return sorted(str(path.relative_to(root)) for path in root.rglob("*") if path.is_file())


@pytest.mark.anyio
async def test_export_chat_writes_markdown_and_the_attachment_in_a_folder_named_after_the_chat(
    evo, bound, monkeypatch, tmp_path
):
    _export_setup(evo, monkeypatch, tmp_path, _export_rows())
    with bound(evo, timezone="Europe/Rome"):
        result = json.loads(await chats.export_chat("Mario Rossi"))
    folder = _folder(tmp_path)
    transcript = (folder / "chat.md").read_text(encoding="utf-8")
    assert re.sub(r"exported \S+", "exported <now>", transcript) == EXPORT_MARKDOWN
    assert re.search(r"exported 20\d\d-\d\d-\d\dT\d\d:\d\d:\d\d\+0[12]:00 ", transcript)
    assert _tree(folder) == ["chat.md", f"media/{EXPORT_PHOTO}"]
    assert (folder / "media" / EXPORT_PHOTO).read_bytes() == b"JPEG"
    assert result == {
        "chat_id": PERSON,
        "chat_name": "Mario Rossi",
        "folder": str(folder),
        "transcript": str(folder / "chat.md"),
        "messages": 3,
        "media_files": 1,
        "media_skipped": [],
        "media_skipped_count": 0,
        "size_bytes": 4 + len(transcript.encode("utf-8")),
    }
    assert _message_calls(evo) == [{"where": {"key": KEY}, "offset": 100, "page": 1}]
    assert [r.json for r in evo.requests if r.path == GET_BASE64] == [{"message": {"key": {"id": "3EB0EXP002"}}}]


@pytest.mark.anyio
async def test_export_chat_json_has_the_chat_the_zone_and_every_projected_message(evo, bound, monkeypatch, tmp_path):
    _export_setup(evo, monkeypatch, tmp_path, _export_rows())
    with bound(evo, timezone="Europe/Rome"):
        result = json.loads(await chats.export_chat(PERSON, export_format="json"))
    folder = _folder(tmp_path)
    assert result["transcript"] == str(folder / "chat.json")
    document = json.loads((folder / "chat.json").read_text(encoding="utf-8"))
    assert list(document) == ["chat_id", "chat_name", "exported_at", "since", "until", "time_zone", "messages"]
    assert (document["chat_id"], document["chat_name"]) == (PERSON, "Mario Rossi")
    assert (document["since"], document["until"], document["time_zone"]) == (None, None, "Europe/Rome")
    first, photo, deleted = document["messages"]
    assert first["text"] == "Dinner at 8?\nBring the wine"
    assert first["timestamp"] == "2026-07-01T12:00:00+02:00"
    assert first["quoted"]["text"] == "Table booked"
    assert "media_file" not in first
    assert photo["media_file"] == f"media/{EXPORT_PHOTO}"
    assert deleted["deleted"] is True
    assert "text" not in deleted


@pytest.mark.anyio
async def test_export_chat_whatsapp_txt_uses_the_whatsapp_format_and_keeps_media_beside_it(
    evo, bound, monkeypatch, tmp_path
):
    _export_setup(evo, monkeypatch, tmp_path, _export_rows())
    with bound(evo, timezone="Europe/Rome"):
        result = json.loads(await chats.export_chat(PERSON, export_format="whatsapp_txt"))
    folder = _folder(tmp_path)
    assert (folder / "chat.txt").read_text(encoding="utf-8") == EXPORT_TXT
    assert _tree(folder) == [EXPORT_PHOTO, "chat.txt"]
    assert result["transcript"] == str(folder / "chat.txt")


@pytest.mark.anyio
async def test_export_chat_period_is_sent_as_utc_and_named_in_the_display_zone(evo, bound, monkeypatch, tmp_path):
    _export_setup(evo, monkeypatch, tmp_path, _export_rows())
    with bound(evo, timezone="Europe/Rome"):
        result = json.loads(
            await chats.export_chat(
                PERSON,
                since=datetime(2026, 7, 1),
                until=datetime(2026, 7, 3),
                export_format="json",
                content="transcript",
            )
        )
    assert _message_calls(evo)[0]["where"] == {
        "key": KEY,
        "messageTimestamp": {"gte": "2026-06-30T22:00:00Z", "lte": "2026-07-02T22:00:00Z"},
    }
    folder = _folder(tmp_path, "Mario Rossi_2026-07-01_2026-07-03")
    assert result["folder"] == str(folder)
    document = json.loads((folder / "chat.json").read_text(encoding="utf-8"))
    assert (document["since"], document["until"]) == ("2026-07-01T00:00:00+02:00", "2026-07-03T00:00:00+02:00")


@pytest.mark.anyio
async def test_export_chat_transcript_only_fetches_no_media(evo, bound, monkeypatch, tmp_path):
    _export_setup(evo, monkeypatch, tmp_path, _export_rows(), media=False)  # an unprogrammed media call would fail
    with bound(evo, timezone="Europe/Rome"):
        result = json.loads(await chats.export_chat(PERSON, content="transcript"))
    assert _tree(_folder(tmp_path)) == ["chat.md"]
    assert (result["media_files"], result["media_skipped"]) == (0, [])


@pytest.mark.anyio
async def test_export_chat_media_only_with_a_type_filter_writes_no_transcript(evo, bound, monkeypatch, tmp_path):
    rows = [*_export_rows(), _document("3EB0EXP004", 1782900200)]
    _export_setup(evo, monkeypatch, tmp_path, rows, media=False)
    evo.on("POST", GET_BASE64, json={"fileName": "report.pdf", "mimetype": "application/pdf", "base64": _b64(b"%PDF")})
    with bound(evo, timezone="Europe/Rome"):
        result = json.loads(await chats.export_chat(PERSON, content="media", media_types=["document"]))
    folder = _folder(tmp_path)
    assert _tree(folder) == ["media/20260701-120320_3EB0EXP004_report.pdf"]
    assert "transcript" not in result
    assert result["media_files"] == 1
    assert [r.json for r in evo.requests if r.path == GET_BASE64] == [{"message": {"key": {"id": "3EB0EXP004"}}}]


@pytest.mark.anyio
async def test_export_chat_lists_each_skipped_attachment_with_its_reason(evo, bound, monkeypatch, tmp_path):
    monkeypatch.setattr(media, "MAX_FILE_BYTES", 8)
    monkeypatch.setattr(chats, "EXPORT_MAX_MEDIA_FILES", 2)
    rows = [_document(f"3EB0DOC00{i}", TS + i, name=f"d{i}.pdf") for i in range(1, 7)]
    _export_setup(evo, monkeypatch, tmp_path, rows, media=False)

    def answer(request):
        message_id = request.json["message"]["key"]["id"]
        if message_id == "3EB0DOC002":
            return 201, None
        if message_id == "3EB0DOC003":
            return 200, {"mimetype": "application/pdf", "base64": _b64(b"x" * 20)}
        if message_id == "3EB0DOC004":
            return 500, {"message": "boom"}
        return 200, {"mimetype": "application/pdf", "base64": _b64(b"abc")}

    evo.on("POST", GET_BASE64, handler=answer)
    with bound(evo):
        result = json.loads(await chats.export_chat(PERSON, content="media"))
    assert result["media_files"] == 2
    assert [entry["message_id"] for entry in result["media_skipped"]] == [
        "3EB0DOC002",
        "3EB0DOC003",
        "3EB0DOC004",
        "3EB0DOC006",
    ]
    reasons = [entry["reason"] for entry in result["media_skipped"]]
    assert reasons[:2] == ["not stored by Evolution", "over the size limit"]
    assert reasons[2] and reasons[2] not in reasons[:2]
    assert reasons[3] == "export limit reached"
    assert result["media_skipped_count"] == 4
    assert [name.split("_", 2)[1] for name in _tree(_folder(tmp_path, "Mario Rossi_start_now"))] == [
        "3EB0DOC001",
        "3EB0DOC005",
    ]


@pytest.mark.anyio
async def test_export_chat_stops_taking_media_at_the_byte_limit(evo, bound, monkeypatch, tmp_path):
    monkeypatch.setattr(chats, "EXPORT_MAX_MEDIA_BYTES", 10)
    rows = [_document("3EB0DOC001", TS), _document("3EB0DOC002", TS + 1)]
    _export_setup(evo, monkeypatch, tmp_path, rows, media=False)
    evo.on("POST", GET_BASE64, json={"mimetype": "application/pdf", "base64": _b64(b"123456")})
    with bound(evo):
        result = json.loads(await chats.export_chat(PERSON, content="media"))
    assert result["media_files"] == 1
    assert result["media_skipped"] == [{"message_id": "3EB0DOC002", "reason": "export limit reached"}]
    assert result["size_bytes"] == 6


@pytest.mark.anyio
async def test_export_chat_names_an_expired_attachment_without_the_signed_address(evo, bound, monkeypatch, tmp_path):
    rows = [_document("3EB0DOC001", TS), _document("3EB0DOC002", TS + 1)]
    _export_setup(evo, monkeypatch, tmp_path, rows, media=False)

    def answer(request):
        if request.json["message"]["key"]["id"] == "3EB0DOC001":
            return 400, {"message": "Error: Failed to fetch stream from https://cdn.example/f.enc?oh=SECRET&oe=1."}
        return 400, {"message": "Error: something else at https://cdn.example/g.enc?oh=SECRET2 failed"}

    evo.on("POST", GET_BASE64, handler=answer)
    with bound(evo):
        result = json.loads(await chats.export_chat(PERSON, content="media"))
    assert result["media_files"] == 0
    first, second = (entry["reason"] for entry in result["media_skipped"])
    assert first == "no longer available from WhatsApp"
    assert "SECRET" not in first + second and "<address omitted>" in second


@pytest.mark.anyio
async def test_export_chat_fetches_attachments_in_batches_and_saves_them_in_message_order(
    evo, bound, monkeypatch, tmp_path
):
    monkeypatch.setattr(chats, "EXPORT_MEDIA_CONCURRENCY", 2)
    rows = [_document(f"3EB0DOC00{i}", TS + i, name=f"d{i}.pdf") for i in range(1, 6)]
    _export_setup(evo, monkeypatch, tmp_path, rows, media=False)
    evo.on("POST", GET_BASE64, json={"mimetype": "application/pdf", "base64": _b64(b"abc")})
    with bound(evo):
        result = json.loads(await chats.export_chat(PERSON, content="media"))
    assert result["media_files"] == 5
    assert sorted(r.json["message"]["key"]["id"] for r in evo.requests if r.path == GET_BASE64) == [
        f"3EB0DOC00{i}" for i in range(1, 6)
    ]


@pytest.mark.anyio
async def test_export_chat_starts_no_fetch_after_the_media_time_limit(evo, bound, monkeypatch, tmp_path):
    monkeypatch.setattr(chats, "EXPORT_MEDIA_SECONDS", 0)
    rows = [_document("3EB0DOC001", TS), _document("3EB0DOC002", TS + 1)]
    _export_setup(evo, monkeypatch, tmp_path, rows, media=False)
    evo.on("POST", GET_BASE64, json={"mimetype": "application/pdf", "base64": _b64(b"abc")})
    with bound(evo):
        result = json.loads(await chats.export_chat(PERSON))
    assert result["media_files"] == 0
    assert [entry["reason"] for entry in result["media_skipped"]] == ["export time limit reached"] * 2
    assert [r for r in evo.requests if r.path == GET_BASE64] == []
    assert (_folder(tmp_path) / "chat.md").exists()


def _many(count):
    return [_row(f"3EB0X{i:05d}", text=f"message {i}", ts=TS + i) for i in range(count)]


@pytest.mark.anyio
async def test_export_chat_takes_the_newest_5000_messages_and_says_so(evo, bound, monkeypatch, tmp_path):
    _export_setup(evo, monkeypatch, tmp_path, _many(5100), media=False)
    with bound(evo):
        result = json.loads(await chats.export_chat(PERSON, export_format="whatsapp_txt", content="transcript"))
    assert result["truncated"] is True
    assert result["note"] == (
        "Exported the newest 5000 messages of the period; narrow since or until to export older ones."
    )
    assert result["messages"] == 5000
    calls = _message_calls(evo)
    assert [(call["offset"], call["page"]) for call in calls] == [(100, page) for page in range(1, 51)]
    lines = (_folder(tmp_path) / "chat.txt").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 5000
    assert lines[0].endswith("Ana: message 100")
    assert lines[-1].endswith("Ana: message 5099")


@pytest.mark.anyio
async def test_export_chat_of_exactly_5000_messages_is_not_truncated(evo, bound, monkeypatch, tmp_path):
    _export_setup(evo, monkeypatch, tmp_path, _many(5000), media=False)
    with bound(evo):
        result = json.loads(await chats.export_chat(PERSON, export_format="whatsapp_txt", content="transcript"))
    assert result["messages"] == 5000
    assert "truncated" not in result
    assert "note" not in result


@pytest.mark.anyio
async def test_export_chat_replaces_the_folder_of_an_earlier_export(evo, bound, monkeypatch, tmp_path):
    _export_setup(evo, monkeypatch, tmp_path, _export_rows())
    with bound(evo, timezone="Europe/Rome"):
        await chats.export_chat(PERSON)
        folder = _folder(tmp_path)
        (folder / "stale.txt").write_text("left over", encoding="utf-8")
        (folder / "media" / "stale.bin").write_bytes(b"old")
        await chats.export_chat(PERSON)
    assert _tree(folder) == ["chat.md", f"media/{EXPORT_PHOTO}"]


@pytest.mark.anyio
async def test_export_chat_without_messages_writes_nothing(evo, bound, monkeypatch, tmp_path):
    _export_setup(evo, monkeypatch, tmp_path, [], media=False)
    with bound(evo):
        result = json.loads(await chats.export_chat(PERSON))
    assert result == {
        "chat_id": PERSON,
        "chat_name": "Mario Rossi",
        "messages": 0,
        "media_files": 0,
        "note": "No stored messages in this period; nothing was exported.",
    }
    assert not (tmp_path / "dl").exists()


@pytest.mark.anyio
async def test_export_chat_refuses_a_reversed_period_before_calling_evolution(evo, bound):
    with bound(evo), pytest.raises(ToolExecutionError) as excinfo:
        await chats.export_chat(PERSON, since=datetime(2026, 7, 2), until=datetime(2026, 7, 1))
    assert str(excinfo.value) == "since must be earlier than until. Nothing was changed."
    assert evo.requests == []


@pytest.mark.anyio
async def test_export_chat_a_partial_name_is_refused_and_nothing_is_written(evo, bound, monkeypatch, tmp_path):
    _export_setup(evo, monkeypatch, tmp_path, _export_rows(), media=False)
    with bound(evo), pytest.raises(ToolExecutionError, match=r"named exactly 'Mario'.*Nothing was changed\.$"):
        await chats.export_chat("Mario")
    assert not (tmp_path / "dl").exists()


def _hosted_setup(evo, monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "_data_dir_override", tmp_path)
    monkeypatch.setattr(tenant, "_public_url", "https://mcp.example.test")
    _export_setup(evo, monkeypatch, tmp_path, _export_rows())


@pytest.mark.anyio
async def test_export_chat_hosted_publishes_a_zip_and_removes_the_work_folder(evo, bound, monkeypatch, tmp_path):
    _hosted_setup(evo, monkeypatch, tmp_path)
    with bound(evo, mode="hosted", subject="t_abc123", timezone="Europe/Rome"):
        result = json.loads(await chats.export_chat(PERSON))
    assert "folder" not in result
    assert "transcript" not in result
    assert result["download_url"].startswith("https://mcp.example.test/files/")
    assert result["expires_at"]
    assert (result["messages"], result["media_files"], result["media_skipped_count"]) == (3, 1, 0)
    token = result["download_url"].rsplit("/", 1)[1]
    archive = tmp_path / "files" / "t_abc123" / token / f"{EXPORT_FOLDER}.zip"
    assert result["size_bytes"] == archive.stat().st_size
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.namelist() == [f"{EXPORT_FOLDER}/chat.md", f"{EXPORT_FOLDER}/media/{EXPORT_PHOTO}"]
        assert bundle.getinfo(f"{EXPORT_FOLDER}/chat.md").compress_type == zipfile.ZIP_DEFLATED
        text = bundle.read(f"{EXPORT_FOLDER}/chat.md").decode("utf-8")
        assert bundle.read(f"{EXPORT_FOLDER}/media/{EXPORT_PHOTO}") == b"JPEG"
    assert re.sub(r"exported \S+", "exported <now>", text) == EXPORT_MARKDOWN
    assert list((tmp_path / "files" / "t_abc123" / "tmp").iterdir()) == []
    assert not (tmp_path / "dl").exists()


@pytest.mark.anyio
async def test_export_chat_hosted_refuses_a_zip_over_the_limit_and_publishes_nothing(evo, bound, monkeypatch, tmp_path):
    _hosted_setup(evo, monkeypatch, tmp_path)
    monkeypatch.setattr(chats, "EXPORT_MAX_HOSTED_BYTES", 10)
    with bound(evo, mode="hosted", subject="t_abc123"), pytest.raises(ToolExecutionError) as excinfo:
        await chats.export_chat(PERSON)
    assert re.fullmatch(
        r"The export is \d+ bytes; the hosted server delivers at most 10 bytes\. "
        r"Narrow since or until, or export the transcript only\.",
        str(excinfo.value),
    )
    tenant_dir = tmp_path / "files" / "t_abc123"
    assert list(tenant_dir.glob("*/*.zip")) == []
    assert list((tenant_dir / "tmp").iterdir()) == []


@pytest.mark.anyio
async def test_export_chat_hosted_without_a_subject_fails_before_calling_evolution(evo, bound):
    with bound(evo, mode="hosted", subject=None), pytest.raises(ToolExecutionError, match="has no subject"):
        await chats.export_chat(PERSON)
    assert evo.requests == []


@pytest.mark.anyio
async def test_download_message_media_and_export_chat_share_one_hosted_publisher(evo, bound, monkeypatch, tmp_path):
    published = []

    def fake_publish(path, conn):
        published.append((path.name, conn.subject))
        return {"download_url": "https://x.test/files/t", "expires_at": "soon"}

    monkeypatch.setattr(chats, "_publish_hosted", fake_publish)
    monkeypatch.setattr(paths, "_data_dir_override", tmp_path)
    _export_setup(evo, monkeypatch, tmp_path, [_document("3EB0DOC001", TS)], media=False)
    evo.on("POST", GET_BASE64, json={"mimetype": "application/pdf", "fileName": "a.pdf", "base64": _b64(b"%PDF")})
    with bound(evo, mode="hosted", subject="t_abc123"):
        await chats.download_message_media("3EB0DOC001")
        await chats.export_chat(PERSON)
    assert published == [("393331234567_3EB0DOC001_a.pdf", "t_abc123"), (f"{EXPORT_FOLDER}.zip", "t_abc123")]
