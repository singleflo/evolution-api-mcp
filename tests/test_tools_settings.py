import json

import httpx2
import pytest

from evolution_api_mcp.errors import ToolExecutionError
from evolution_api_mcp.tools.settings import get_instance_settings, get_proxy, set_proxy, update_instance_settings
from tests.conftest import INSTANCE

SETTINGS_FIND = f"/settings/find/{INSTANCE}"
SETTINGS_SET = f"/settings/set/{INSTANCE}"
PROXY_FIND = f"/proxy/find/{INSTANCE}"
PROXY_SET = f"/proxy/set/{INSTANCE}"

STORED = {
    "rejectCall": True,
    "msgCall": "Please write instead.",
    "groupsIgnore": False,
    "alwaysOnline": False,
    "readMessages": True,
    "readStatus": False,
    "syncFullHistory": False,
    "wavoipToken": "wavoip-secret-token",
}


# --- get_instance_settings -------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_settings_projects_the_seven_values_and_hides_wavoip_token(evo, bound):
    evo.on("GET", SETTINGS_FIND, json=STORED)
    with bound(evo):
        text = await get_instance_settings()
    assert json.loads(text) == {
        "reject_calls": True,
        "call_rejection_message": "Please write instead.",
        "ignore_groups": False,
        "always_online": False,
        "auto_read_messages": True,
        "auto_read_status": False,
        "sync_full_history": False,
    }
    assert "wavoip" not in text


@pytest.mark.anyio
async def test_get_settings_without_stored_settings_is_not_configured(evo, bound):
    evo.on("GET", SETTINGS_FIND)
    with bound(evo):
        assert json.loads(await get_instance_settings()) == {"configured": False}


@pytest.mark.anyio
async def test_get_settings_treats_a_null_call_message_as_empty(evo, bound):
    evo.on("GET", SETTINGS_FIND, json={**STORED, "msgCall": None})
    with bound(evo):
        assert json.loads(await get_instance_settings())["call_rejection_message"] == ""


# --- update_instance_settings ----------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_update_settings_overlays_the_given_values_on_the_stored_ones(evo, bound):
    evo.on("GET", SETTINGS_FIND, json=STORED)
    evo.on("POST", SETTINGS_SET, status=201, json={"settings": {"instanceName": INSTANCE, "settings": {}}})
    with bound(evo):
        text = await update_instance_settings(always_online=True, call_rejection_message="")
    assert evo.last("POST", SETTINGS_SET).json == {
        "rejectCall": True,
        "msgCall": "",
        "groupsIgnore": False,
        "alwaysOnline": True,
        "readMessages": True,
        "readStatus": False,
        "syncFullHistory": False,
    }
    assert json.loads(text) == {
        "reject_calls": True,
        "call_rejection_message": "",
        "ignore_groups": False,
        "always_online": True,
        "auto_read_messages": True,
        "auto_read_status": False,
        "sync_full_history": False,
    }
    assert "wavoip" not in text


@pytest.mark.anyio
async def test_update_settings_starts_from_defaults_when_nothing_is_stored(evo, bound):
    evo.on("GET", SETTINGS_FIND)
    evo.on("POST", SETTINGS_SET, status=201, json={"settings": {}})
    with bound(evo):
        await update_instance_settings(ignore_groups=True)
    assert evo.last("POST", SETTINGS_SET).json == {
        "rejectCall": False,
        "msgCall": "",
        "groupsIgnore": True,
        "alwaysOnline": False,
        "readMessages": False,
        "readStatus": False,
        "syncFullHistory": False,
    }


@pytest.mark.anyio
async def test_update_settings_can_switch_a_stored_true_off(evo, bound):
    evo.on("GET", SETTINGS_FIND, json=STORED)
    evo.on("POST", SETTINGS_SET, status=201, json={"settings": {}})
    with bound(evo):
        result = json.loads(await update_instance_settings(reject_calls=False))
    assert evo.last("POST", SETTINGS_SET).json["rejectCall"] is False
    assert result["reject_calls"] is False
    assert result["auto_read_messages"] is True


@pytest.mark.anyio
async def test_update_settings_needs_at_least_one_value(evo, bound):
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await update_instance_settings()
    assert str(caught.value) == "Give at least one setting to change. Nothing was changed."
    assert evo.requests == []


@pytest.mark.anyio
async def test_update_settings_without_an_answer_is_uncertain_with_the_stored_settings(evo, bound):
    evo.on("GET", SETTINGS_FIND, json=STORED)
    evo.fail("POST", SETTINGS_SET, httpx2.ReadTimeout("timed out"))
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await update_instance_settings(always_online=True)
    message = str(caught.value)
    assert message.startswith("UNCERTAIN: Evolution did not confirm the result")
    assert '"always_online": false' in message
    assert "wavoip" not in message


