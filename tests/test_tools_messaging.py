import base64
import json
import time
from types import SimpleNamespace

import httpx2
import pytest
from pydantic import ValidationError

from evolution_api_mcp import context, media, ratelimit
from evolution_api_mcp.errors import ToolExecutionError
from evolution_api_mcp.registry import BUSINESS
from evolution_api_mcp.tools import messaging
from evolution_api_mcp.tools.messaging import ContactCard, ListRow, ListSection, MessageButton
from tests.conftest import INSTANCE
from tests.fakes import program_directory

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
    assert str(caught.value) == (
        "Message 3EB0MISSING was not found in this chat. "
        "read_messages, search_messages and list_recent_messages show message ids. Nothing was sent."
    )
    assert [r.path for r in evo.requests] == [FIND_MESSAGES]


@pytest.mark.anyio
async def test_a_reply_without_a_chat_goes_to_the_chat_of_the_message(evo, bound):
    _stored(evo, _row("3EB0QUOTE02", from_me=False, remote=GROUP, participant="391110001111@s.whatsapp.net"))
    path = _accept(evo, "message/sendText")
    with bound(evo):
        await messaging.send_text_message(text="ok, thanks", reply_to_message_id="3EB0QUOTE02")
    assert evo.last("POST", FIND_MESSAGES).json == {"where": {"key": {"id": "3EB0QUOTE02"}}, "offset": 2, "page": 1}
    body = evo.last("POST", path).json
    assert body["number"] == GROUP
    assert body["text"] == "ok, thanks"
    assert body["quoted"]["key"] == {
        "id": "3EB0QUOTE02",
        "remoteJid": GROUP,
        "fromMe": False,
        "participant": "391110001111@s.whatsapp.net",
    }
    assert [r.path for r in evo.requests] == [FIND_MESSAGES, path]


@pytest.mark.anyio
async def test_a_reply_refuses_a_chat_the_message_does_not_belong_to(evo, bound):
    _stored(evo, _row("3EB0QUOTE03", from_me=False, remote=GROUP))  # the fake does not apply the chat filter
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await messaging.send_text_message(chat=PERSON, text="hi", reply_to_message_id="3EB0QUOTE03")
    assert str(caught.value) == f"Message 3EB0QUOTE03 belongs to chat {GROUP}, not {PERSON}. Nothing was sent."
    assert [r.path for r in evo.requests] == [FIND_MESSAGES]


@pytest.mark.anyio
async def test_a_send_with_neither_chat_nor_reply_is_refused_before_calling_evolution(evo, bound):
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await messaging.send_text_message(text="hi")
    assert str(caught.value) == "Give chat, or reply_to_message_id to answer in that message's chat. Nothing was sent."
    assert evo.requests == []


@pytest.mark.anyio
async def test_a_reply_to_an_id_shared_by_two_chats_names_them_and_sends_nothing(evo, bound):
    evo.on("POST", FIND_MESSAGES, json=_page(_row("3EB0QUOTE05", from_me=False), _row("3EB0QUOTE05", remote=GROUP)))
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await messaging.send_text_message(text="hi", reply_to_message_id="3EB0QUOTE05")
    assert str(caught.value) == (
        f"Message 3EB0QUOTE05 exists in 2 chats ({PERSON}, {GROUP}). Pass chat to pick one. Nothing was sent."
    )
    assert [r.path for r in evo.requests] == [FIND_MESSAGES]


_REPLY_ONLY = [
    ("message/sendText", "send_text_message", {"text": "hi"}),
    (
        "message/sendMedia",
        "send_media_message",
        {"media_url": "https://example.com/a.jpg", "media_type": "image"},
    ),
    ("message/sendWhatsAppAudio", "send_voice_note", {"audio_url": "https://example.com/a.ogg"}),
    ("message/sendPtv", "send_video_note", {"video_url": "https://example.com/a.mp4"}),
    ("message/sendSticker", "send_sticker", {"sticker_url": "https://example.com/a.webp"}),
    (
        "message/sendLocation",
        "send_location",
        {"latitude": 45.0, "longitude": 9.0, "name": "Spot", "address": "Main Street 1"},
    ),
    (
        "message/sendContact",
        "send_contact_card",
        {"contacts": [ContactCard(full_name="Ana", phone_number="393331234567")]},
    ),
    ("message/sendPoll", "send_poll", {"question": "Lunch?", "options": ["Pizza", "Sushi"]}),
    (
        "message/sendList",
        "send_list_message",
        {
            "title": "Menu",
            "description": "Pick one",
            "button_text": "Open",
            "sections": [ListSection(title="Food", rows=[ListRow(row_id="1", title="Pizza")])],
        },
    ),
    (
        "message/sendButtons",
        "send_button_message",
        {
            "title": "Menu",
            "description": "Pick one",
            "buttons": [MessageButton(type="reply", text="Yes", reply_id="yes")],
        },
    ),
]


