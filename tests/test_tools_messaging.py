import base64
import json
import time
from types import SimpleNamespace

import httpx2
import pytest
from pydantic import ValidationError

from evolution_api_mcp import context
from evolution_api_mcp.errors import ToolExecutionError
from evolution_api_mcp.registry import BUSINESS
from evolution_api_mcp.tools import messaging
from evolution_api_mcp.tools.messaging import ContactCard, ListRow, ListSection, MessageButton
from tests.conftest import INSTANCE

PERSON = "393331234567@s.whatsapp.net"
DIGITS = "393331234567"
GROUP = "120363012345678901@g.us"
FIND_MESSAGES = f"/chat/findMessages/{INSTANCE}"
SENT = {
    "key": {"id": "3EB0SENT01", "remoteJid": PERSON, "fromMe": True},
    "pushName": "Me",
    "status": "PENDING",
    "message": {"conversation": "x"},
    "messageType": "conversation",
    "messageTimestamp": 1759180000,
}
SENT_RESULT = {
    "message_id": "3EB0SENT01",
    "chat_id": PERSON,
    "status": "PENDING",
    "timestamp": "2025-09-29T21:06:40Z",
}


def _path(endpoint):
    return f"/{endpoint}/{INSTANCE}"


def _accept(evo, endpoint):
    evo.on("POST", _path(endpoint), status=201, json=SENT)
    return _path(endpoint)


def _page(*records):
    return {"messages": {"total": len(records), "pages": 1, "currentPage": 1, "records": list(records)}}


def _row(
    message_id="3EB0ROW001", *, from_me=True, remote=PERSON, participant=None, message=None, kind="conversation", age=60
):
    key = {"id": message_id, "remoteJid": remote, "fromMe": from_me}
    if participant:
        key["participant"] = participant
    return {
        "id": f"row-{message_id}",
        "key": key,
        "pushName": "Ana",
        "messageType": kind,
        "message": message if message is not None else {"conversation": "original text"},
        "messageTimestamp": int(time.time()) - age,
        "MessageUpdate": [{"status": "DELIVERY_ACK"}],
    }


def _stored(evo, row):
    evo.on("POST", FIND_MESSAGES, json=_page(row))


def _result(text):
    return json.loads(text)


# --- send_text_message -----------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_send_text_posts_the_documented_body_and_returns_the_compact_result(evo, bound):
    path = _accept(evo, "message/sendText")
    with bound(evo, default_delay_ms=1200):
        result = _result(await messaging.send_text_message(chat="+39 333 123 4567", text="Hello Ana"))
    assert result == SENT_RESULT
    request = evo.last("POST", path)
    assert request.json == {"number": PERSON, "text": "Hello Ana", "delay": 1200}
    assert [r.path for r in evo.requests] == [path]


@pytest.mark.anyio
async def test_send_text_carries_quote_mentions_preview_and_explicit_delay(evo, bound):
    stored = _row("3EB0QUOTE01", from_me=False, remote=GROUP, participant="391110001111@s.whatsapp.net")
    _stored(evo, stored)
    path = _accept(evo, "message/sendText")
    with bound(evo):
        await messaging.send_text_message(
            chat=GROUP,
            text="see this",
            reply_to_message_id="3EB0QUOTE01",
            mention=["+39 111 000 1111"],
            mention_everyone=True,
            link_preview=False,
            delay_ms=500,
        )
    assert evo.last("POST", path).json == {
        "number": GROUP,
        "text": "see this",
        "delay": 500,
        "quoted": {
            "key": {
                "id": "3EB0QUOTE01",
                "remoteJid": GROUP,
                "fromMe": False,
                "participant": "391110001111@s.whatsapp.net",
            }
        },
        "mentioned": ["391110001111"],
        "mentionsEveryOne": True,
        "linkPreview": False,
    }


