from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import datetime, timedelta, timezone

import pytest

from evolution_api_mcp import directory, messages
from evolution_api_mcp.client import EvolutionClient
from evolution_api_mcp.context import InstanceIdentity
from tests.conftest import BASE, TOKEN
from tests.fakes import FakeEvolution

CHAT = "393331234567@s.whatsapp.net"
GROUP = "120363025246125486@g.us"


def row(message_type: str, message: dict, *, from_me: bool = False, chat: str = CHAT, **extra: object) -> dict:
    return {
        "key": {"id": "MSG0001", "remoteJid": chat, "fromMe": from_me},
        "pushName": "Marta",
        "messageType": message_type,
        "message": message,
        "messageTimestamp": 1_700_000_000,
        **extra,
    }


# ---- timestamps -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1_700_000_000, 1_700_000_000),
        (1_700_000_000.9, 1_700_000_000),
        ("1700000000", 1_700_000_000),
        ("1700000000.0", 1_700_000_000),
        ({"low": 1_700_000_000, "high": 0, "unsigned": True}, 1_700_000_000),
        ({"low": 5, "high": 1}, (1 << 32) | 5),
        ({"low": -1, "high": 0}, 0xFFFFFFFF),
        ("soon", None),
        (None, None),
        (True, None),
        ({"low": 1}, None),
        (float("nan"), None),
    ],
)
def test_ts_seconds(value: object, expected: int | None) -> None:
    assert messages.ts_seconds(value) == expected


def test_iso_renders_utc_seconds() -> None:
    assert messages.iso(0) == "1970-01-01T00:00:00Z"
    assert messages.iso(86_400) == "1970-01-02T00:00:00Z"
    assert messages.iso(1_700_000_000) == "2023-11-14T22:13:20Z"
    assert messages.iso({"low": 1_700_000_000, "high": 0}) == "2023-11-14T22:13:20Z"
    assert messages.iso("garbage") is None
    assert messages.iso(None) is None


# ---- filters --------------------------------------------------------------------------------------------------


def test_chat_key_filter_sends_both_jid_keys() -> None:
    assert messages.chat_key_filter(CHAT) == {"remoteJid": CHAT, "remoteJidAlt": CHAT}


def test_time_window_none_when_no_bound() -> None:
    assert messages.time_window(None, None) is None