@pytest.mark.anyio
@pytest.mark.parametrize(("endpoint", "tool", "kwargs"), _REPLY_ONLY, ids=[case[1] for case in _REPLY_ONLY])
async def test_every_send_tool_answers_in_the_chat_of_the_message_it_quotes(evo, bound, endpoint, tool, kwargs):
    _stored(evo, _row("3EB0QUOTE04", from_me=False, remote=GROUP, participant="391110001111@s.whatsapp.net"))
    path = _accept(evo, endpoint)
    with bound(evo):
        await getattr(messaging, tool)(reply_to_message_id="3EB0QUOTE04", **kwargs)
    body = evo.last("POST", path).json
    assert body["number"] == GROUP
    assert body["quoted"]["key"]["id"] == "3EB0QUOTE04"

    with bound(evo), pytest.raises(ToolExecutionError, match="Give chat, or reply_to_message_id"):
        await getattr(messaging, tool)(**kwargs)


@pytest.mark.anyio
async def test_send_text_accepts_an_exact_name_and_refuses_a_partial_one(evo, bound):
    program_directory(evo, INSTANCE, contacts=[{"remoteJid": PERSON, "pushName": "Mario Rossi", "isSaved": True}])
    path = _accept(evo, "message/sendText")
    with bound(evo):
        await messaging.send_text_message(chat="Mario Rossi", text="hello")
        assert evo.last("POST", path).json["number"] == PERSON
        sent = len([r for r in evo.requests if r.path == path])
        with pytest.raises(ToolExecutionError, match=r"named exactly 'Mario'.*Nothing was sent\.$"):
            await messaging.send_text_message(chat="Mario", text="hello")
    assert len([r for r in evo.requests if r.path == path]) == sent


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


# --- send_local_files ------------------------------------------------------------------------------------------


@pytest.fixture
def local_root(tmp_path, monkeypatch):
    root = tmp_path / "files"
    root.mkdir()
    monkeypatch.setattr(context, "local_config", lambda: SimpleNamespace(file_roots=(root,)))
    return root


def _sent_file(name, size, message_id="3EB0SENT01"):
    return {"file": name, "size_bytes": size, "message_id": message_id, "status": "PENDING"}


@pytest.mark.anyio
async def test_send_local_files_sends_the_base64_of_the_file_as_the_detected_kind(evo, bound, local_root):
    payload = b"\x89PNG\r\n\x1a\nfake image bytes"
    (local_root / "photo.png").write_bytes(payload)
    path = _accept(evo, "message/sendMedia")
    with bound(evo):
        result = _result(
            await messaging.send_local_files(paths=[str(local_root / "photo.png")], chat=PERSON, caption="From my desk")
        )
    assert result == {"chat_id": PERSON, "files": [_sent_file("photo.png", len(payload))]}
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
async def test_send_local_files_as_document_keeps_the_original_mimetype(evo, bound, local_root):
    (local_root / "clip.mp4").write_bytes(b"video bytes")
    path = _accept(evo, "message/sendMedia")
    with bound(evo):
        await messaging.send_local_files(paths=[str(local_root / "clip.mp4")], chat=PERSON, send_as="document")
    body = evo.last("POST", path).json
    assert body["mediatype"] == "document"
    assert body["mimetype"] == "video/mp4"
    assert body["fileName"] == "clip.mp4"
    assert "caption" not in body


@pytest.mark.anyio
async def test_send_local_files_as_voice_note_uses_the_audio_endpoint(evo, bound, local_root):
    (local_root / "note.mp3").write_bytes(b"audio bytes")
    path = _accept(evo, "message/sendWhatsAppAudio")
    with bound(evo):
        await messaging.send_local_files(paths=[str(local_root / "note.mp3")], chat=PERSON, send_as="voice_note")
    assert evo.last("POST", path).json == {
        "number": PERSON,
        "audio": base64.b64encode(b"audio bytes").decode(),
        "delay": 0,
    }


@pytest.mark.anyio
async def test_send_local_files_refusals_send_nothing(evo, bound, local_root, tmp_path):
    (local_root / "photo.png").write_bytes(b"png")
    (local_root / "note.mp3").write_bytes(b"mp3")
    (local_root / ".secret.png").write_bytes(b"png")
    (tmp_path / "outside.png").write_bytes(b"png")
    with bound(evo):
        with pytest.raises(ToolExecutionError, match="voice_note' needs an audio file; photo.png is image/png."):
            await messaging.send_local_files(paths=[str(local_root / "photo.png")], chat=PERSON, send_as="voice_note")
        with pytest.raises(ToolExecutionError) as caught:
            await messaging.send_local_files(
                paths=[str(local_root / "note.mp3")], chat=PERSON, send_as="voice_note", caption="hear this"
            )
        assert str(caught.value) == "Voice notes carry no caption; send the text separately."
        with pytest.raises(ToolExecutionError) as caught:
            await messaging.send_local_files(paths=[str(local_root / "note.mp3")], chat=PERSON, caption="hear this")
        assert str(caught.value) == "Audio messages carry no caption; send the text separately."
        with pytest.raises(ToolExecutionError, match="is outside the folders this server may read"):
            await messaging.send_local_files(paths=[str(tmp_path / "outside.png")], chat=PERSON)
        with pytest.raises(ToolExecutionError, match="Hidden files and folders"):
            await messaging.send_local_files(paths=[str(local_root / ".secret.png")], chat=PERSON)
        with pytest.raises(ToolExecutionError, match="Give an absolute path"):
            await messaging.send_local_files(paths=["photo.png"], chat=PERSON)
    assert evo.requests == []