@pytest.mark.anyio
async def test_send_text_reply_to_an_unknown_message_sends_nothing(evo, bound):
    evo.on("POST", FIND_MESSAGES, json=_page())
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await messaging.send_text_message(chat=PERSON, text="hi", reply_to_message_id="3EB0MISSING")
    assert str(caught.value) == "Message 3EB0MISSING was not found in this chat. Use read_messages to find its id."
    assert [r.path for r in evo.requests] == [FIND_MESSAGES]


@pytest.mark.anyio
async def test_business_instances_get_bare_digits_and_refuse_mentions_and_groups(
    evo, bound, make_connection, make_identity
):
    path = _accept(evo, "message/sendText")
    conn = make_connection(identity=make_identity(BUSINESS))
    with bound(evo, conn):
        await messaging.send_text_message(chat=PERSON, text="hello")
        assert evo.last("POST", path).json == {"number": DIGITS, "text": "hello", "delay": 0}

        with pytest.raises(ToolExecutionError, match="Mentions work only on WhatsApp Web"):
            await messaging.send_text_message(chat=PERSON, text="hello", mention=[DIGITS])
        with pytest.raises(ToolExecutionError, match="phone numbers only; groups and @lid ids need"):
            await messaging.send_text_message(chat=GROUP, text="hello")
    assert len(evo.requests) == 1


@pytest.mark.anyio
async def test_business_window_rejection_returned_as_201_is_a_refusal(evo, bound, make_connection, make_identity):
    evo.on(
        "POST",
        _path("message/sendText"),
        status=201,
        json={"message": "(#131047) Re-engagement message", "type": "OAuthException", "code": 131047, "error_data": {}},
    )
    conn = make_connection(identity=make_identity(BUSINESS))
    with bound(evo, conn), pytest.raises(ToolExecutionError) as caught:
        await messaging.send_text_message(chat=PERSON, text="hello")
    assert "refused the message (code 131047)" in str(caught.value)
    assert "send_template_message" in str(caught.value)


@pytest.mark.anyio
async def test_a_send_that_times_out_is_uncertain_and_shows_the_chat_state(evo, bound):
    evo.fail("POST", _path("message/sendText"), httpx2.ReadTimeout("timed out"))
    _stored(evo, _row("3EB0OUT001"))
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await messaging.send_text_message(chat=PERSON, text="hello")
    assert str(caught.value).startswith("UNCERTAIN: Evolution did not confirm the result")
    assert "3EB0OUT001" in str(caught.value)


# --- send_media_message ----------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_send_media_image_body_with_caption_and_mimetype(evo, bound):
    path = _accept(evo, "message/sendMedia")
    with bound(evo):
        result = _result(
            await messaging.send_media_message(
                chat=PERSON,
                media_url="https://example.com/a/photo.jpg",
                media_type="image",
                caption="Look",
                mimetype="image/jpeg",
            )
        )
    assert result == SENT_RESULT
    assert evo.last("POST", path).json == {
        "number": PERSON,
        "mediatype": "image",
        "media": "https://example.com/a/photo.jpg",
        "caption": "Look",
        "mimetype": "image/jpeg",
        "delay": 0,
    }


@pytest.mark.anyio
async def test_send_media_document_file_name_defaults_to_the_url_segment_or_document(evo, bound):
    path = _accept(evo, "message/sendMedia")
    with bound(evo):
        await messaging.send_media_message(
            chat=PERSON, media_url="https://example.com/files/report%202026.pdf?x=1", media_type="document"
        )
        assert evo.last("POST", path).json["fileName"] == "report%202026.pdf"

        await messaging.send_media_message(chat=PERSON, media_url="https://example.com/", media_type="document")
        assert evo.last("POST", path).json["fileName"] == "document"

        await messaging.send_media_message(
            chat=PERSON, media_url="https://example.com/x", media_type="document", file_name="Invoice.pdf"
        )
        assert evo.last("POST", path).json["fileName"] == "Invoice.pdf"

        await messaging.send_media_message(chat=PERSON, media_url="https://example.com/v.mp4", media_type="video")
        assert "fileName" not in evo.last("POST", path).json


