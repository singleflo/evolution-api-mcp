import json

import pytest

from evolution_api_mcp.errors import ToolExecutionError
from evolution_api_mcp.tools.status import post_status
from tests.conftest import INSTANCE

SEND_STATUS = f"/message/sendStatus/{INSTANCE}"

ANA = "393331234567@s.whatsapp.net"
BOB = "393339876543@s.whatsapp.net"
POSTED = {
    "key": {"id": "3EB0STATUS0001", "remoteJid": "status@broadcast", "fromMe": True},
    "messageType": "extendedTextMessage",
    "status": "PENDING",
}
NO_CONTACTS = (
    "Evolution found no saved contacts to show this status to; pass audience with phone numbers. Nothing was posted."
)


def _bad_request(*messages):
    return {"status": 400, "error": "Bad Request", "response": {"message": list(messages)}}


@pytest.mark.anyio
async def test_text_status_to_all_contacts(evo, bound):
    evo.on("POST", SEND_STATUS, status=201, json=POSTED)
    with bound(evo):
        result = json.loads(await post_status("text", "Closed on Monday", background_color="#112233", font=2))

    assert evo.last("POST", SEND_STATUS).json == {
        "type": "text",
        "content": "Closed on Monday",
        "backgroundColor": "#112233",
        "font": 2,
        "allContacts": True,
    }
    assert result == {"posted": True, "message_id": "3EB0STATUS0001", "audience": "all_contacts"}


@pytest.mark.anyio
async def test_status_defaults_are_the_documented_ones(evo, bound):
    evo.on("POST", SEND_STATUS, status=201, json=POSTED)
    with bound(evo):
        await post_status("text", "Hello")

    body = evo.last("POST", SEND_STATUS).json
    assert (body["backgroundColor"], body["font"]) == ("#128C7E", 1)


@pytest.mark.anyio
async def test_status_to_an_audience_sends_deduplicated_jids(evo, bound):
    evo.on("POST", SEND_STATUS, status=201, json=POSTED)
    with bound(evo):
        result = json.loads(await post_status("text", "Hi", audience=["+39 333 123 4567", "393339876543", ANA]))

    body = evo.last("POST", SEND_STATUS).json
    assert body["allContacts"] is False
    assert body["statusJidList"] == [ANA, BOB]
    assert result == {"posted": True, "message_id": "3EB0STATUS0001", "audience": 2}


@pytest.mark.anyio
async def test_image_status_sends_the_url_and_caption(evo, bound):
    evo.on("POST", SEND_STATUS, status=201, json=POSTED)
    with bound(evo):
        await post_status("image", "https://cdn.example.com/menu.jpg", caption="Today's menu")

    body = evo.last("POST", SEND_STATUS).json
    assert body["type"] == "image"
    assert body["content"] == "https://cdn.example.com/menu.jpg"
    assert body["caption"] == "Today's menu"


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["image", "video", "audio"])
async def test_media_status_needs_a_url(evo, bound, kind):
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await post_status(kind, "/Users/me/menu.jpg")

    assert str(caught.value) == "For image, video and audio status updates, content is the media URL (http or https)."
    assert evo.requests == []


@pytest.mark.anyio
async def test_status_audience_refuses_groups_and_lid_ids(evo, bound):
    with bound(evo), pytest.raises(ToolExecutionError, match="is not a phone number"):
        await post_status("text", "Hi", audience=[ANA, "120363012345678901@g.us"])

    assert evo.requests == []


@pytest.mark.anyio
@pytest.mark.parametrize("evolution_message", ["StatusJidList is required", "Contacts not found"])
async def test_all_contacts_status_without_saved_contacts_asks_for_an_audience(evo, bound, evolution_message):
    evo.on("POST", SEND_STATUS, status=400, json=_bad_request(evolution_message))
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await post_status("text", "Hello")

    assert str(caught.value) == NO_CONTACTS


@pytest.mark.anyio
async def test_other_bad_requests_keep_evolutions_own_message(evo, bound):
    evo.on("POST", SEND_STATUS, status=400, json=_bad_request("Background color is required"))
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await post_status("text", "Hello")

    assert (
        str(caught.value) == "Evolution refused the request (400): Background color is required. Nothing was changed."
    )


@pytest.mark.anyio
async def test_status_with_an_audience_does_not_claim_there_were_no_contacts(evo, bound):
    evo.on("POST", SEND_STATUS, status=400, json=_bad_request("StatusJidList is required"))
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await post_status("text", "Hello", audience=[ANA])

    assert str(caught.value) != NO_CONTACTS
    assert "StatusJidList is required" in str(caught.value)


@pytest.mark.anyio
async def test_status_server_failure_is_uncertain(evo, bound):
    evo.on(
        "POST",
        SEND_STATUS,
        status=500,
        json={"status": 500, "error": "Internal Server Error", "response": {"message": "boom"}},
    )
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await post_status("text", "Hello")

    assert str(caught.value).startswith("UNCERTAIN: Evolution did not confirm the result")
