import json

import httpx2
import pytest

from evolution_api_mcp.errors import ToolExecutionError
from evolution_api_mcp.tools.instance import (
    get_instance_status,
    logout_instance,
    restart_instance,
    set_presence,
    start_pairing,
)
from tests.conftest import INSTANCE, TOKEN

FETCH_INSTANCES = "/instance/fetchInstances"
STATE = f"/instance/connectionState/{INSTANCE}"
CONNECT = f"/instance/connect/{INSTANCE}"
RESTART = f"/instance/restart/{INSTANCE}"
LOGOUT = f"/instance/logout/{INSTANCE}"
PRESENCE = f"/instance/setPresence/{INSTANCE}"
ROOT = {
    "status": 200,
    "message": "Welcome to the Evolution API, it is working!",
    "version": "2.3.7",
    "clientName": "evolution_exchange",
}


def _row(**overrides):
    row = {
        "name": INSTANCE,
        "connectionStatus": "open",
        "ownerJid": "393331234567@s.whatsapp.net",
        "profileName": "Shop",
        "integration": "WHATSAPP-BAILEYS",
        "token": TOKEN,
        "Proxy": {"password": "hunter2"},
    }
    return {**row, **overrides}


def _state(state):
    return {"instance": {"instanceName": INSTANCE, "state": state}}


def _program_status(evo, *, state="open", row=None):
    evo.on("GET", FETCH_INSTANCES, json=[row or _row()])
    evo.on("GET", STATE, json=_state(state))
    evo.on("GET", "/", json=ROOT)


# --- get_instance_status ---------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_status_of_an_open_instance(evo, bound):
    _program_status(evo)
    with bound(evo, toolsets=frozenset({"messaging", "chats"})):
        result = json.loads(await get_instance_status())
    assert result == {
        "instance": INSTANCE,
        "integration": "WHATSAPP-BAILEYS",
        "state": "open",
        "phone_number": "393331234567",
        "profile_name": "Shop",
        "evolution_version": "2.3.7",
        "server": "local",
        "policy": "standard",
        "toolsets": ["messaging", "chats"],
        "tools_available": result["tools_available"],
    }
    assert result["tools_available"] > 1
    assert evo.last("GET", FETCH_INSTANCES).params == {"instanceName": INSTANCE}
    assert evo.last("GET", STATE).headers["apikey"] == TOKEN


@pytest.mark.anyio
async def test_status_never_returns_the_instance_row_secrets(evo, bound):
    _program_status(evo)
    with bound(evo):
        text = await get_instance_status()
    assert "hunter2" not in text
    assert TOKEN not in text


@pytest.mark.anyio
async def test_status_counts_only_the_tools_this_connection_can_run(evo, bound):
    # Read-only policy with the instance and settings toolsets: get_instance_status, get_instance_settings, get_proxy.
    _program_status(evo)
    with bound(evo, policy="read", toolsets=frozenset({"instance", "settings"})):
        result = json.loads(await get_instance_status())
    assert result["tools_available"] == 3
    assert result["policy"] == "read"
    assert result["toolsets"] == ["instance", "settings"]


@pytest.mark.anyio
async def test_closed_session_adds_the_pairing_note_and_missing_owner_is_none(evo, bound):
    _program_status(evo, state="close", row=_row(ownerJid=None, profileName=None))
    with bound(evo):
        result = json.loads(await get_instance_status())
    assert result["state"] == "close"
    assert result["phone_number"] is None
    assert result["note"] == (
        "The WhatsApp session is not connected; sends and live lookups fail until it is. "
        "start_pairing (instance toolset) links it again on WhatsApp Web instances."
    )


@pytest.mark.anyio
async def test_status_when_evolution_no_longer_lists_the_instance(evo, bound):
    evo.on("GET", FETCH_INSTANCES, json=[_row(name="other")])
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await get_instance_status()
    assert str(caught.value) == "Evolution no longer lists this instance; check that it still exists."


@pytest.mark.anyio
async def test_status_failure_says_nothing_was_changed(evo, bound):
    evo.on("GET", FETCH_INSTANCES, status=500, json={"status": 500, "error": "Internal Server Error", "message": "db"})
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await get_instance_status()
    assert str(caught.value) == "Evolution failed (HTTP 500): db. Nothing was changed."


# --- start_pairing ---------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_pairing_without_number_returns_the_qr_image(evo, bound):
    qr = {"pairingCode": None, "code": "2@abc", "base64": "data:image/png;base64,QUJD", "count": 1}
    evo.on("GET", CONNECT, json=qr)
    with bound(evo):
        blocks = await start_pairing()
    assert evo.last("GET", CONNECT).params == {}
    assert [block.type for block in blocks] == ["text", "image"]
    assert json.loads(blocks[0].text) == {
        "how_to": (
            "Scan with WhatsApp → Settings → Linked devices → Link a device. The code changes about every 20 "
            "seconds; call start_pairing again for a fresh one."
        )
    }
    assert blocks[1].data == "QUJD"
    assert blocks[1].mime_type == "image/png"