@pytest.mark.anyio
async def test_send_local_files_sends_in_order_with_caption_quote_and_mentions_on_the_first_file_only(
    evo, bound, local_root
):
    for name in ("a.png", "b.pdf", "c.mp4"):
        (local_root / name).write_bytes(name.encode())
    _stored(evo, _row("3EB0QUOTE06", from_me=False))
    path = _accept(evo, "message/sendMedia")
    with bound(evo, default_delay_ms=300):
        result = _result(
            await messaging.send_local_files(
                paths=[str(local_root / "a.png"), str(local_root / "b.pdf"), str(local_root / "c.mp4")],
                chat=PERSON,
                caption="Three files",
                reply_to_message_id="3EB0QUOTE06",
                mention=["+39 333 123 4567"],
            )
        )
    assert result == {
        "chat_id": PERSON,
        "files": [_sent_file("a.png", 5), _sent_file("b.pdf", 5), _sent_file("c.mp4", 5)],
    }
    first, second, third = [r.json for r in evo.requests if r.path == path]
    assert first["fileName"] == "a.png"
    assert first["caption"] == "Three files"
    assert first["quoted"]["key"]["id"] == "3EB0QUOTE06"
    assert first["mentioned"] == [DIGITS]
    assert first["delay"] == 300
    for later, name in ((second, "b.pdf"), (third, "c.mp4")):
        assert later["fileName"] == name
        assert later["delay"] == 300
        assert not {"caption", "quoted", "mentioned"} & set(later)
    assert [r.path for r in evo.requests].count(path) == 3


@pytest.mark.anyio
async def test_send_local_files_without_a_chat_answers_in_the_chat_of_the_quoted_message(evo, bound, local_root):
    (local_root / "a.png").write_bytes(b"png")
    (local_root / "b.png").write_bytes(b"png")
    _stored(evo, _row("3EB0QUOTE07", from_me=False, remote=GROUP, participant="391110001111@s.whatsapp.net"))
    path = _accept(evo, "message/sendMedia")
    with bound(evo):
        result = _result(
            await messaging.send_local_files(
                paths=[str(local_root / "a.png"), str(local_root / "b.png")], reply_to_message_id="3EB0QUOTE07"
            )
        )
    assert result["chat_id"] == GROUP
    assert [r.json["number"] for r in evo.requests if r.path == path] == [GROUP, GROUP]
    assert [r.path for r in evo.requests].count(FIND_MESSAGES) == 1


@pytest.mark.anyio
async def test_send_local_files_checks_every_file_before_sending_the_first(evo, bound, local_root, tmp_path):
    (local_root / "good.png").write_bytes(b"png")
    (tmp_path / "outside.png").write_bytes(b"png")
    _accept(evo, "message/sendMedia")
    with bound(evo), pytest.raises(ToolExecutionError, match="is outside the folders this server may read"):
        await messaging.send_local_files(
            paths=[str(local_root / "good.png"), str(tmp_path / "outside.png")], chat=PERSON
        )
    assert evo.requests == []


@pytest.mark.anyio
async def test_send_local_files_refuses_a_batch_over_300_mib(evo, bound, local_root):
    for index in range(4):
        with (local_root / f"big{index}.bin").open("wb") as handle:
            handle.truncate(104_857_600)  # sparse: the limit reads sizes, not bytes
    _accept(evo, "message/sendMedia")
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await messaging.send_local_files(paths=[str(local_root / f"big{index}.bin") for index in range(4)], chat=PERSON)
    assert str(caught.value) == (
        "These files total 419430400 bytes; one call sends at most 314572800 bytes (300 MiB). Nothing was sent."
    )
    assert evo.requests == []


def _three_pngs(root):
    return [str(root / name) for name in ("a.png", "b.png", "c.png")]


@pytest.mark.anyio
async def test_send_local_files_accepts_a_batch_of_exactly_300_mib(evo, bound, local_root, monkeypatch):
    for index in range(3):
        with (local_root / f"big{index}.bin").open("wb") as handle:
            handle.truncate(104_857_600)
    _accept(evo, "message/sendMedia")
    monkeypatch.setattr(messaging, "_read_base64", lambda path: ("QQ==", 104_857_600))
    with bound(evo):
        result = _result(
            await messaging.send_local_files(
                paths=[str(local_root / f"big{index}.bin") for index in range(3)], chat=PERSON
            )
        )
    assert len(result["files"]) == 3


@pytest.mark.anyio
async def test_send_local_files_charges_every_message_against_the_write_limit(evo, bound, local_root, make_connection):
    for name in ("a.png", "b.png", "c.png"):
        (local_root / name).write_bytes(b"png")
    _accept(evo, "message/sendMedia")
    conn = make_connection(max_writes_per_minute=2)
    with bound(evo, conn), pytest.raises(ToolExecutionError, match="Rate limit reached: at most 2 changes per minute"):
        await messaging.send_local_files(paths=_three_pngs(local_root), chat=PERSON)
    assert evo.requests == []