@pytest.mark.anyio
async def test_send_media_audio_refuses_a_caption_before_calling_evolution(evo, bound):
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await messaging.send_media_message(
            chat=PERSON, media_url="https://example.com/a.mp3", media_type="audio", caption="listen"
        )
    assert str(caught.value) == "Audio messages carry no caption; send the text separately."
    assert evo.requests == []


# --- send_local_file -------------------------------------------------------------------------------------------


@pytest.fixture
def local_root(tmp_path, monkeypatch):
    root = tmp_path / "files"
    root.mkdir()
    monkeypatch.setattr(context, "local_config", lambda: SimpleNamespace(file_roots=(root,)))
    return root


@pytest.mark.anyio
async def test_send_local_file_sends_the_base64_of_the_file_as_the_detected_kind(evo, bound, local_root):
    payload = b"\x89PNG\r\n\x1a\nfake image bytes"
    (local_root / "photo.png").write_bytes(payload)
    path = _accept(evo, "message/sendMedia")
    with bound(evo):
        result = _result(
            await messaging.send_local_file(chat=PERSON, path=str(local_root / "photo.png"), caption="From my desk")
        )
    assert result == {**SENT_RESULT, "file": "photo.png", "size_bytes": len(payload)}
    assert evo.last("POST", path).json == {
        "number": PERSON,
        "mediatype": "image",
        "mimetype": "image/png",
        "fileName": "photo.png",
        "media": base64.b64encode(payload).decode(),
        "caption": "From my desk",
        "delay": 0,
    }


@pytest.mark.anyio
async def test_send_local_file_as_document_keeps_the_original_mimetype(evo, bound, local_root):
    (local_root / "clip.mp4").write_bytes(b"video bytes")
    path = _accept(evo, "message/sendMedia")
    with bound(evo):
        await messaging.send_local_file(chat=PERSON, path=str(local_root / "clip.mp4"), send_as="document")
    body = evo.last("POST", path).json
    assert body["mediatype"] == "document"
    assert body["mimetype"] == "video/mp4"
    assert body["fileName"] == "clip.mp4"
    assert "caption" not in body


@pytest.mark.anyio
async def test_send_local_file_as_voice_note_uses_the_audio_endpoint(evo, bound, local_root):
    (local_root / "note.mp3").write_bytes(b"audio bytes")
    path = _accept(evo, "message/sendWhatsAppAudio")
    with bound(evo):
        await messaging.send_local_file(chat=PERSON, path=str(local_root / "note.mp3"), send_as="voice_note")
    assert evo.last("POST", path).json == {
        "number": PERSON,
        "audio": base64.b64encode(b"audio bytes").decode(),
        "delay": 0,
    }


@pytest.mark.anyio
async def test_send_local_file_refusals_send_nothing(evo, bound, local_root, tmp_path):
    (local_root / "photo.png").write_bytes(b"png")
    (local_root / "note.mp3").write_bytes(b"mp3")
    (local_root / ".secret.png").write_bytes(b"png")
    (tmp_path / "outside.png").write_bytes(b"png")
    with bound(evo):
        with pytest.raises(ToolExecutionError, match="voice_note' needs an audio file; photo.png is image/png."):
            await messaging.send_local_file(chat=PERSON, path=str(local_root / "photo.png"), send_as="voice_note")
        with pytest.raises(ToolExecutionError) as caught:
            await messaging.send_local_file(
                chat=PERSON, path=str(local_root / "note.mp3"), send_as="voice_note", caption="hear this"
            )
        assert str(caught.value) == "Voice notes carry no caption; send the text separately."
        with pytest.raises(ToolExecutionError) as caught:
            await messaging.send_local_file(chat=PERSON, path=str(local_root / "note.mp3"), caption="hear this")
        assert str(caught.value) == "Audio messages carry no caption; send the text separately."
        with pytest.raises(ToolExecutionError, match="is outside the folders this server may read"):
            await messaging.send_local_file(chat=PERSON, path=str(tmp_path / "outside.png"))
        with pytest.raises(ToolExecutionError, match="Hidden files and folders"):
            await messaging.send_local_file(chat=PERSON, path=str(local_root / ".secret.png"))
        with pytest.raises(ToolExecutionError, match="Give an absolute path"):
            await messaging.send_local_file(chat=PERSON, path="photo.png")
    assert evo.requests == []