def test_time_window_fills_missing_end_with_iso_strings() -> None:
    since = datetime(2026, 9, 1, 8, 30, tzinfo=timezone.utc)
    window = messages.time_window(since, None)
    assert window is not None
    assert window["gte"] == "2026-09-01T08:30:00Z"
    upper = datetime.strptime(window["lte"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    assert now + timedelta(hours=23) < upper <= now + timedelta(days=1, seconds=5)

    until = datetime(2026, 9, 2, 0, 0, tzinfo=timezone.utc)
    assert messages.time_window(None, until) == {"gte": "1970-01-01T00:00:00Z", "lte": "2026-09-02T00:00:00Z"}


def test_time_window_normalizes_naive_and_offset_datetimes_to_utc() -> None:
    naive = datetime(2026, 9, 1, 10, 0)
    plus_two = datetime(2026, 9, 1, 12, 0, tzinfo=timezone(timedelta(hours=2)))
    window = messages.time_window(naive, plus_two)
    assert window == {"gte": "2026-09-01T10:00:00Z", "lte": "2026-09-01T10:00:00Z"}
    assert all(isinstance(value, str) for value in window.values())


# ---- types and text -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "message", "expected"),
    [
        ("conversation", {}, "text"),
        ("extendedTextMessage", {}, "text"),
        ("imageMessage", {}, "image"),
        ("videoMessage", {}, "video"),
        ("ptvMessage", {}, "video_note"),
        ("audioMessage", {"audioMessage": {"ptt": True}}, "voice_note"),
        ("audioMessage", {"audioMessage": {"ptt": False}}, "audio"),
        ("audioMessage", {"audioMessage": {}}, "audio"),
        ("audioMessage", None, "audio"),
        ("documentMessage", {}, "document"),
        ("documentWithCaptionMessage", {}, "document"),
        ("stickerMessage", {}, "sticker"),
        ("locationMessage", {}, "location"),
        ("liveLocationMessage", {}, "location"),
        ("contactMessage", {}, "contact"),
        ("contactsArrayMessage", {}, "contact"),
        ("pollCreationMessage", {}, "poll"),
        ("pollCreationMessageV2", {}, "poll"),
        ("pollCreationMessageV3", {}, "poll"),
        ("reactionMessage", {}, "reaction"),
        ("protocolMessage", {}, "system"),
        ("buttonsResponseMessage", {}, "response"),
        ("listResponseMessage", {}, "response"),
        ("templateButtonReplyMessage", {}, "response"),
        ("interactiveResponseMessage", {}, "response"),
        ("templateMessage", {}, "template"),
        ("somethingNew", {}, "other"),
        (None, None, "other"),
    ],
)
def test_normalize_type(raw: str | None, message: dict | None, expected: str) -> None:
    assert messages.normalize_type(raw, message) == expected


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ({"conversation": "hi"}, "hi"),
        ({"extendedTextMessage": {"text": "link https://x.test"}}, "link https://x.test"),
        ({"imageMessage": {"caption": "cap"}}, "cap"),
        ({"videoMessage": {"caption": "clip"}}, "clip"),
        ({"documentMessage": {"caption": "doc"}}, "doc"),
        ({"documentWithCaptionMessage": {"message": {"documentMessage": {"caption": "wrapped"}}}}, "wrapped"),
        ({"buttonsResponseMessage": {"selectedDisplayText": "Yes"}}, "Yes"),
        ({"listResponseMessage": {"title": "Option A"}}, "Option A"),
        ({"templateButtonReplyMessage": {"selectedDisplayText": "Stop"}}, "Stop"),
        ({"interactiveResponseMessage": {"body": {"text": "picked"}}}, "picked"),
        ({"pollCreationMessageV3": {"name": "Lunch?"}}, "Lunch?"),
        ({"reactionMessage": {"text": "👍"}}, "👍"),
        ({"audioMessage": {"ptt": True}, "speechToText": "[audio] hello"}, "[audio] hello"),
        ({"conversation": "", "extendedTextMessage": {"text": "second"}}, "second"),
        ({"conversation": "first", "extendedTextMessage": {"text": "second"}}, "first"),
        ({"imageMessage": {}}, None),
        ({}, None),
        (None, None),
    ],
)
def test_extract_text(message: dict | None, expected: str | None) -> None:
    assert messages.extract_text(message) == expected


# ---- projection -----------------------------------------------------------------------------------------------


def test_project_text_message() -> None:
    projected = messages.project_message(
        row("conversation", {"conversation": "Hello"}, MessageUpdate=[{"status": "DELIVERY_ACK"}, {"status": "READ"}])
    )
    assert projected == {
        "message_id": "MSG0001",
        "chat_id": CHAT,
        "from_me": False,
        "sender": CHAT,
        "sender_name": "Marta",
        "timestamp": "2023-11-14T22:13:20Z",
        "type": "text",
        "text": "Hello",
        "status": "READ",
    }


def test_project_extended_text_with_quote_from_context_info() -> None:
    projected = messages.project_message(
        row(
            "extendedTextMessage",
            {"extendedTextMessage": {"text": "Sure"}},
            contextInfo={"stanzaId": "QUOTED01", "participant": CHAT},
            status="SERVER_ACK",
        )
    )
    assert projected["text"] == "Sure"
    assert projected["quoted"] == {"message_id": "QUOTED01", "sender": CHAT}
    assert "quoted_message_id" not in projected
    assert projected["status"] == "SERVER_ACK"


def test_project_image_with_caption_and_media_details() -> None:
    projected = messages.project_message(
        row(
            "imageMessage",
            {"imageMessage": {"caption": "Receipt", "mimetype": "image/jpeg", "fileLength": "48213", "url": "secret"}},
        )
    )
    assert projected["type"] == "image"
    assert projected["text"] == "Receipt"
    assert projected["media"] == {"mimetype": "image/jpeg", "size_bytes": 48213}
    assert "url" not in str(projected)


def test_project_voice_note_and_audio() -> None:
    voice = messages.project_message(
        row("audioMessage", {"audioMessage": {"ptt": True, "seconds": 12, "mimetype": "audio/ogg; codecs=opus"}})
    )
    assert voice["type"] == "voice_note"
    assert voice["media"] == {"mimetype": "audio/ogg; codecs=opus", "seconds": 12}
    plain = messages.project_message(row("audioMessage", {"audioMessage": {"ptt": False}}))
    assert plain["type"] == "audio"
    assert "media" not in plain