@pytest.mark.anyio
async def test_send_local_files_partial_failure_names_what_was_sent_and_what_was_not(evo, bound, local_root):
    for name in ("a.png", "b.png", "c.png"):
        (local_root / name).write_bytes(b"png")
    calls_seen = []

    def answer(request):
        calls_seen.append(request.json["fileName"])
        if len(calls_seen) == 2:
            return 400, {"response": {"message": [{"exists": False, "number": DIGITS}]}}
        return 201, SENT

    evo.on("POST", _path("message/sendMedia"), handler=answer)
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await messaging.send_local_files(paths=_three_pngs(local_root), chat=PERSON)
    assert str(caught.value) == (
        f"Sent a.png. Sending b.png failed: {DIGITS} is not on WhatsApp. Nothing was sent. Not sent: c.png."
    )
    assert calls_seen == ["a.png", "b.png"]


@pytest.mark.anyio
async def test_send_local_files_a_single_failing_file_reports_the_plain_error(evo, bound, local_root):
    (local_root / "a.png").write_bytes(b"png")
    evo.on(
        "POST",
        _path("message/sendMedia"),
        status=400,
        json={"response": {"message": [{"exists": False, "number": DIGITS}]}},
    )
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await messaging.send_local_files(paths=[str(local_root / "a.png")], chat=PERSON)
    assert str(caught.value) == f"{DIGITS} is not on WhatsApp. Nothing was sent."


@pytest.mark.anyio
async def test_send_local_files_without_chat_or_reply_is_refused_before_calling_evolution(evo, bound, local_root):
    (local_root / "a.png").write_bytes(b"png")
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await messaging.send_local_files(paths=[str(local_root / "a.png")])
    assert str(caught.value) == "Give chat, or reply_to_message_id to answer in that message's chat. Nothing was sent."
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
    assert str(caught.value) == (
        "Message 3EB0MISSING was not found in this chat. "
        "read_messages, search_messages and list_recent_messages show message ids. Nothing was sent."
    )
    assert [r.path for r in evo.requests] == [FIND_MESSAGES]


@pytest.mark.anyio
async def test_react_without_a_chat_finds_the_chat_from_the_message_id(evo, bound):
    _stored(evo, _row("3EB0TARGET3", from_me=False, remote=GROUP, participant="391110001111@s.whatsapp.net"))
    path = _accept(evo, "message/sendReaction")
    with bound(evo):
        result = _result(await messaging.react_to_message(message_id="3EB0TARGET3", emoji="👍"))
    assert result["reacted_to"] == "3EB0TARGET3"
    assert evo.last("POST", FIND_MESSAGES).json == {"where": {"key": {"id": "3EB0TARGET3"}}, "offset": 2, "page": 1}
    assert evo.last("POST", path).json["key"]["remoteJid"] == GROUP


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
async def test_edit_message_without_a_chat_finds_the_chat_from_the_message_id(evo, bound):
    _stored(evo, _row("3EB0OWN0006", remote=GROUP))
    evo.on("POST", _path("chat/updateMessage"), status=201, json={})
    with bound(evo):
        result = _result(await messaging.edit_message(message_id="3EB0OWN0006", new_text="fixed text"))
    assert result == {"edited": True, "message_id": "3EB0OWN0006", "chat_id": GROUP}
    assert evo.last("POST", _path("chat/updateMessage")).json["number"] == GROUP


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
async def test_delete_for_everyone_without_a_chat_finds_the_chat_from_the_message_id(evo, bound):
    _stored(evo, _row("3EB0OWN0007", remote=GROUP))
    evo.on("DELETE", _path("chat/deleteMessageForEveryone"), json={})
    with bound(evo):
        await messaging.delete_message_for_everyone(message_id="3EB0OWN0007")
    assert evo.last("DELETE", _path("chat/deleteMessageForEveryone")).json["remoteJid"] == GROUP


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("tool", "kwargs", "suffix"),
    [
        ("react_to_message", {"emoji": "👍"}, "Nothing was sent."),
        ("edit_message", {"new_text": "x"}, "Nothing was sent."),
        ("delete_message_for_everyone", {}, "Nothing was changed."),
    ],
)
async def test_message_id_tools_refuse_an_id_shared_by_two_chats_until_a_chat_picks_one(
    evo, bound, tool, kwargs, suffix
):
    evo.on("POST", FIND_MESSAGES, json=_page(_row("3EB0SHARED01"), _row("3EB0SHARED01", remote=GROUP)))
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await getattr(messaging, tool)(message_id="3EB0SHARED01", **kwargs)
    assert str(caught.value) == (
        f"Message 3EB0SHARED01 exists in 2 chats ({PERSON}, {GROUP}). Pass chat to pick one. {suffix}"
    )
    assert [r.path for r in evo.requests] == [FIND_MESSAGES]


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


# --- forward_message -------------------------------------------------------------------------------------------