# --- voice note, video note, sticker ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_send_voice_note_video_note_and_sticker_bodies(evo, bound):
    voice = _accept(evo, "message/sendWhatsAppAudio")
    video = _accept(evo, "message/sendPtv")
    sticker = _accept(evo, "message/sendSticker")
    with bound(evo):
        voice_result = _result(await messaging.send_voice_note(chat=PERSON, audio_url="https://example.com/v.ogg"))
        assert voice_result == SENT_RESULT
        await messaging.send_video_note(chat=PERSON, video_url="https://example.com/v.mp4", delay_ms=100)
        await messaging.send_sticker(chat=PERSON, sticker_url="https://example.com/s.webp")
    assert evo.last("POST", voice).json == {"number": PERSON, "audio": "https://example.com/v.ogg", "delay": 0}
    assert evo.last("POST", video).json == {"number": PERSON, "video": "https://example.com/v.mp4", "delay": 100}
    assert evo.last("POST", sticker).json == {"number": PERSON, "sticker": "https://example.com/s.webp", "delay": 0}


@pytest.mark.anyio
async def test_send_voice_note_quotes_the_replied_message(evo, bound):
    _stored(evo, _row("3EB0QUOTE02", from_me=False))
    path = _accept(evo, "message/sendWhatsAppAudio")
    with bound(evo):
        await messaging.send_voice_note(
            chat=PERSON, audio_url="https://example.com/v.ogg", reply_to_message_id="3EB0QUOTE02"
        )
    assert evo.last("POST", path).json["quoted"] == {"key": {"id": "3EB0QUOTE02", "remoteJid": PERSON, "fromMe": False}}


# --- location and contact cards --------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_send_location_body_on_business_uses_bare_digits(evo, bound, make_connection, make_identity):
    path = _accept(evo, "message/sendLocation")
    with bound(evo, make_connection(identity=make_identity(BUSINESS))):
        result = _result(
            await messaging.send_location(
                chat=PERSON, latitude=41.3874, longitude=2.1686, name="Office", address="Carrer Example 1, Barcelona"
            )
        )
    assert result == SENT_RESULT
    assert evo.last("POST", path).json == {
        "number": DIGITS,
        "latitude": 41.3874,
        "longitude": 2.1686,
        "name": "Office",
        "address": "Carrer Example 1, Barcelona",
        "delay": 0,
    }


@pytest.mark.anyio
async def test_send_contact_card_normalizes_numbers_and_omits_empty_fields(evo, bound):
    path = _accept(evo, "message/sendContact")
    cards = [
        ContactCard(full_name="Ana Ruiz", phone_number="+39 (333) 123-4567"),
        ContactCard(
            full_name="Luca Bianchi",
            phone_number="393331112222",
            organization="Acme",
            email="luca@example.com",
            url="https://example.com",
        ),
    ]
    with bound(evo):
        await messaging.send_contact_card(chat=PERSON, contacts=cards)
    assert evo.last("POST", path).json == {
        "number": PERSON,
        "contact": [
            {"fullName": "Ana Ruiz", "wuid": DIGITS, "phoneNumber": f"+{DIGITS}"},
            {
                "fullName": "Luca Bianchi",
                "wuid": "393331112222",
                "phoneNumber": "+393331112222",
                "organization": "Acme",
                "email": "luca@example.com",
                "url": "https://example.com",
            },
        ],
        "delay": 0,
    }