def test_project_document_direct_and_wrapped() -> None:
    direct = messages.project_message(
        row(
            "documentMessage",
            {
                "documentMessage": {
                    "fileName": "invoice.pdf",
                    "mimetype": "application/pdf",
                    "fileLength": {"low": 2048, "high": 0},
                }
            },
        )
    )
    assert direct["type"] == "document"
    assert direct["media"] == {"mimetype": "application/pdf", "file_name": "invoice.pdf", "size_bytes": 2048}
    wrapped = messages.project_message(
        row(
            "documentWithCaptionMessage",
            {
                "documentWithCaptionMessage": {
                    "message": {"documentMessage": {"fileName": "a.pdf", "caption": "see attached"}}
                }
            },
        )
    )
    assert wrapped["type"] == "document"
    assert wrapped["text"] == "see attached"
    assert wrapped["media"] == {"file_name": "a.pdf"}


def test_project_location() -> None:
    projected = messages.project_message(
        row(
            "locationMessage",
            {
                "locationMessage": {
                    "degreesLatitude": 41.38,
                    "degreesLongitude": 2.17,
                    "name": "Office",
                    "jpegThumbnail": "x",
                }
            },
        )
    )
    assert projected["type"] == "location"
    assert projected["location"] == {"latitude": 41.38, "longitude": 2.17, "name": "Office"}


def test_project_contact_poll_and_reaction() -> None:
    contact = messages.project_message(
        row("contactMessage", {"contactMessage": {"displayName": "Ana", "vcard": "BEGIN:VCARD"}})
    )
    assert contact["contact"] == {"name": "Ana"}
    assert "vcard" not in str(contact).lower()

    poll = messages.project_message(
        row(
            "pollCreationMessageV3",
            {
                "pollCreationMessageV3": {
                    "name": "Lunch?",
                    "options": [{"optionName": "Pizza"}, {"optionName": "Sushi"}],
                }
            },
        )
    )
    assert poll["type"] == "poll"
    assert poll["poll"] == {"question": "Lunch?", "options": ["Pizza", "Sushi"]}

    reaction = messages.project_message(
        row("reactionMessage", {"reactionMessage": {"text": "❤️", "key": {"id": "TARGET01", "remoteJid": CHAT}}})
    )
    assert reaction["type"] == "reaction"
    assert reaction["reaction"] == {"emoji": "❤️", "target_message_id": "TARGET01"}


def test_project_protocol_is_system_and_unknown_is_other_with_raw_type() -> None:
    system = messages.project_message(row("protocolMessage", {"protocolMessage": {"type": 0}}))
    assert system["type"] == "system"
    assert "raw_type" not in system
    other = messages.project_message(row("albumMessage", {"albumMessage": {}}))
    assert other["type"] == "other"
    assert other["raw_type"] == "albumMessage"


def test_project_suppresses_voce_push_name_and_own_messages_are_me() -> None:
    mine = messages.project_message(row("conversation", {"conversation": "ok"}, from_me=True, pushName="Você"))
    assert mine["from_me"] is True
    assert mine["sender"] == "me"
    assert "sender_name" not in mine
    theirs = messages.project_message(row("conversation", {"conversation": "ok"}, pushName="Você"))
    assert "sender_name" not in theirs


def test_project_group_sender_prefers_key_participant_then_context_info() -> None:
    base = row("conversation", {"conversation": "yo"}, chat=GROUP)
    base["key"]["participant"] = "34600000001@s.whatsapp.net"
    assert messages.project_message(base)["sender"] == "34600000001@s.whatsapp.net"

    via_context = row(
        "conversation", {"conversation": "yo"}, chat=GROUP, contextInfo={"participant": "34600000002@s.whatsapp.net"}
    )
    assert messages.project_message(via_context)["sender"] == "34600000002@s.whatsapp.net"

    unknown = row("conversation", {"conversation": "yo"}, chat=GROUP)
    assert messages.project_message(unknown)["sender"] == GROUP


def test_project_handles_long_timestamp() -> None:
    projected = messages.project_message(
        row("conversation", {"conversation": "x"}, messageTimestamp={"low": 1_700_000_000, "high": 0})
    )
    assert projected["timestamp"] == "2023-11-14T22:13:20Z"


