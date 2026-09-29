import base64
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx2
import pytest
from mcp_types import ImageContent, TextContent

from evolution_api_mcp import context, media, paths, tenant
from evolution_api_mcp.errors import ToolExecutionError
from evolution_api_mcp.registry import BUSINESS
from evolution_api_mcp.tools import chats
from tests.conftest import INSTANCE

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

NOT_FOUND_HINT = "Use read_messages or search_messages to find its id."


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


# --- list_chats ------------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_chats_maps_take_and_skip_and_projects_rows(evo, bound):
    last = _row("3EB0LAST01", text="See you tomorrow", from_me=True)
    evo.on(
        "POST",
        FIND_CHATS,
        json=[_chat_row(PERSON, unread=3, last=last), _chat_row(GROUP, name="Family", updated=TS)],
    )
    with bound(evo):
        result = json.loads(await chats.list_chats(limit=20, offset=0))
    assert evo.last("POST", FIND_CHATS).json == {"take": 21, "skip": 0}
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
    }


@pytest.mark.anyio
async def test_list_chats_pages_with_offset_and_reports_has_more(evo, bound):
    rows = [_chat_row(f"39333000000{i}@s.whatsapp.net") for i in range(3)]
    evo.on("POST", FIND_CHATS, json=rows)
    with bound(evo):
        result = json.loads(await chats.list_chats(limit=2, offset=4))
    assert evo.last("POST", FIND_CHATS).json == {"take": 3, "skip": 4}
    assert [c["chat_id"] for c in result["chats"]] == [r["remoteJid"] for r in rows[:2]]
    assert result["has_more"] is True
    assert result["offset"] == 4
    assert "scanned" not in result


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
    pages = [r.json["page"] for r in evo.requests if r.path == FIND_MESSAGES]
    assert pages == [1, 2]
    assert [m["message_id"] for m in result["matches"]] == ["3EB0000005", "3EB0000150"]
    assert result["scanned"] == 200
    assert result["complete"] is True


@pytest.mark.anyio
async def test_search_messages_scan_stops_at_2000_messages_with_a_note(evo, bound):
    evo.on("POST", FIND_MESSAGES, handler=_search_pages(5000, {4000: "invoice far back"}))
    with bound(evo):
        result = json.loads(await chats.search_messages("invoice"))
    assert len([r for r in evo.requests if r.path == FIND_MESSAGES]) == 20
    assert result["matches"] == []
    assert result["scanned"] == 2000
    assert result["complete"] is False
    assert result["note"] == (
        "Only the newest 2000 messages were scanned; narrow with chat, since or until to search further back."
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


# --- get_message -----------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_message_returns_the_full_text_and_the_raw_type(evo, bound):
    evo.on("POST", FIND_MESSAGES, json=_page(_row("3EB0AAAA01", text="y" * 5000)))
    with bound(evo):
        result = json.loads(await chats.get_message("3EB0AAAA01"))
    assert evo.last("POST", FIND_MESSAGES).json == {"where": {"key": {"id": "3EB0AAAA01"}}, "offset": 1, "page": 1}
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
    assert evo.last("POST", FIND_MESSAGES).json["where"] == {"key": {"id": "3EB0AAAA01", **KEY}}


@pytest.mark.anyio
async def test_get_message_not_found_names_the_chat_scope(evo, bound):
    evo.on("POST", FIND_MESSAGES, json=_page())
    with bound(evo):
        with pytest.raises(ToolExecutionError) as anywhere:
            await chats.get_message("3EB0MISSING")
        with pytest.raises(ToolExecutionError) as in_chat:
            await chats.get_message("3EB0MISSING", chat=PERSON)
    assert str(anywhere.value) == f"Message 3EB0MISSING was not found. {NOT_FOUND_HINT}"
    assert str(in_chat.value) == f"Message 3EB0MISSING was not found in this chat. {NOT_FOUND_HINT}"


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
        result = json.loads(await chats.get_message_status(GROUP, "3EB0AAAA01"))
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
        assert json.loads(await chats.get_message_status(PERSON, "3EB0AAAA01"))["latest"] == "PENDING"
        evo.on("POST", FIND_STATUS, json=[_status("PENDING"), _status("ERROR"), _status("SERVER_ACK")])
        assert json.loads(await chats.get_message_status(PERSON, "3EB0AAAA01"))["latest"] == "SERVER_ACK"


@pytest.mark.anyio
async def test_get_message_status_deleted_wins_over_read(evo, bound):
    evo.on("POST", FIND_STATUS, json=[_status("READ"), _status("DELETED")])
    with bound(evo):
        result = json.loads(await chats.get_message_status(PERSON, "3EB0AAAA01"))
    assert result["latest"] == "DELETED"


@pytest.mark.anyio
async def test_get_message_status_without_records_uses_the_stored_message_status(evo, bound):
    evo.on("POST", FIND_STATUS, json=[])
    evo.on("POST", FIND_MESSAGES, json=_page(_row("3EB0AAAA01", from_me=True, status="DELIVERY_ACK")))
    with bound(evo):
        result = json.loads(await chats.get_message_status(PERSON, "3EB0AAAA01"))
    assert evo.last("POST", FIND_MESSAGES).json["where"] == {"key": {"id": "3EB0AAAA01", **KEY}}
    assert result["latest"] == "DELIVERY_ACK"
    assert result["updates"] == []


@pytest.mark.anyio
async def test_get_message_status_of_an_unknown_message_is_unknown(evo, bound):
    evo.on("POST", FIND_STATUS, json=[])
    evo.on("POST", FIND_MESSAGES, json=_page())
    with bound(evo):
        result = json.loads(await chats.get_message_status(PERSON, "3EB0AAAA01"))
    assert result["latest"] == "unknown"


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
    assert str(excinfo.value) == "Message 3EB0MISSING was not found in this chat. Use read_messages to find its id."


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