@pytest.mark.parametrize("phone", ["12345", "1234567890123456", "abc-def-ghij", ""])
def test_contact_card_refuses_numbers_outside_10_to_15_digits(phone):
    with pytest.raises(ValidationError, match="10 to 15 digits"):
        ContactCard(full_name="Ana", phone_number=phone)


# --- poll ------------------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_send_poll_body_and_duplicate_options_refusal(evo, bound):
    path = _accept(evo, "message/sendPoll")
    with bound(evo):
        await messaging.send_poll(chat=GROUP, question="Lunch?", options=["Pizza", "Sushi"], max_selections=0)
        assert evo.last("POST", path).json == {
            "number": GROUP,
            "name": "Lunch?",
            "selectableCount": 0,
            "values": ["Pizza", "Sushi"],
            "delay": 0,
        }
        await messaging.send_poll(chat=GROUP, question="Lunch?", options=["Pizza", "Sushi", "Salad"])
        assert evo.last("POST", path).json["selectableCount"] == 1

        with pytest.raises(ToolExecutionError, match="Poll options must all be different. Nothing was sent."):
            await messaging.send_poll(chat=GROUP, question="Lunch?", options=["Pizza", "Pizza"])
    assert len(evo.requests) == 2


# --- list and button messages ----------------------------------------------------------------------------------


def _sections(*row_counts):
    return [
        ListSection(
            title=f"Section {index}",
            rows=[
                ListRow(row_id=f"r{index}-{n}", title=f"Row {n}", description="More" if n == 0 else None)
                for n in range(count)
            ],
        )
        for index, count in enumerate(row_counts)
    ]


@pytest.mark.anyio
async def test_send_list_message_body_maps_rows_and_footer(evo, bound):
    path = _accept(evo, "message/sendList")
    with bound(evo):
        result = _result(
            await messaging.send_list_message(
                chat=PERSON,
                title="Menu",
                description="Pick one",
                button_text="Open",
                sections=_sections(2),
                footer="Thanks",
            )
        )
    assert result == SENT_RESULT
    assert evo.last("POST", path).json == {
        "number": PERSON,
        "title": "Menu",
        "description": "Pick one",
        "buttonText": "Open",
        "footerText": "Thanks",
        "sections": [
            {
                "title": "Section 0",
                "rows": [
                    {"title": "Row 0", "rowId": "r0-0", "description": "More"},
                    {"title": "Row 1", "rowId": "r0-1"},
                ],
            }
        ],
        "delay": 0,
    }


@pytest.mark.anyio
async def test_send_list_message_defaults_to_an_empty_footer(evo, bound):
    path = _accept(evo, "message/sendList")
    with bound(evo):
        await messaging.send_list_message(
            chat=PERSON, title="Menu", description="Pick one", button_text="Open", sections=_sections(1)
        )
    assert evo.last("POST", path).json["footerText"] == ""


@pytest.mark.anyio
async def test_business_lists_hold_ten_rows_in_total(evo, bound, make_connection, make_identity):
    path = _accept(evo, "message/sendList")
    with bound(evo, make_connection(identity=make_identity(BUSINESS))):
        with pytest.raises(ToolExecutionError) as caught:
            await messaging.send_list_message(
                chat=PERSON, title="Menu", description="Pick", button_text="Open", sections=_sections(6, 5)
            )
        assert str(caught.value) == "WhatsApp Business Platform lists hold at most 10 rows in total."
        assert evo.requests == []

        await messaging.send_list_message(
            chat=PERSON, title="Menu", description="Pick", button_text="Open", sections=_sections(5, 5)
        )
    assert evo.last("POST", path).json["number"] == DIGITS


@pytest.mark.anyio
async def test_baileys_lists_may_hold_more_than_ten_rows(evo, bound):
    _accept(evo, "message/sendList")
    with bound(evo):
        await messaging.send_list_message(
            chat=PERSON, title="Menu", description="Pick", button_text="Open", sections=_sections(10, 10)
        )
    assert len(evo.requests) == 1