def test_project_truncates_text_and_flags_it() -> None:
    long_text = "a" * 2000
    cut = messages.project_message(row("conversation", {"conversation": long_text}))
    assert cut["text"] == "a" * 1500
    assert cut["text_truncated"] is True
    limited = messages.project_message(row("conversation", {"conversation": long_text}), text_limit=200)
    assert len(limited["text"]) == 200
    assert limited["text_truncated"] is True
    full = messages.project_message(row("conversation", {"conversation": long_text}), text_limit=20_000)
    assert full["text"] == long_text
    assert "text_truncated" not in full
    unlimited = messages.project_message(row("conversation", {"conversation": long_text}), text_limit=None)
    assert unlimited["text"] == long_text
    exact = messages.project_message(row("conversation", {"conversation": "b" * 1500}))
    assert "text_truncated" not in exact


def test_project_never_leaks_internal_fields() -> None:
    projected = messages.project_message(
        row("conversation", {"conversation": "hi"}, instanceId="cuid123", source="android", id="dbcuid")
    )
    assert not {"instanceId", "source", "id"} & projected.keys()
    assert "cuid123" not in str(projected)
    assert "android" not in str(projected)


def test_project_tolerates_a_bare_row() -> None:
    assert messages.project_message({}) == {"type": "other"}


# ---- projection v2: names, quotes, deletion, forwarding, mentions ----------------------------------------------

OTHER = "391110001111@s.whatsapp.net"
THIRD = "391110002222@s.whatsapp.net"
LID = "99887766@lid"
ME = "393930000000@s.whatsapp.net"


def known(*entries: tuple[str, str | None], me: tuple[str, ...] = ()) -> directory.Directory:
    names = directory.Directory(me=frozenset(me))
    for chat_id, name in entries:
        kind = "group" if chat_id.endswith("@g.us") else "person"
        names.entries[chat_id] = directory.Entry(chat_id, name, kind, None, True)
    return names


@pytest.mark.parametrize(
    ("chat_id", "expected"),
    [
        ("status@broadcast", True),
        ("0@s.whatsapp.net", True),
        ("120363@newsletter", True),
        ("12345@broadcast", True),
        (CHAT, False),
        (GROUP, False),
        (LID, False),
        (None, False),
    ],
)
def test_is_noise_chat(chat_id: str | None, expected: bool) -> None:
    assert messages.is_noise_chat(chat_id) is expected


def test_chat_name_is_shown_only_when_asked_for() -> None:
    names = known((CHAT, "Marta Rossi"))
    plain = messages.project_message(row("conversation", {"conversation": "hi"}), names=names)
    named = messages.project_message(row("conversation", {"conversation": "hi"}), names=names, include_chat_name=True)
    assert "chat_name" not in plain
    assert named["chat_name"] == "Marta Rossi"
    unnamed = messages.project_message(row("conversation", {"conversation": "hi"}), include_chat_name=True)
    assert "chat_name" not in unnamed


def test_sender_name_falls_back_to_the_directory_but_push_name_wins() -> None:
    names = known((CHAT, "Marta Rossi"), (GROUP, "Family"), (OTHER, "Ana"))
    no_push = messages.project_message(row("conversation", {"conversation": "hi"}, pushName=""), names=names)
    assert no_push["sender_name"] == "Marta Rossi"
    with_push = messages.project_message(row("conversation", {"conversation": "hi"}), names=names)
    assert with_push["sender_name"] == "Marta"
    mine = messages.project_message(row("conversation", {"conversation": "hi"}, from_me=True, pushName=""), names=names)
    assert "sender_name" not in mine
    assert "sender_name" not in messages.project_message(row("conversation", {"conversation": "hi"}, pushName=""))

    in_group = row("conversation", {"conversation": "yo"}, chat=GROUP, pushName="")
    in_group["key"]["participant"] = OTHER
    assert messages.project_message(in_group, names=names)["sender_name"] == "Ana"

    unknown_participant = row("conversation", {"conversation": "yo"}, chat=GROUP, pushName="")
    assert "sender_name" not in messages.project_message(unknown_participant, names=names)  # the sender is the group