GET_BASE64 = _path("chat/getBase64FromMediaMessage")
B64 = base64.b64encode(b"media bytes").decode()
OTHER = "393339990000@s.whatsapp.net"
FORWARD_NOTE = "WhatsApp shows these as new messages, without the Forwarded label."
VCARD = "BEGIN:VCARD\nVERSION:3.0\nFN:Ana Ruiz\nTEL;type=CELL;type=VOICE;waid=393331234567:+39 333 123 4567\nEND:VCARD"


def _forwarded(chat_id, message_id="3EB0SENT01"):
    return {"chat_id": chat_id, "message_id": message_id, "status": "PENDING"}


def _bodies(evo, path):
    return [r.json for r in evo.requests if r.path == path]


@pytest.mark.anyio
async def test_forward_text_resends_the_full_text_to_every_chat_and_charges_each_copy(evo, bound, make_connection):
    _stored(evo, _row("3EB0SRC0001", from_me=False, message={"conversation": "x" * 2000}))
    path = _accept(evo, "message/sendText")
    conn = make_connection(max_writes_per_minute=5)
    with bound(evo, conn):
        result = _result(await messaging.forward_message(message_id="3EB0SRC0001", to=[PERSON, GROUP], delay_ms=250))
    assert result == {
        "source_message_id": "3EB0SRC0001",
        "type": "text",
        "forwarded": [_forwarded(PERSON), _forwarded(GROUP)],
        "note": FORWARD_NOTE,
    }
    assert _bodies(evo, path) == [
        {"number": PERSON, "text": "x" * 2000, "delay": 250},
        {"number": GROUP, "text": "x" * 2000, "delay": 250},
    ]
    # The call itself and the second copy are recorded; three more fit in the five allowed, a sixth does not.
    ratelimit.charge(conn, writes=3)
    with pytest.raises(ToolExecutionError, match="Rate limit reached"):
        ratelimit.charge(conn, writes=1)


@pytest.mark.anyio
async def test_forward_is_refused_before_sending_when_the_copies_do_not_fit_the_write_limit(
    evo, bound, make_connection
):
    _stored(evo, _row("3EB0SRC0002", from_me=False))
    _accept(evo, "message/sendText")
    conn = make_connection(max_writes_per_minute=2)
    with bound(evo, conn), pytest.raises(ToolExecutionError, match="at most 2 changes per minute"):
        await messaging.forward_message(message_id="3EB0SRC0002", to=[PERSON, GROUP, OTHER])
    assert [r.path for r in evo.requests] == [FIND_MESSAGES]


@pytest.mark.anyio
async def test_forward_image_fetches_the_media_once_and_sends_it_to_every_chat(evo, bound):
    row = _row(
        "3EB0IMG0002",
        from_me=False,
        kind="imageMessage",
        message={"imageMessage": {"caption": "look", "mimetype": "image/jpeg"}},
    )
    _stored(evo, row)
    evo.on("POST", GET_BASE64, json={"base64": B64, "mimetype": "image/png", "fileName": "pic.png"})
    path = _accept(evo, "message/sendMedia")
    with bound(evo):
        result = _result(await messaging.forward_message(message_id="3EB0IMG0002", to=[PERSON, GROUP]))
    assert result["type"] == "image"
    assert [entry["chat_id"] for entry in result["forwarded"]] == [PERSON, GROUP]
    assert [r.path for r in evo.requests].count(GET_BASE64) == 1
    assert [r.path for r in evo.requests].count(FIND_MESSAGES) == 1  # the stored row is reused for the media fetch
    assert evo.last("POST", GET_BASE64).json == {"message": {"key": {"id": "3EB0IMG0002"}}}
    expected = {"mediatype": "image", "mimetype": "image/png", "media": B64, "caption": "look", "delay": 0}
    assert _bodies(evo, path) == [{"number": PERSON, **expected}, {"number": GROUP, **expected}]


@pytest.mark.anyio
async def test_forward_document_keeps_its_file_name_and_falls_back_to_document(evo, bound):
    named = _row(
        "3EB0DOC0001",
        from_me=False,
        kind="documentMessage",
        message={"documentMessage": {"mimetype": "application/pdf", "fileName": "invoice.pdf", "caption": "Q3"}},
    )
    anonymous = _row(
        "3EB0DOC0002",
        from_me=False,
        kind="documentWithCaptionMessage",
        message={"documentWithCaptionMessage": {"message": {"documentMessage": {"mimetype": "application/pdf"}}}},
    )
    evo.on("POST", GET_BASE64, json={"base64": B64})
    path = _accept(evo, "message/sendMedia")
    with bound(evo):
        _stored(evo, named)
        await messaging.forward_message(message_id="3EB0DOC0001", to=[PERSON])
        _stored(evo, anonymous)
        await messaging.forward_message(message_id="3EB0DOC0002", to=[PERSON])
    assert _bodies(evo, path) == [
        {
            "number": PERSON,
            "mediatype": "document",
            "mimetype": "application/pdf",
            "fileName": "invoice.pdf",
            "media": B64,
            "caption": "Q3",
            "delay": 0,
        },
        {
            "number": PERSON,
            "mediatype": "document",
            "mimetype": "application/pdf",
            "fileName": "document",
            "media": B64,
            "delay": 0,
        },
    ]