@pytest.mark.anyio
async def test_send_button_message_body_for_every_button_type(evo, bound):
    path = _accept(evo, "message/sendButtons")
    buttons = [
        MessageButton(type="reply", text="Yes", reply_id="yes"),
        MessageButton(type="url", text="Site", url="https://example.com"),
        MessageButton(type="call", text="Call us", phone_number="+393331234567"),
    ]
    with bound(evo):
        result = _result(
            await messaging.send_button_message(
                chat=PERSON, title="Order", description="Confirm?", buttons=buttons, footer="Acme"
            )
        )
        await messaging.send_button_message(
            chat=PERSON,
            title="Code",
            description="Your code",
            buttons=[MessageButton(type="copy", text="Copy", copy_code="ABC123")],
        )
    assert result == SENT_RESULT
    first, last = [r for r in evo.requests if r.path == path]
    assert first.json == {
        "number": PERSON,
        "title": "Order",
        "description": "Confirm?",
        "footer": "Acme",
        "buttons": [
            {"type": "reply", "displayText": "Yes", "id": "yes"},
            {"type": "url", "displayText": "Site", "url": "https://example.com"},
            {"type": "call", "displayText": "Call us", "phoneNumber": "+393331234567"},
        ],
        "delay": 0,
    }
    assert last.json["buttons"] == [{"type": "copy", "displayText": "Copy", "copyCode": "ABC123"}]
    assert "footer" not in last.json


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("button_type", "missing"),
    [("reply", "reply_id"), ("url", "url"), ("call", "phone_number"), ("copy", "copy_code")],
)
async def test_a_button_without_its_required_field_is_refused(evo, bound, button_type, missing):
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await messaging.send_button_message(
            chat=PERSON,
            title="Order",
            description="Confirm?",
            buttons=[MessageButton(type=button_type, text="Go")],
        )
    assert str(caught.value) == f"A {button_type} button ('Go') needs {missing}. Nothing was sent."
    assert evo.requests == []


@pytest.mark.anyio
async def test_business_buttons_are_reply_only(evo, bound, make_connection, make_identity):
    path = _accept(evo, "message/sendButtons")
    with bound(evo, make_connection(identity=make_identity(BUSINESS))):
        with pytest.raises(ToolExecutionError) as caught:
            await messaging.send_button_message(
                chat=PERSON,
                title="Order",
                description="Confirm?",
                buttons=[MessageButton(type="url", text="Site", url="https://example.com")],
            )
        assert str(caught.value) == "WhatsApp Business Platform instances support reply buttons only."
        assert evo.requests == []

        await messaging.send_button_message(
            chat=PERSON,
            title="Order",
            description="Confirm?",
            buttons=[MessageButton(type="reply", text="Yes", reply_id="yes")],
        )
    assert evo.last("POST", path).json["number"] == DIGITS


# --- send_chat_presence ----------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_send_chat_presence_posts_number_presence_and_delay(evo, bound):
    evo.on("POST", _path("chat/sendPresence"), status=201, json={"presence": "composing"})
    with bound(evo):
        result = _result(await messaging.send_chat_presence(chat=GROUP, presence="composing"))
        assert result == {"chat_id": GROUP, "presence": "composing", "duration_ms": 3000}
        assert evo.last("POST", _path("chat/sendPresence")).json == {
            "number": GROUP,
            "presence": "composing",
            "delay": 3000,
        }
        await messaging.send_chat_presence(chat=PERSON, presence="recording", duration_ms=5000)
    assert evo.last("POST", _path("chat/sendPresence")).json == {
        "number": PERSON,
        "presence": "recording",
        "delay": 5000,
    }


@pytest.mark.anyio
async def test_send_chat_presence_is_refused_on_business_instances(evo, bound, make_connection, make_identity):
    with bound(evo, make_connection(identity=make_identity(BUSINESS))), pytest.raises(ToolExecutionError) as caught:
        await messaging.send_chat_presence(chat=PERSON, presence="composing")
    assert "needs WHATSAPP-BAILEYS, and this instance uses WHATSAPP-BUSINESS" in str(caught.value)
    assert evo.requests == []


