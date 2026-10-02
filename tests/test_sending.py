import httpx2
import pytest

from evolution_api_mcp import sending
from evolution_api_mcp.client import EvolutionClient
from evolution_api_mcp.errors import ToolExecutionError
from evolution_api_mcp.registry import BAILEYS, BUSINESS
from tests.conftest import BASE, INSTANCE, TOKEN

PERSON = "393331234567@s.whatsapp.net"
GROUP = "120363012345678901@g.us"
FIND_MESSAGES = f"/chat/findMessages/{INSTANCE}"
SEND_TEXT = f"/message/sendText/{INSTANCE}"


def _page(*records):
    return {"messages": {"total": len(records), "pages": 1, "currentPage": 1, "records": list(records)}}


def _row(message_id, *, from_me, text="Hello", participant=None, remote=PERSON, ts=1759180000):
    key = {"id": message_id, "remoteJid": remote, "fromMe": from_me}
    if participant:
        key["participant"] = participant
    return {
        "id": f"row-{message_id}",
        "key": key,
        "pushName": "Ana",
        "messageType": "conversation",
        "message": {"conversation": text},
        "messageTimestamp": ts,
        "MessageUpdate": [{"status": "SERVER_ACK"}],
    }


def _client(evo):
    return EvolutionClient(BASE, TOKEN, transport=evo)


async def _options(evo, conn, chat=PERSON, **kwargs):
    defaults = {
        "delay_ms": None,
        "reply_to_message_id": None,
        "mention": None,
        "mention_everyone": False,
        "link_preview": None,
    }
    return await sending.build_options(_client(evo), conn.identity, conn, chat, **{**defaults, **kwargs})


# --- recipient -------------------------------------------------------------------------------------------------


def test_recipient_is_the_full_jid_on_baileys(make_identity):
    assert sending.recipient(make_identity(BAILEYS), GROUP) == GROUP
    assert sending.recipient(make_identity(BAILEYS), PERSON) == PERSON


def test_recipient_is_bare_digits_on_business(make_identity):
    assert sending.recipient(make_identity(BUSINESS), PERSON) == "393331234567"


@pytest.mark.parametrize("chat", [GROUP, "9876543210@lid"])
def test_recipient_refuses_groups_and_lids_on_business(make_identity, chat):
    with pytest.raises(ToolExecutionError, match="phone numbers only; groups and @lid ids need a WhatsApp Web"):
        sending.recipient(make_identity(BUSINESS), chat)


# --- reply_key -------------------------------------------------------------------------------------------------


def test_reply_key_keeps_the_stored_participant():
    row = _row("3EB0AAAA01", from_me=False, participant="391110001111@s.whatsapp.net", remote=GROUP)
    assert sending.reply_key(row) == {
        "key": {
            "id": "3EB0AAAA01",
            "remoteJid": GROUP,
            "fromMe": False,
            "participant": "391110001111@s.whatsapp.net",
        }
    }


def test_reply_key_without_participant_has_no_participant_key():
    assert sending.reply_key(_row("3EB0AAAA02", from_me=True)) == {
        "key": {"id": "3EB0AAAA02", "remoteJid": PERSON, "fromMe": True}
    }


# --- build_options ---------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_delay_defaults_to_the_connection_pacing_and_explicit_value_wins(evo, make_connection):
    conn = make_connection(default_delay_ms=1200)
    assert (await _options(evo, conn))["delay"] == 1200
    assert (await _options(evo, conn, delay_ms=0))["delay"] == 0
    assert (await _options(evo, conn, delay_ms=3000))["delay"] == 3000
    assert evo.requests == []


@pytest.mark.anyio
async def test_plain_options_carry_only_the_delay(evo, make_connection):
    assert await _options(evo, make_connection()) == {"delay": 0}


@pytest.mark.anyio
async def test_reply_resolves_the_stored_key_including_participant(evo, make_connection):
    stored = _row("3EB0AAAA03", from_me=False, participant="391110001111@s.whatsapp.net", remote=GROUP)
    evo.on("POST", FIND_MESSAGES, json=_page(stored))
    options = await _options(evo, make_connection(), chat=GROUP, reply_to_message_id="3EB0AAAA03")
    assert options["quoted"] == {
        "key": {
            "id": "3EB0AAAA03",
            "remoteJid": GROUP,
            "fromMe": False,
            "participant": "391110001111@s.whatsapp.net",
        }
    }
    where = evo.last("POST", FIND_MESSAGES).json["where"]
    assert where["key"]["id"] == "3EB0AAAA03"
    assert where["key"]["remoteJid"] == GROUP
    assert where["key"]["remoteJidAlt"] == GROUP