@pytest.mark.anyio
async def test_forward_audio_file_is_sent_as_media_without_a_caption(evo, bound):
    row = _row(
        "3EB0AUD0001",
        from_me=False,
        kind="audioMessage",
        message={"audioMessage": {"mimetype": "audio/mpeg", "ptt": False}},
    )
    _stored(evo, row)
    evo.on("POST", GET_BASE64, json={"base64": B64, "caption": "ignored"})
    path = _accept(evo, "message/sendMedia")
    with bound(evo):
        await messaging.forward_message(message_id="3EB0AUD0001", to=[PERSON])
    assert evo.last("POST", path).json == {
        "number": PERSON,
        "mediatype": "audio",
        "mimetype": "audio/mpeg",
        "media": B64,
        "delay": 0,
    }


_NOTE_CASES = [
    ("message/sendWhatsAppAudio", "audioMessage", {"audioMessage": {"mimetype": "audio/ogg", "ptt": True}}, "audio"),
    ("message/sendPtv", "ptvMessage", {"ptvMessage": {"mimetype": "video/mp4"}}, "video"),
    ("message/sendSticker", "stickerMessage", {"stickerMessage": {"mimetype": "image/webp"}}, "sticker"),
]


@pytest.mark.anyio
@pytest.mark.parametrize(("endpoint", "kind", "message", "field"), _NOTE_CASES, ids=[case[0] for case in _NOTE_CASES])
async def test_forward_voice_note_video_note_and_sticker_use_their_own_endpoints(
    evo, bound, endpoint, kind, message, field
):
    _stored(evo, _row("3EB0NOTE001", from_me=False, kind=kind, message=message))
    evo.on("POST", GET_BASE64, json={"base64": B64})
    path = _accept(evo, endpoint)
    with bound(evo):
        await messaging.forward_message(message_id="3EB0NOTE001", to=[PERSON])
    assert evo.last("POST", path).json == {"number": PERSON, field: B64, "delay": 0}


@pytest.mark.anyio
async def test_forward_location_resends_the_pin_and_fills_a_missing_name_and_address(evo, bound):
    pin = {"degreesLatitude": 41.3874, "degreesLongitude": 2.1686, "name": "Plaça Catalunya", "address": "Barcelona"}
    path = _accept(evo, "message/sendLocation")
    with bound(evo):
        _stored(evo, _row("3EB0LOC0001", from_me=False, kind="locationMessage", message={"locationMessage": pin}))
        await messaging.forward_message(message_id="3EB0LOC0001", to=[PERSON])
        bare = {"degreesLatitude": 41.3874, "degreesLongitude": 2.1686}
        _stored(evo, _row("3EB0LOC0002", from_me=False, kind="locationMessage", message={"locationMessage": bare}))
        await messaging.forward_message(message_id="3EB0LOC0002", to=[PERSON])
    assert _bodies(evo, path) == [
        {
            "number": PERSON,
            "latitude": 41.3874,
            "longitude": 2.1686,
            "name": "Plaça Catalunya",
            "address": "Barcelona",
            "delay": 0,
        },
        {
            "number": PERSON,
            "latitude": 41.3874,
            "longitude": 2.1686,
            "name": "Location",
            "address": "41.3874, 2.1686",
            "delay": 0,
        },
    ]


@pytest.mark.anyio
async def test_forward_contact_card_sends_the_name_and_phone_read_from_the_vcard(evo, bound):
    message = {"contactMessage": {"displayName": "Ana", "vcard": VCARD}}
    _stored(evo, _row("3EB0CON0001", from_me=False, kind="contactMessage", message=message))
    path = _accept(evo, "message/sendContact")
    with bound(evo):
        await messaging.forward_message(message_id="3EB0CON0001", to=[PERSON])
    assert evo.last("POST", path).json == {
        "number": PERSON,
        "contact": [{"fullName": "Ana Ruiz", "wuid": DIGITS, "phoneNumber": f"+{DIGITS}"}],
        "delay": 0,
    }


@pytest.mark.anyio
async def test_forward_contacts_array_sends_every_card(evo, bound):
    message = {
        "contactsArrayMessage": {
            "contacts": [
                {"displayName": "Ana", "vcard": VCARD},
                {"displayName": "Bo Lund", "vcard": "BEGIN:VCARD\nTEL:+46 70 123 45 67\nEND:VCARD"},
            ]
        }
    }
    _stored(evo, _row("3EB0CON0002", from_me=False, kind="contactsArrayMessage", message=message))
    path = _accept(evo, "message/sendContact")
    with bound(evo):
        await messaging.forward_message(message_id="3EB0CON0002", to=[PERSON])
    assert evo.last("POST", path).json["contact"] == [
        {"fullName": "Ana Ruiz", "wuid": DIGITS, "phoneNumber": f"+{DIGITS}"},
        {"fullName": "Bo Lund", "wuid": "46701234567", "phoneNumber": "+46701234567"},
    ]