@pytest.mark.anyio
async def test_pairing_with_number_sends_digits_and_returns_the_code(evo, bound):
    evo.on("GET", CONNECT, json={"pairingCode": "WZYE-H1YY", "code": "2@abc", "base64": "data:image/png;base64,QUJD"})
    with bound(evo):
        blocks = await start_pairing(phone_number="+39 333 123 4567")
    assert evo.last("GET", CONNECT).params == {"number": "393331234567"}
    assert len(blocks) == 1
    result = json.loads(blocks[0].text)
    assert result["pairing_code"] == "WZYE-H1YY"
    assert result["how_to"].startswith("On the phone: WhatsApp → Settings → Linked devices")


@pytest.mark.anyio
async def test_pairing_an_open_instance_says_there_is_nothing_to_pair(evo, bound):
    evo.on("GET", CONNECT, json=_state("open"))
    with bound(evo):
        blocks = await start_pairing()
    texts = [json.loads(block.text) for block in blocks]
    assert texts == [{"state": "open", "note": "Already connected; nothing to pair."}]


@pytest.mark.anyio
async def test_pairing_without_a_code_yet_asks_to_call_again(evo, bound):
    evo.on("GET", CONNECT, json={"count": 0})
    with bound(evo):
        blocks = await start_pairing()
    assert json.loads(blocks[0].text) == {
        "state": "connecting",
        "note": "Evolution has not produced a pairing code yet; call start_pairing again in a few seconds.",
    }


@pytest.mark.anyio
async def test_pairing_error_answered_with_http_200_is_a_refusal(evo, bound):
    evo.on("GET", CONNECT, json={"error": True, "message": "Error: Connection Closed."})
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await start_pairing()
    assert str(caught.value) == "Evolution could not start pairing: Error: Connection Closed. Nothing was changed."


@pytest.mark.anyio
@pytest.mark.parametrize("number", ["abc1234", "12345", "1234567890123456"])
async def test_pairing_refuses_numbers_that_are_not_7_to_15_digits(evo, bound, number):
    with bound(evo), pytest.raises(ToolExecutionError, match="7 to 15 digits"):
        await start_pairing(phone_number=number)
    assert evo.requests == []


# --- restart_instance ------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_restart_posts_then_reports_the_state(evo, bound):
    evo.on("POST", RESTART, json={"instance": {"instanceName": INSTANCE, "status": "connecting"}})
    evo.on("GET", STATE, json=_state("connecting"))
    with bound(evo):
        result = json.loads(await restart_instance())
    assert evo.last("POST", RESTART).json == {}
    assert result == {"restarted": True, "state": "connecting"}


@pytest.mark.anyio
async def test_restart_of_a_closed_session_is_refused_and_points_to_pairing(evo, bound):
    refusal = f'BadRequestException: The "{INSTANCE}" instance is not connected'
    evo.on("POST", RESTART, json={"error": True, "message": refusal})
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await restart_instance()
    assert str(caught.value) == (
        f'Evolution could not restart the instance: BadRequestException: The "{INSTANCE}" instance is not connected. '
        "Nothing was changed. A session that is not connected needs start_pairing, not a restart."
    )
    assert [r.method for r in evo.requests] == ["POST"]


@pytest.mark.anyio
async def test_restart_without_an_answer_is_uncertain_with_the_state(evo, bound):
    evo.fail("POST", RESTART, httpx2.ReadTimeout("timed out"))
    evo.on("GET", STATE, json=_state("connecting"))
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await restart_instance()
    message = str(caught.value)
    assert message.startswith("UNCERTAIN: Evolution did not confirm the result")
    assert 'Verified state: {"state": "connecting"}' in message
    assert "Do NOT repeat the call" in message


# --- logout_instance -------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_logout_deletes_the_session(evo, bound):
    evo.on("DELETE", LOGOUT, json={"status": "SUCCESS", "error": False, "response": {"message": "Instance logged out"}})
    with bound(evo):
        result = json.loads(await logout_instance())
    assert result == {
        "logged_out": True,
        "note": "The WhatsApp session was removed from this instance; start_pairing links it again.",
    }


@pytest.mark.anyio
async def test_logout_of_a_closed_session_is_a_refusal(evo, bound):
    evo.on(
        "DELETE",
        LOGOUT,
        status=400,
        json={
            "status": 400,
            "error": "Bad Request",
            "response": {"message": [f'The "{INSTANCE}" instance is not connected']},
        },
    )
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await logout_instance()
    assert str(caught.value) == (
        f'Evolution refused the request (400): The "{INSTANCE}" instance is not connected. Nothing was changed.'
    )


# --- set_presence ----------------------------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize("presence", ["available", "unavailable"])
async def test_set_presence_posts_the_presence(evo, bound, presence):
    evo.on("POST", PRESENCE, status=201, json={"presence": presence})
    with bound(evo):
        result = json.loads(await set_presence(presence=presence))
    assert evo.last("POST", PRESENCE).json == {"presence": presence}
    assert result == {"presence": presence}