@pytest.mark.anyio
async def test_reply_to_an_unknown_message_is_refused(evo, make_connection):
    evo.on("POST", FIND_MESSAGES, json=_page())
    with pytest.raises(ToolExecutionError) as caught:
        await _options(evo, make_connection(), reply_to_message_id="3EB0MISSING")
    assert str(caught.value) == "Message 3EB0MISSING was not found in this chat. Use read_messages to find its id."


@pytest.mark.anyio
async def test_mentions_are_normalised_to_digits_on_baileys(evo, make_connection):
    options = await _options(
        evo, make_connection(), mention=["+39 333 123 4567", "393331234567@s.whatsapp.net"], mention_everyone=True
    )
    assert options["mentioned"] == ["393331234567"]
    assert options["mentionsEveryOne"] is True


@pytest.mark.anyio
async def test_mention_that_is_not_a_phone_is_refused(evo, make_connection):
    with pytest.raises(ToolExecutionError, match="Unsupported chat id"):
        await _options(evo, make_connection(), mention=["abc"])
    with pytest.raises(ToolExecutionError, match="mentions take phone numbers"):
        await _options(evo, make_connection(), mention=[GROUP])


@pytest.mark.anyio
@pytest.mark.parametrize("kwargs", [{"mention": ["393331234567"]}, {"mention_everyone": True}])
async def test_mentions_are_refused_on_business(evo, make_connection, make_identity, kwargs):
    conn = make_connection(identity=make_identity(BUSINESS))
    with pytest.raises(ToolExecutionError) as caught:
        await _options(evo, conn, **kwargs)
    assert str(caught.value) == "Mentions work only on WhatsApp Web (Baileys) instances."


@pytest.mark.anyio
async def test_link_preview_is_sent_only_when_chosen(evo, make_connection):
    conn = make_connection()
    assert "linkPreview" not in await _options(evo, conn)
    assert (await _options(evo, conn, link_preview=False))["linkPreview"] is False
    assert (await _options(evo, conn, link_preview=True))["linkPreview"] is True


# --- check_business_rejection ----------------------------------------------------------------------------------


def test_window_closed_rejection_explains_templates():
    meta_error = {
        "message": "(#131047) Re-engagement message",
        "type": "OAuthException",
        "code": 131047,
        "error_data": {"messaging_product": "whatsapp", "details": "Message failed to send"},
        "fbtrace_id": "AbC123",
    }
    with pytest.raises(ToolExecutionError) as caught:
        sending.check_business_rejection(meta_error)
    text = str(caught.value)
    assert text.startswith(
        "WhatsApp Business Platform refused the message (code 131047): (#131047) Re-engagement message. "
        "Nothing was sent."
    )
    assert text.endswith("an approved template (send_template_message) can reopen the conversation.")
    assert "24-hour customer-service window is closed" in text


def test_other_meta_rejection_has_no_window_hint():
    with pytest.raises(ToolExecutionError) as caught:
        sending.check_business_rejection({"message": "Invalid parameter", "type": "OAuthException", "code": 100})
    assert str(caught.value) == (
        "WhatsApp Business Platform refused the message (code 100): Invalid parameter. Nothing was sent."
    )


def test_a_stored_message_answer_is_not_a_rejection():
    sending.check_business_rejection({"key": {"id": "X"}, "message": {"conversation": "hi"}, "code": 1})
    sending.check_business_rejection(None)
    sending.check_business_rejection("")


# --- sent_result -----------------------------------------------------------------------------------------------


def test_sent_result_projects_the_key_status_and_iso_time():
    response = {
        "key": {"id": "3EB0SENT01", "remoteJid": PERSON, "fromMe": True},
        "pushName": "Me",
        "status": "PENDING",
        "message": {"conversation": "hi"},
        "messageTimestamp": 1759180000,
        "instanceId": "cuid-secret",
    }
    assert sending.sent_result(response) == {
        "message_id": "3EB0SENT01",
        "chat_id": PERSON,
        "status": "PENDING",
        "timestamp": "2025-09-29T21:06:40Z",
    }


# --- send ------------------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_send_posts_the_body_and_returns_the_compact_result(evo, make_connection):
    evo.on(
        "POST",
        SEND_TEXT,
        status=201,
        json={
            "key": {"id": "3EB0SENT02", "remoteJid": PERSON, "fromMe": True},
            "status": "PENDING",
            "messageTimestamp": "1759180000",
        },
    )
    conn = make_connection()
    body = {"number": PERSON, "text": "hello", "delay": 0}
    result = await sending.send(_client(evo), conn.identity, conn, "message/sendText", body, PERSON)
    assert result == {
        "message_id": "3EB0SENT02",
        "chat_id": PERSON,
        "status": "PENDING",
        "timestamp": "2025-09-29T21:06:40Z",
    }
    sent = evo.last("POST", SEND_TEXT)
    assert sent.json == body
    assert sent.headers["apikey"] == TOKEN