_VCARD_CASES = [
    (VCARD, None, {"fullName": "Ana Ruiz", "wuid": DIGITS, "phoneNumber": f"+{DIGITS}"}),
    (
        "BEGIN:VCARD\r\nFN:Bo\r\nTEL:+39 333 000 1111\r\nEND:VCARD\r\n",
        None,
        {"fullName": "Bo", "wuid": "393330001111", "phoneNumber": "+393330001111"},
    ),
    (
        "FN:Cy\nitem1.TEL:+1 555 000 0000\nitem2.TEL;waid=34600111222:+34 600 11 12 22",
        None,
        {"fullName": "Cy", "wuid": "34600111222", "phoneNumber": "+34600111222"},
    ),
    ("TEL:+39 333 000 1111", "Dee", {"fullName": "Dee", "wuid": "393330001111", "phoneNumber": "+393330001111"}),
    (
        "TEL:+39 333 000 1111",
        None,
        {"fullName": "+393330001111", "wuid": "393330001111", "phoneNumber": "+393330001111"},
    ),
    ("BEGIN:VCARD\nFN:No Phone\nEND:VCARD", "No Phone", None),
    ("", None, None),
]


@pytest.mark.parametrize(("vcard", "display_name", "expected"), _VCARD_CASES)
def test_vcard_card_reads_the_name_and_the_preferred_phone(vcard, display_name, expected):
    assert messaging._vcard_card(vcard, display_name) == expected


@pytest.mark.anyio
async def test_forward_accepts_names_removes_duplicates_and_names_the_chats(evo, bound):
    row = _row("3EB0SRC0003", from_me=False)
    program_directory(
        evo, INSTANCE, contacts=[{"remoteJid": PERSON, "pushName": "Mario Rossi", "isSaved": True}], rows=[row]
    )
    path = _accept(evo, "message/sendText")
    with bound(evo):
        result = _result(await messaging.forward_message(message_id="3EB0SRC0003", to=["mario rossi", PERSON, GROUP]))
    assert [entry["chat_id"] for entry in result["forwarded"]] == [PERSON, GROUP]
    assert result["forwarded"][0]["chat_name"] == "Mario Rossi"
    assert "chat_name" not in result["forwarded"][1]
    assert [body["number"] for body in _bodies(evo, path)] == [PERSON, GROUP]


@pytest.mark.anyio
async def test_forward_with_a_chat_filter_narrows_the_source_lookup(evo, bound):
    _stored(evo, _row("3EB0SRC0004", from_me=False, remote=GROUP))
    _accept(evo, "message/sendText")
    with bound(evo):
        await messaging.forward_message(message_id="3EB0SRC0004", to=[PERSON], chat=GROUP)
    where = evo.last("POST", FIND_MESSAGES).json["where"]
    assert where["key"]["id"] == "3EB0SRC0004"
    assert where["key"]["remoteJid"] == GROUP


@pytest.mark.anyio
async def test_forward_on_business_uses_bare_digits_and_the_stored_message_for_media(
    evo, bound, make_connection, make_identity
):
    row = _row("3EB0IMG0003", from_me=False, kind="imageMessage", message={"imageMessage": {"mimetype": "image/jpeg"}})
    _stored(evo, row)
    evo.on("POST", GET_BASE64, json={"base64": B64})
    path = _accept(evo, "message/sendMedia")
    with bound(evo, make_connection(identity=make_identity(BUSINESS))):
        await messaging.forward_message(message_id="3EB0IMG0003", to=[PERSON])
    fetched = evo.last("POST", GET_BASE64).json["message"]
    assert fetched["key"]["id"] == "3EB0IMG0003"
    assert fetched["messageType"] == "imageMessage"
    assert evo.last("POST", path).json["number"] == DIGITS


@pytest.mark.anyio
async def test_forward_to_a_group_on_business_is_refused_before_sending(evo, bound, make_connection, make_identity):
    _stored(evo, _row("3EB0SRC0005", from_me=False))
    with bound(evo, make_connection(identity=make_identity(BUSINESS))), pytest.raises(ToolExecutionError) as caught:
        await messaging.forward_message(message_id="3EB0SRC0005", to=[PERSON, GROUP])
    assert "phone numbers only" in str(caught.value)
    assert [r.path for r in evo.requests] == [FIND_MESSAGES]


_REFUSALS = [
    (
        "deleted",
        {"kind": "conversation", "status": "DELETED"},
        "Message 3EB0REF0001 was deleted and cannot be forwarded. Nothing was sent.",
    ),
    (
        "poll",
        {"kind": "pollCreationMessage", "message": {"pollCreationMessage": {"name": "Lunch?"}}},
        "Message 3EB0REF0001 is a poll; text, media, location and contact messages can be forwarded. Nothing was sent.",
    ),
    (
        "reaction",
        {"kind": "reactionMessage", "message": {"reactionMessage": {"text": "👍", "key": {"id": "3EB0X"}}}},
        "Message 3EB0REF0001 is a reaction; text, media, location and contact messages can be forwarded. "
        "Nothing was sent.",
    ),
    (
        "contact without a phone",
        {"kind": "contactMessage", "message": {"contactMessage": {"displayName": "Ana", "vcard": "FN:Ana"}}},
        "This contact card carries no phone number to forward. Nothing was sent.",
    ),
    (
        "text without text",
        {"kind": "conversation", "message": {"conversation": ""}},
        "Message 3EB0REF0001 has no text to forward. Nothing was sent.",
    ),
    (
        "location without coordinates",
        {"kind": "locationMessage", "message": {"locationMessage": {"name": "Somewhere"}}},
        "Message 3EB0REF0001 carries no coordinates to forward. Nothing was sent.",
    ),
]