# --- react_to_message ------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_react_sends_the_stored_key_and_the_emoji(evo, bound):
    row = _row("3EB0TARGET1", from_me=False, remote=GROUP, participant="391110001111@s.whatsapp.net")
    _stored(evo, row)
    path = _accept(evo, "message/sendReaction")
    with bound(evo):
        result = _result(await messaging.react_to_message(chat=GROUP, message_id="3EB0TARGET1", emoji="👍"))
    assert result == {**SENT_RESULT, "reacted_to": "3EB0TARGET1", "emoji": "👍"}
    assert evo.last("POST", path).json == {
        "key": {
            "id": "3EB0TARGET1",
            "remoteJid": GROUP,
            "fromMe": False,
            "participant": "391110001111@s.whatsapp.net",
        },
        "reaction": "👍",
    }
    where = evo.last("POST", FIND_MESSAGES).json["where"]
    assert where["key"] == {"id": "3EB0TARGET1", "remoteJid": GROUP, "remoteJidAlt": GROUP}


@pytest.mark.anyio
async def test_react_with_an_empty_emoji_removes_the_reaction(evo, bound):
    _stored(evo, _row("3EB0TARGET2", from_me=False))
    path = _accept(evo, "message/sendReaction")
    with bound(evo):
        result = _result(await messaging.react_to_message(chat=PERSON, message_id="3EB0TARGET2", emoji=""))
    assert evo.last("POST", path).json["reaction"] == ""
    assert result["emoji"] == ""


@pytest.mark.anyio
async def test_react_to_an_unknown_message_sends_nothing(evo, bound):
    evo.on("POST", FIND_MESSAGES, json=_page())
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await messaging.react_to_message(chat=PERSON, message_id="3EB0MISSING", emoji="👍")
    assert str(caught.value) == "Message 3EB0MISSING was not found in this chat. Use read_messages to find its id."
    assert [r.path for r in evo.requests] == [FIND_MESSAGES]


# --- edit_message ----------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_edit_message_posts_the_key_of_the_stored_message(evo, bound):
    _stored(evo, _row("3EB0OWN0001", remote=PERSON))
    evo.on("POST", _path("chat/updateMessage"), status=201, json={"key": {"id": "3EB0OWN0001"}})
    with bound(evo):
        result = _result(await messaging.edit_message(chat=PERSON, message_id="3EB0OWN0001", new_text="fixed text"))
    assert result == {"edited": True, "message_id": "3EB0OWN0001", "chat_id": PERSON}
    assert evo.last("POST", _path("chat/updateMessage")).json == {
        "number": PERSON,
        "text": "fixed text",
        "key": {"id": "3EB0OWN0001", "remoteJid": PERSON, "fromMe": True},
    }


@pytest.mark.anyio
async def test_edit_message_accepts_image_captions(evo, bound):
    row = _row("3EB0IMG0001", kind="imageMessage", message={"imageMessage": {"caption": "old caption"}})
    _stored(evo, row)
    evo.on("POST", _path("chat/updateMessage"), status=201, json={})
    with bound(evo):
        await messaging.edit_message(chat=PERSON, message_id="3EB0IMG0001", new_text="new caption")
    assert evo.last("POST", _path("chat/updateMessage")).json["text"] == "new caption"