@pytest.mark.anyio
async def test_meta_rejection_returned_as_201_is_a_refusal_not_uncertain(evo, make_connection, make_identity):
    evo.on(
        "POST",
        SEND_TEXT,
        status=201,
        json={
            "message": "(#131047) Re-engagement message",
            "type": "OAuthException",
            "code": 131047,
            "error_data": {"details": "Message failed to send because more than 24 hours have passed"},
            "fbtrace_id": "AbC123",
        },
    )
    conn = make_connection(identity=make_identity(BUSINESS))
    with pytest.raises(ToolExecutionError) as caught:
        await sending.send(
            _client(evo), conn.identity, conn, "message/sendText", {"number": "393331234567", "text": "x"}, PERSON
        )
    text = str(caught.value)
    assert "refused the message (code 131047)" in text
    assert "Nothing was sent." in text
    assert "24-hour customer-service window" in text
    assert "UNCERTAIN" not in text
    assert [r.path for r in evo.requests] == [SEND_TEXT]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "body",
    [
        {
            "status": 400,
            "error": "Bad Request",
            "response": {"message": [{"jid": "3999@s.whatsapp.net", "exists": False, "number": "3999"}]},
        },
        {"jid": "3999@s.whatsapp.net", "exists": False, "number": "3999"},
    ],
)
async def test_number_not_on_whatsapp_is_reported_as_nothing_sent(evo, make_connection, body):
    evo.on("POST", SEND_TEXT, status=400, json=body)
    conn = make_connection()
    with pytest.raises(ToolExecutionError) as caught:
        await sending.send(
            _client(evo), conn.identity, conn, "message/sendText", {"number": "3999", "text": "x"}, PERSON
        )
    assert str(caught.value) == "3999 is not on WhatsApp. Nothing was sent."
    assert [r.path for r in evo.requests] == [SEND_TEXT]


@pytest.mark.anyio
async def test_other_400_is_a_plain_refusal(evo, make_connection):
    evo.on(
        "POST",
        SEND_TEXT,
        status=400,
        json={"status": 400, "error": "Bad Request", "response": {"message": ["Text is required"]}},
    )
    conn = make_connection()
    with pytest.raises(ToolExecutionError) as caught:
        await sending.send(_client(evo), conn.identity, conn, "message/sendText", {"number": PERSON}, PERSON)
    assert str(caught.value) == "Evolution refused the request (400): Text is required. Nothing was changed."


@pytest.mark.anyio
async def test_read_timeout_is_uncertain_and_rereads_recent_outgoing_messages(evo, make_connection):
    evo.fail("POST", SEND_TEXT, httpx2.ReadTimeout("timed out"))
    evo.on("POST", FIND_MESSAGES, json=_page(_row("3EB0OUT001", from_me=True, text="hello there")))
    conn = make_connection()
    with pytest.raises(ToolExecutionError) as caught:
        await sending.send(
            _client(evo), conn.identity, conn, "message/sendText", {"number": PERSON, "text": "hello there"}, PERSON
        )
    text = str(caught.value)
    assert text.startswith("UNCERTAIN: Evolution did not confirm the result")
    assert "hello there" in text and "3EB0OUT001" in text
    assert text.endswith("Do NOT repeat the call before checking this state.")

    where = evo.last("POST", FIND_MESSAGES).json["where"]
    assert where["key"] == {"remoteJid": PERSON, "remoteJidAlt": PERSON, "fromMe": True}
    assert set(where["messageTimestamp"]) == {"gte", "lte"}
    assert evo.last("POST", FIND_MESSAGES).json["offset"] == 5


@pytest.mark.anyio
async def test_uncertain_state_is_not_re_read_when_the_re_read_fails(evo, make_connection):
    evo.fail("POST", SEND_TEXT, httpx2.ReadTimeout("timed out"))
    evo.fail("POST", FIND_MESSAGES, httpx2.ConnectError("down"))
    conn = make_connection()
    with pytest.raises(ToolExecutionError, match="Verified state: not re-read"):
        await sending.send(_client(evo), conn.identity, conn, "message/sendText", {"number": PERSON}, PERSON)


@pytest.mark.anyio
async def test_unreachable_server_means_nothing_was_sent(evo, make_connection):
    evo.fail("POST", SEND_TEXT, httpx2.ConnectError("refused"))
    conn = make_connection()
    with pytest.raises(ToolExecutionError) as caught:
        await sending.send(_client(evo), conn.identity, conn, "message/sendText", {"number": PERSON}, PERSON)
    assert str(caught.value).startswith("Could not reach Evolution at ")
    assert str(caught.value).endswith("Nothing was sent.")