def test_sender_phone_is_shown_for_a_known_lid_sender() -> None:
    names = known()
    names.lid_phone[LID] = "393331234567"
    lid_row = row("conversation", {"conversation": "yo"}, chat=GROUP, pushName="")
    lid_row["key"]["participant"] = LID
    assert messages.project_message(lid_row, names=names)["sender_phone"] == "393331234567"
    assert "sender_phone" not in messages.project_message(lid_row)
    assert "sender_phone" not in messages.project_message(lid_row, names=known())
    phone_row = row("conversation", {"conversation": "yo"}, chat=GROUP)
    phone_row["key"]["participant"] = OTHER
    assert "sender_phone" not in messages.project_message(phone_row, names=names)


def test_quoted_carries_sender_and_text_cut_at_200_characters() -> None:
    names = known((OTHER, "Ana"))
    quoting = row(
        "extendedTextMessage",
        {"extendedTextMessage": {"text": "Sure"}},
        contextInfo={
            "stanzaId": "QUOTED01",
            "participant": OTHER,
            "quotedMessage": {"conversation": "q" * 300},
        },
    )
    assert messages.project_message(quoting, names=names)["quoted"] == {
        "message_id": "QUOTED01",
        "sender": "Ana",
        "text": "q" * 200,
    }
    assert messages.project_message(quoting)["quoted"]["sender"] == OTHER
    bare = row("conversation", {"conversation": "x"}, contextInfo={"stanzaId": "QUOTED02"})
    assert messages.project_message(bare)["quoted"] == {"message_id": "QUOTED02"}
    assert "quoted" not in messages.project_message(row("conversation", {"conversation": "x"}, contextInfo={}))


def test_deleted_message_drops_its_content() -> None:
    image = row(
        "imageMessage",
        {"imageMessage": {"caption": "secret", "mimetype": "image/jpeg", "fileLength": 10}},
        status="DELETED",
    )
    projected = messages.project_message(image)
    assert projected["deleted"] is True
    assert projected["type"] == "image"
    assert not {"text", "media"} & projected.keys()
    assert "secret" not in str(projected)


def test_a_key_marked_deleted_is_deleted() -> None:
    stored = row("conversation", {"conversation": "oops"})
    stored["key"]["deleted"] = True
    projected = messages.project_message(stored)
    assert projected["deleted"] is True
    assert "text" not in projected
    assert "deleted" not in messages.project_message(row("conversation", {"conversation": "fine"}))


@pytest.mark.parametrize(
    ("context", "expected"),
    [
        ({"isForwarded": True}, True),
        ({"forwardingScore": 2}, True),
        ({"forwardingScore": 0}, False),
        ({"forwardingScore": True}, False),
        ({"isForwarded": False}, False),
        ({}, False),
    ],
)
def test_forwarded_flag(context: dict, expected: bool) -> None:
    projected = messages.project_message(row("conversation", {"conversation": "fwd"}, contextInfo=context))
    assert projected.get("forwarded", False) is expected


def test_mentions_are_names_then_phones_then_jids_and_flag_this_number() -> None:
    names = known((OTHER, "Ana"), me=(ME,))
    mentioning = row(
        "extendedTextMessage",
        {"extendedTextMessage": {"text": "@Ana @x @y"}},
        chat=GROUP,
        contextInfo={"mentionedJid": [OTHER, THIRD, "55667788@lid", ME]},
    )
    projected = messages.project_message(mentioning, names=names)
    assert projected["mentions"] == ["Ana", "391110002222", "55667788@lid", "393930000000"]
    assert projected["mentions_me"] is True

    without_names = messages.project_message(mentioning)
    assert without_names["mentions"] == ["391110001111", "391110002222", "55667788@lid", "393930000000"]
    assert "mentions_me" not in without_names

    others_only = row("conversation", {"conversation": "hi"}, contextInfo={"mentionedJid": [OTHER]})
    assert "mentions_me" not in messages.project_message(others_only, names=names)
    assert "mentions" not in messages.project_message(row("conversation", {"conversation": "hi"}))


def test_a_mention_addressed_to_this_numbers_lid_is_recognised() -> None:
    names = known(me=(ME,))
    names.lid_phone[LID] = "393930000000"
    mentioning = row("conversation", {"conversation": "@me"}, contextInfo={"mentionedJid": [LID]})
    projected = messages.project_message(mentioning, names=names)
    assert projected["mentions_me"] is True
    assert projected["mentions"] == ["393930000000"]


# ---- project_rows: folding reactions --------------------------------------------------------------------------