@pytest.mark.anyio
@pytest.mark.parametrize(("case", "spec", "expected"), _REFUSALS, ids=[case[0] for case in _REFUSALS])
async def test_forward_refusals_send_nothing(evo, bound, case, spec, expected):
    row = _row("3EB0REF0001", from_me=False, kind=spec["kind"], message=spec.get("message"))
    if "status" in spec:
        row["status"] = spec["status"]
    _stored(evo, row)
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await messaging.forward_message(message_id="3EB0REF0001", to=[PERSON])
    assert str(caught.value) == expected
    assert [r.path for r in evo.requests] == [FIND_MESSAGES]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("kind", "message"),
    [("ptvMessage", {"ptvMessage": {}}), ("stickerMessage", {"stickerMessage": {"mimetype": "image/webp"}})],
)
async def test_forward_video_notes_and_stickers_are_refused_on_business(
    evo, bound, make_connection, make_identity, kind, message
):
    _stored(evo, _row("3EB0REF0002", from_me=False, kind=kind, message=message))
    with bound(evo, make_connection(identity=make_identity(BUSINESS))), pytest.raises(ToolExecutionError) as caught:
        await messaging.forward_message(message_id="3EB0REF0002", to=[PERSON])
    assert str(caught.value) == (
        "Video notes and stickers can be forwarded only on WhatsApp Web (Baileys) instances. Nothing was sent."
    )
    assert [r.path for r in evo.requests] == [FIND_MESSAGES]


@pytest.mark.anyio
async def test_forward_media_over_the_size_limit_is_refused_before_sending(evo, bound, monkeypatch):
    monkeypatch.setattr(media, "MAX_FILE_BYTES", 5)
    row = _row("3EB0IMG0004", from_me=False, kind="imageMessage", message={"imageMessage": {}})
    _stored(evo, row)
    evo.on("POST", GET_BASE64, json={"base64": B64})
    _accept(evo, "message/sendMedia")
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await messaging.forward_message(message_id="3EB0IMG0004", to=[PERSON, GROUP])
    assert str(caught.value) == ("The file is 11 bytes; the limit is 104857600 bytes (100 MiB). Nothing was sent.")
    assert [r.path for r in evo.requests] == [FIND_MESSAGES, GET_BASE64]


@pytest.mark.anyio
async def test_forward_media_that_evolution_does_not_have_is_refused(evo, bound):
    _stored(evo, _row("3EB0IMG0005", from_me=False, kind="imageMessage", message={"imageMessage": {}}))
    evo.on("POST", GET_BASE64, json={})
    with bound(evo), pytest.raises(ToolExecutionError, match="Evolution returned no media for message 3EB0IMG0005"):
        await messaging.forward_message(message_id="3EB0IMG0005", to=[PERSON])


@pytest.mark.anyio
async def test_forward_an_unknown_message_sends_nothing(evo, bound):
    evo.on("POST", FIND_MESSAGES, json=_page())
    with bound(evo), pytest.raises(ToolExecutionError, match="Message 3EB0MISSING was not found.*Nothing was sent."):
        await messaging.forward_message(message_id="3EB0MISSING", to=[PERSON])


@pytest.mark.anyio
async def test_forward_partial_failure_names_who_got_it_and_who_was_not_attempted(evo, bound):
    _stored(evo, _row("3EB0SRC0006", from_me=False))
    seen = []

    def answer(request):
        seen.append(request.json["number"])
        if len(seen) == 2:
            return 400, {"response": {"message": [{"exists": False, "number": "120363012345678901"}]}}
        return 201, SENT

    evo.on("POST", _path("message/sendText"), handler=answer)
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await messaging.forward_message(message_id="3EB0SRC0006", to=[PERSON, GROUP, OTHER])
    assert str(caught.value) == (
        f"Forwarded to {PERSON}. Forwarding to {GROUP} failed: 120363012345678901 is not on WhatsApp. "
        f"Nothing was sent. Not attempted: {OTHER}."
    )
    assert seen == [PERSON, GROUP]


@pytest.mark.anyio
async def test_forward_a_single_failing_recipient_reports_the_plain_error(evo, bound):
    _stored(evo, _row("3EB0SRC0007", from_me=False))
    evo.on(
        "POST",
        _path("message/sendText"),
        status=400,
        json={"response": {"message": [{"exists": False, "number": DIGITS}]}},
    )
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await messaging.forward_message(message_id="3EB0SRC0007", to=[PERSON])
    assert str(caught.value) == f"{DIGITS} is not on WhatsApp. Nothing was sent."