# --- get_proxy -------------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_proxy_never_returns_the_password(evo, bound):
    evo.on(
        "GET",
        PROXY_FIND,
        json={
            "id": "cl1",
            "enabled": True,
            "host": "proxy.example.com",
            "port": "8080",
            "protocol": "http",
            "username": "ana",
            "password": "hunter2",
            "instanceId": "cl2",
            "createdAt": "2026-09-01T00:00:00.000Z",
        },
    )
    with bound(evo):
        text = await get_proxy()
    assert json.loads(text) == {
        "enabled": True,
        "host": "proxy.example.com",
        "port": "8080",
        "protocol": "http",
        "username": "ana",
        "password_set": True,
    }
    assert "hunter2" not in text


@pytest.mark.anyio
async def test_get_proxy_without_password_reports_password_not_set(evo, bound):
    evo.on(
        "GET",
        PROXY_FIND,
        json={
            "enabled": True,
            "host": "p.example.com",
            "port": "3128",
            "protocol": "socks5",
            "username": "",
            "password": "",
        },
    )
    with bound(evo):
        assert json.loads(await get_proxy())["password_set"] is False


@pytest.mark.anyio
async def test_get_proxy_without_a_stored_proxy_is_disabled(evo, bound):
    evo.on("GET", PROXY_FIND)
    with bound(evo):
        assert json.loads(await get_proxy()) == {"enabled": False}


# --- set_proxy -------------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_set_proxy_sends_the_port_as_a_string_and_hides_the_password_in_the_answer(evo, bound):
    stored = {
        "enabled": True,
        "host": "proxy.example.com",
        "port": "8080",
        "protocol": "socks5",
        "username": "ana",
        "password": "hunter2",
    }
    evo.on("POST", PROXY_SET, status=201, json={"proxy": {"instanceName": INSTANCE, "proxy": stored}})
    with bound(evo):
        text = await set_proxy(
            enabled=True, host="proxy.example.com", port=8080, protocol="socks5", username="ana", password="hunter2"
        )
    assert evo.last("POST", PROXY_SET).json == stored
    assert json.loads(text) == {
        "enabled": True,
        "host": "proxy.example.com",
        "port": "8080",
        "protocol": "socks5",
        "username": "ana",
        "password_set": True,
    }
    assert "hunter2" not in text


@pytest.mark.anyio
async def test_set_proxy_omits_credentials_that_were_not_given(evo, bound):
    answer = {"proxy": {"proxy": {"enabled": True, "host": "p", "port": "1", "protocol": "http"}}}
    evo.on("POST", PROXY_SET, status=201, json=answer)
    with bound(evo):
        await set_proxy(enabled=True, host="p", port=1, protocol="http")
    assert evo.last("POST", PROXY_SET).json == {"enabled": True, "host": "p", "port": "1", "protocol": "http"}


@pytest.mark.anyio
async def test_set_proxy_disable_sends_placeholders_and_reports_the_blanked_row(evo, bound):
    blanked = {"enabled": False, "host": "", "port": "", "protocol": "", "username": "", "password": ""}
    evo.on("POST", PROXY_SET, status=201, json={"proxy": {"instanceName": INSTANCE, "proxy": blanked}})
    with bound(evo):
        result = json.loads(await set_proxy(enabled=False))
    assert evo.last("POST", PROXY_SET).json == {"enabled": False, "host": "disabled", "port": "0", "protocol": "http"}
    assert result["enabled"] is False
    assert result["password_set"] is False


@pytest.mark.anyio
async def test_set_proxy_enabling_needs_host_port_and_protocol(evo, bound):
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await set_proxy(enabled=True, host="p.example.com")
    assert str(caught.value) == "Enabling the proxy needs port, protocol. Nothing was changed."
    assert evo.requests == []


@pytest.mark.anyio
async def test_set_proxy_rejected_by_evolution_is_a_refusal(evo, bound):
    refusal = {"status": 400, "error": "Bad Request", "response": {"message": ["Invalid proxy"]}}
    evo.on("POST", PROXY_SET, status=400, json=refusal)
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await set_proxy(enabled=True, host="dead.example.com", port=1, protocol="http")
    assert str(caught.value) == "Evolution refused the request (400): Invalid proxy. Nothing was changed."


@pytest.mark.anyio
async def test_set_proxy_reads_the_stored_row_when_the_answer_has_no_proxy(evo, bound):
    evo.on("POST", PROXY_SET, status=201, json={})
    evo.on(
        "GET",
        PROXY_FIND,
        json={"enabled": True, "host": "p", "port": "1", "protocol": "http", "username": "u", "password": "secret-pw"},
    )
    with bound(evo):
        text = await set_proxy(enabled=True, host="p", port=1, protocol="http", username="u", password="secret-pw")
    assert json.loads(text)["password_set"] is True
    assert "secret-pw" not in text


@pytest.mark.anyio
async def test_set_proxy_without_an_answer_is_uncertain_and_hides_the_password(evo, bound):
    evo.fail("POST", PROXY_SET, httpx2.ReadTimeout("timed out"))
    evo.on("GET", PROXY_FIND, json={"enabled": False, "host": "", "port": "", "protocol": "", "password": ""})
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await set_proxy(enabled=True, host="p", port=1, protocol="http", password="secret-pw")
    message = str(caught.value)
    assert message.startswith("UNCERTAIN:")
    assert '"enabled": false' in message
    assert "secret-pw" not in message