def group_row(
    message_id: str,
    message_type: str,
    message: dict,
    *,
    who: str | None = OTHER,
    from_me: bool = False,
    ts: int = 1_700_000_000,
) -> dict:
    key: dict = {"id": message_id, "remoteJid": GROUP, "fromMe": from_me}
    if who:
        key["participant"] = who
    return {"key": key, "messageType": message_type, "message": message, "messageTimestamp": ts}


def reaction(
    message_id: str, target: str, emoji: str, *, who: str | None = OTHER, from_me: bool = False, ts: int
) -> dict:
    payload = {"reactionMessage": {"text": emoji, "key": {"id": target, "remoteJid": GROUP}}}
    return group_row(message_id, "reactionMessage", payload, who=who, from_me=from_me, ts=ts)


def test_reactions_fold_into_their_target_and_their_rows_disappear() -> None:
    names = known((OTHER, "Ana"))
    target = group_row("T1", "conversation", {"conversation": "lunch?"}, who=THIRD, ts=50)
    rows = [
        target,
        reaction("R1", "T1", "👍", ts=100),
        reaction("R2", "T1", "🎉", ts=200),  # Ana changed her mind: only the newest counts
        reaction("R3", "T1", "❤️", who=None, from_me=True, ts=150),
        reaction("R4", "T1", "", who=THIRD, ts=120),  # an empty emoji removes a reaction: nothing to show
        reaction("R5", "OUTSIDE", "😮", ts=300),  # its target is not in the list: stays a row
        None,
    ]
    projected = messages.project_rows(rows, names=names)
    assert [item["message_id"] for item in projected] == ["T1", "R5"]
    assert projected[0]["reactions"] == [{"emoji": "🎉", "by": "Ana"}, {"emoji": "❤️", "by": "me"}]
    assert projected[1]["type"] == "reaction"
    assert projected[1]["reaction"] == {"emoji": "😮", "target_message_id": "OUTSIDE"}


def test_a_removed_reaction_shows_nothing_whatever_the_row_order() -> None:
    target = group_row("T1", "conversation", {"conversation": "lunch?"}, who=THIRD, ts=50)
    added = reaction("R1", "T1", "👍", ts=100)
    removed = reaction("R2", "T1", "", ts=200)
    newest_first = messages.project_rows([removed, added, target])
    chronological = messages.project_rows([target, added, removed])
    assert [item["message_id"] for item in newest_first] == ["T1"]
    assert [item["message_id"] for item in chronological] == ["T1"]
    assert "reactions" not in newest_first[0]
    assert "reactions" not in chronological[0]


def test_project_rows_passes_the_projection_options_through() -> None:
    names = known((GROUP, "Family"))
    rows = [group_row("T1", "conversation", {"conversation": "x" * 40}, ts=50)]
    projected = messages.project_rows(rows, text_limit=10, names=names, include_chat_name=True)
    assert projected[0]["chat_name"] == "Family"
    assert projected[0]["text"] == "x" * 10
    assert projected[0]["text_truncated"] is True


# ---- Evolution calls ------------------------------------------------------------------------------------------


@pytest.fixture
async def client(evo: FakeEvolution) -> AsyncIterator[EvolutionClient]:
    instance = EvolutionClient(BASE, TOKEN, transport=evo)
    yield instance
    await instance.aclose()


@pytest.fixture
def identity(make_identity: Callable[..., InstanceIdentity]) -> InstanceIdentity:
    return make_identity()


def find_messages_answer(records: list[dict], *, total: int, pages: int, current: int) -> dict:
    return {"messages": {"total": total, "pages": pages, "currentPage": current, "records": records}}


@pytest.mark.anyio
async def test_fetch_page_posts_page_size_as_offset_and_maps_the_answer(
    evo: FakeEvolution, client: EvolutionClient, identity: InstanceIdentity
) -> None:
    records = [row("conversation", {"conversation": "a"})]
    evo.on("POST", "/chat/findMessages/inst", json=find_messages_answer(records, total=61, pages=3, current=2))
    window = {"gte": "2026-09-01T00:00:00Z", "lte": "2026-09-30T00:00:00Z"}
    where = {"key": messages.chat_key_filter(CHAT), "messageTimestamp": window}

    page = await messages.fetch_page(client, identity, where=where, page_size=30, page=2)

    assert page == {"total": 61, "pages": 3, "page": 2, "records": records}
    request = evo.last("POST", "/chat/findMessages/inst")
    assert request.json == {"where": where, "offset": 30, "page": 2}
    assert request.headers["apikey"] == TOKEN