@pytest.mark.anyio
async def test_edit_message_refusals_change_nothing(evo, bound):
    with bound(evo):
        _stored(evo, _row("3EB0THEIRS01", from_me=False))
        with pytest.raises(ToolExecutionError) as caught:
            await messaging.edit_message(chat=PERSON, message_id="3EB0THEIRS01", new_text="x")
        assert str(caught.value) == "Only messages sent from this number can be edited."

        _stored(evo, _row("3EB0AUDIO001", kind="audioMessage", message={"audioMessage": {"ptt": True}}))
        with pytest.raises(ToolExecutionError) as caught:
            await messaging.edit_message(chat=PERSON, message_id="3EB0AUDIO001", new_text="x")
        assert str(caught.value) == "Only text messages and image/video captions can be edited."

        _stored(evo, _row("3EB0OLD00001", age=20 * 60 + 5))
        with pytest.raises(ToolExecutionError) as caught:
            await messaging.edit_message(chat=PERSON, message_id="3EB0OLD00001", new_text="x")
        assert str(caught.value) == (
            "WhatsApp allows edits for 15 minutes after sending; this message is 20 minutes old."
        )

        evo.on("POST", FIND_MESSAGES, json=_page())
        with pytest.raises(ToolExecutionError, match="Message 3EB0MISSING was not found in this chat"):
            await messaging.edit_message(chat=PERSON, message_id="3EB0MISSING", new_text="x")
    assert all(r.path == FIND_MESSAGES for r in evo.requests)


@pytest.mark.anyio
async def test_edit_message_timeout_is_uncertain_and_rereads_the_message(evo, bound):
    _stored(evo, _row("3EB0OWN0002"))
    evo.fail("POST", _path("chat/updateMessage"), httpx2.ReadTimeout("timed out"))
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await messaging.edit_message(chat=PERSON, message_id="3EB0OWN0002", new_text="fixed text")
    text = str(caught.value)
    assert text.startswith("UNCERTAIN: Evolution did not confirm the result")
    assert "original text" in text and "3EB0OWN0002" in text
    assert text.endswith("Do NOT repeat the call before checking this state.")


# --- delete_message_for_everyone -------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_delete_for_everyone_sends_a_delete_with_the_stored_key(evo, bound):
    _stored(evo, _row("3EB0OWN0003", remote=GROUP, participant="393339990000@s.whatsapp.net"))
    evo.on("DELETE", _path("chat/deleteMessageForEveryone"), json={"key": {"id": "3EB0OWN0003"}})
    with bound(evo):
        result = _result(await messaging.delete_message_for_everyone(chat=GROUP, message_id="3EB0OWN0003"))
    assert result == {"deleted_for_everyone": True, "message_id": "3EB0OWN0003"}
    assert evo.last("DELETE", _path("chat/deleteMessageForEveryone")).json == {
        "id": "3EB0OWN0003",
        "remoteJid": GROUP,
        "fromMe": True,
        "participant": "393339990000@s.whatsapp.net",
    }


@pytest.mark.anyio
async def test_delete_for_everyone_without_a_participant_omits_it(evo, bound):
    _stored(evo, _row("3EB0OWN0004"))
    evo.on("DELETE", _path("chat/deleteMessageForEveryone"), json={})
    with bound(evo):
        await messaging.delete_message_for_everyone(chat=PERSON, message_id="3EB0OWN0004")
    assert evo.last("DELETE", _path("chat/deleteMessageForEveryone")).json == {
        "id": "3EB0OWN0004",
        "remoteJid": PERSON,
        "fromMe": True,
    }


@pytest.mark.anyio
async def test_delete_for_everyone_refuses_messages_from_other_people(evo, bound):
    _stored(evo, _row("3EB0THEIRS02", from_me=False))
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await messaging.delete_message_for_everyone(chat=PERSON, message_id="3EB0THEIRS02")
    assert str(caught.value) == "Only messages sent from this number can be deleted for everyone."
    assert [r.path for r in evo.requests] == [FIND_MESSAGES]


@pytest.mark.anyio
async def test_delete_for_everyone_is_refused_without_the_irreversible_grant(evo, bound, make_connection):
    with bound(evo, make_connection(irreversible_granted=False)), pytest.raises(ToolExecutionError) as caught:
        await messaging.delete_message_for_everyone(chat=PERSON, message_id="3EB0OWN0005")
    assert "EVOLUTION_MCP_ALLOW_IRREVERSIBLE=yes" in str(caught.value)
    assert evo.requests == []