@pytest.mark.anyio
async def test_fetch_page_without_stored_messages_is_empty(
    evo: FakeEvolution, client: EvolutionClient, identity: InstanceIdentity
) -> None:
    evo.on(
        "POST",
        "/chat/findMessages/inst",
        json={"messages": {"total": 0, "pages": 0, "currentPage": 1, "records": []}},
    )
    assert await messages.fetch_page(client, identity, where={}, page_size=10, page=1) == {
        "total": 0,
        "pages": 0,
        "page": 1,
        "records": [],
    }
    evo.on("POST", "/chat/findMessages/inst", json={})
    assert (await messages.fetch_page(client, identity, where={}, page_size=10, page=3))["records"] == []


@pytest.mark.anyio
async def test_find_message_filters_by_id_and_chat_and_returns_the_row(
    evo: FakeEvolution, client: EvolutionClient, identity: InstanceIdentity
) -> None:
    stored = row("conversation", {"conversation": "hello"})
    evo.on("POST", "/chat/findMessages/inst", json=find_messages_answer([stored], total=1, pages=1, current=1))

    assert await messages.find_message(client, identity, "MSG0001", CHAT) == stored
    assert evo.last("POST", "/chat/findMessages/inst").json == {
        "where": {"key": {"id": "MSG0001", "remoteJid": CHAT, "remoteJidAlt": CHAT}},
        "offset": 1,
        "page": 1,
    }

    await messages.find_message(client, identity, "MSG0001")
    assert evo.last("POST", "/chat/findMessages/inst").json == {
        "where": {"key": {"id": "MSG0001"}},
        "offset": 2,
        "page": 1,
    }


@pytest.mark.anyio
async def test_find_message_missing_returns_none(
    evo: FakeEvolution, client: EvolutionClient, identity: InstanceIdentity
) -> None:
    evo.on("POST", "/chat/findMessages/inst", json=find_messages_answer([], total=0, pages=0, current=1))
    assert await messages.find_message(client, identity, "NOPE0001", CHAT) is None


@pytest.mark.anyio
async def test_find_message_without_a_chat_refuses_an_id_shared_by_several_chats(
    evo: FakeEvolution, client: EvolutionClient, identity: InstanceIdentity
) -> None:
    records = [row("conversation", {"conversation": "a"}), row("conversation", {"conversation": "b"}, chat=GROUP)]
    evo.on("POST", "/chat/findMessages/inst", json=find_messages_answer(records, total=2, pages=1, current=1))

    with pytest.raises(messages.AmbiguousMessageId) as caught:
        await messages.find_message(client, identity, "MSG0001")
    assert caught.value.chat_ids == [CHAT, GROUP]

    # with a chat the page holds one row and nothing is ambiguous
    assert await messages.find_message(client, identity, "MSG0001", CHAT) == records[0]
    assert evo.last("POST", "/chat/findMessages/inst").json["offset"] == 1


@pytest.mark.anyio
async def test_find_message_returns_the_first_of_duplicate_rows_in_one_chat(
    evo: FakeEvolution, client: EvolutionClient, identity: InstanceIdentity
) -> None:
    records = [row("conversation", {"conversation": "a"}), row("conversation", {"conversation": "a again"})]
    evo.on("POST", "/chat/findMessages/inst", json=find_messages_answer(records, total=2, pages=1, current=1))
    assert await messages.find_message(client, identity, "MSG0001") == records[0]


@pytest.mark.anyio
async def test_latest_message_asks_for_one_newest_row_of_the_chat(
    evo: FakeEvolution, client: EvolutionClient, identity: InstanceIdentity
) -> None:
    newest = row("conversation", {"conversation": "latest"})
    evo.on("POST", "/chat/findMessages/inst", json=find_messages_answer([newest], total=9, pages=9, current=1))

    assert await messages.latest_message(client, identity, CHAT) == newest
    assert evo.last("POST", "/chat/findMessages/inst").json == {
        "where": {"key": {"remoteJid": CHAT, "remoteJidAlt": CHAT}},
        "offset": 1,
        "page": 1,
    }

    evo.on("POST", "/chat/findMessages/inst", json=find_messages_answer([], total=0, pages=0, current=1))
    assert await messages.latest_message(client, identity, CHAT) is None
