import json
import typing

import httpx2
import pytest

from evolution_api_mcp.errors import ToolExecutionError
from evolution_api_mcp.tools.events import (
    EVENT_NAMES,
    EventName,
    get_event_channel,
    get_webhook,
    set_event_channel,
    set_webhook,
)
from tests.conftest import INSTANCE

WEBHOOK_FIND = f"/webhook/find/{INSTANCE}"
WEBHOOK_SET = f"/webhook/set/{INSTANCE}"


def _channel(channel, action):
    return f"/{channel}/{action}/{INSTANCE}"


def test_event_names_are_the_31_evolution_events_and_the_literal_matches():
    assert len(EVENT_NAMES) == 31
    assert len(set(EVENT_NAMES)) == 31
    assert typing.get_args(EventName) == EVENT_NAMES
    assert {"MESSAGES_UPSERT", "SEND_MESSAGE", "CONNECTION_UPDATE", "STATUS_INSTANCE"} <= set(EVENT_NAMES)


# --- get_webhook -----------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_webhook_keeps_header_names_and_hides_their_values(evo, bound):
    evo.on(
        "GET",
        WEBHOOK_FIND,
        json={
            "id": "cl1",
            "url": "https://hooks.example.com/evo",
            "headers": {"Authorization": "Bearer top-secret", "jwt_key": "signing-key"},
            "enabled": True,
            "events": ["MESSAGES_UPSERT"],
            "webhookByEvents": True,
            "webhookBase64": False,
            "createdAt": "2026-09-01T00:00:00.000Z",
            "instanceId": "cl2",
        },
    )
    with bound(evo):
        text = await get_webhook()
    assert json.loads(text) == {
        "enabled": True,
        "url": "https://hooks.example.com/evo",
        "by_events": True,
        "include_media_base64": False,
        "events": ["MESSAGES_UPSERT"],
        "headers": {"Authorization": "[redacted]", "jwt_key": "[redacted]"},
    }
    assert "top-secret" not in text
    assert "signing-key" not in text


@pytest.mark.anyio
async def test_get_webhook_without_headers_has_an_empty_header_map(evo, bound):
    evo.on(
        "GET",
        WEBHOOK_FIND,
        json={
            "url": "https://h.example.com",
            "headers": None,
            "enabled": False,
            "events": [],
            "webhookByEvents": False,
        },
    )
    with bound(evo):
        assert json.loads(await get_webhook())["headers"] == {}


@pytest.mark.anyio
async def test_get_webhook_without_a_stored_webhook_is_not_configured(evo, bound):
    evo.on("GET", WEBHOOK_FIND)
    with bound(evo):
        assert json.loads(await get_webhook()) == {"configured": False}


# --- set_webhook -----------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_set_webhook_sends_the_wrapped_body_and_returns_the_view_of_the_answer(evo, bound):
    evo.on(
        "POST",
        WEBHOOK_SET,
        status=201,
        json={
            "id": "cl1",
            "url": "https://hooks.example.com/evo",
            "headers": {"Authorization": "Bearer top-secret"},
            "enabled": True,
            "events": ["MESSAGES_UPSERT", "SEND_MESSAGE"],
            "webhookByEvents": False,
            "webhookBase64": True,
        },
    )
    with bound(evo):
        text = await set_webhook(
            enabled=True,
            url="https://hooks.example.com/evo",
            events=["MESSAGES_UPSERT", "SEND_MESSAGE"],
            include_media_base64=True,
            headers={"Authorization": "Bearer top-secret"},
        )
    assert evo.last("POST", WEBHOOK_SET).json == {
        "webhook": {
            "enabled": True,
            "url": "https://hooks.example.com/evo",
            "byEvents": False,
            "base64": True,
            "events": ["MESSAGES_UPSERT", "SEND_MESSAGE"],
            "headers": {"Authorization": "Bearer top-secret"},
        }
    }
    assert json.loads(text) == {
        "enabled": True,
        "url": "https://hooks.example.com/evo",
        "by_events": False,
        "include_media_base64": True,
        "events": ["MESSAGES_UPSERT", "SEND_MESSAGE"],
        "headers": {"Authorization": "[redacted]"},
    }
    assert "top-secret" not in text


@pytest.mark.anyio
async def test_set_webhook_without_headers_omits_the_key_and_reports_the_all_events_answer(evo, bound):
    evo.on(
        "POST",
        WEBHOOK_SET,
        status=201,
        json={"url": "https://h.example.com/x", "headers": None, "enabled": True, "events": list(EVENT_NAMES)},
    )
    with bound(evo):
        result = json.loads(await set_webhook(enabled=True, url="https://h.example.com/x", events=[]))
    assert evo.last("POST", WEBHOOK_SET).json == {
        "webhook": {"enabled": True, "url": "https://h.example.com/x", "byEvents": False, "base64": False, "events": []}
    }
    assert result["events"] == list(EVENT_NAMES)


@pytest.mark.anyio
async def test_set_webhook_without_an_answer_is_uncertain_with_the_stored_webhook(evo, bound):
    evo.fail("POST", WEBHOOK_SET, httpx2.ReadTimeout("timed out"))
    evo.on(
        "GET",
        WEBHOOK_FIND,
        json={"url": "https://old.example.com", "headers": {"X-Key": "old-secret"}, "enabled": True, "events": []},
    )
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await set_webhook(enabled=True, url="https://new.example.com", events=[], headers={"X-Key": "new-secret"})
    message = str(caught.value)
    assert message.startswith("UNCERTAIN:")
    assert "https://old.example.com" in message
    assert "old-secret" not in message
    assert "new-secret" not in message


# --- get_event_channel -----------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_event_channel_projects_enabled_and_events(evo, bound):
    evo.on(
        "GET",
        _channel("rabbitmq", "find"),
        json={"id": "cl1", "enabled": True, "events": ["MESSAGES_UPSERT"], "instanceId": "cl2"},
    )
    with bound(evo):
        result = json.loads(await get_event_channel(channel="rabbitmq"))
    assert result == {"channel": "rabbitmq", "enabled": True, "events": ["MESSAGES_UPSERT"]}


@pytest.mark.anyio
async def test_get_event_channel_pusher_reports_the_secret_only_as_set(evo, bound):
    evo.on(
        "GET",
        _channel("pusher", "find"),
        json={
            "enabled": True,
            "appId": "1234",
            "key": "pusher-app-key",
            "secret": "pusher-app-secret",
            "cluster": "eu",
            "useTLS": True,
            "events": ["CALL"],
        },
    )
    with bound(evo):
        text = await get_event_channel(channel="pusher")
    assert json.loads(text) == {
        "channel": "pusher",
        "enabled": True,
        "events": ["CALL"],
        "app_id": "1234",
        "key": "pusher-app-key",
        "cluster": "eu",
        "use_tls": True,
        "secret_set": True,
    }
    assert "pusher-app-secret" not in text


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("channel", "env"),
    [
        ("websocket", "WEBSOCKET_ENABLED"),
        ("rabbitmq", "RABBITMQ_ENABLED"),
        ("nats", "NATS_ENABLED"),
        ("sqs", "SQS_ENABLED"),
        ("kafka", "KAFKA_ENABLED"),
        ("pusher", "PUSHER_ENABLED"),
    ],
)
async def test_get_event_channel_disabled_on_the_server_answers_not_configured(evo, bound, channel, env):
    evo.on("GET", _channel(channel, "find"))
    with bound(evo):
        result = json.loads(await get_event_channel(channel=channel))
    assert result == {
        "channel": channel,
        "configured": False,
        "note": f"Not configured, or disabled on the Evolution server ({env}=false).",
    }


# --- set_event_channel -----------------------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize("channel", ["websocket", "rabbitmq", "nats", "sqs", "kafka"])
async def test_set_event_channel_wraps_the_body_under_the_channel_name(evo, bound, channel):
    row = {"id": "cl1", "enabled": True, "events": ["CALL", "MESSAGES_UPSERT"]}
    evo.on("POST", _channel(channel, "set"), status=201, json=row)
    with bound(evo):
        result = json.loads(await set_event_channel(channel=channel, enabled=True, events=["CALL", "MESSAGES_UPSERT"]))
    assert evo.last("POST", _channel(channel, "set")).json == {
        channel: {"enabled": True, "events": ["CALL", "MESSAGES_UPSERT"]}
    }
    assert result == {"channel": channel, "enabled": True, "events": ["CALL", "MESSAGES_UPSERT"]}


@pytest.mark.anyio
async def test_set_event_channel_pusher_sends_the_credentials_and_hides_the_secret(evo, bound):
    evo.on(
        "POST",
        _channel("pusher", "set"),
        status=201,
        json={
            "enabled": True,
            "appId": "1234",
            "key": "app-key",
            "secret": "app-secret",
            "cluster": "eu",
            "useTLS": False,
            "events": ["CALL"],
        },
    )
    with bound(evo):
        text = await set_event_channel(
            channel="pusher",
            enabled=True,
            events=["CALL"],
            pusher_app_id="1234",
            pusher_key="app-key",
            pusher_secret="app-secret",
            pusher_cluster="eu",
            pusher_use_tls=False,
        )
    assert evo.last("POST", _channel("pusher", "set")).json == {
        "pusher": {
            "enabled": True,
            "appId": "1234",
            "key": "app-key",
            "secret": "app-secret",
            "cluster": "eu",
            "useTLS": False,
            "events": ["CALL"],
        }
    }
    result = json.loads(text)
    assert result["secret_set"] is True
    assert result["use_tls"] is False
    assert "app-secret" not in text


@pytest.mark.anyio
async def test_set_event_channel_pusher_needs_all_four_values(evo, bound):
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await set_event_channel(channel="pusher", enabled=True, events=[], pusher_app_id="1234", pusher_key="k")
    assert str(caught.value) == "The pusher channel needs pusher_secret, pusher_cluster. Nothing was changed."
    assert evo.requests == []


@pytest.mark.anyio
async def test_set_event_channel_disabled_on_the_server_saves_nothing(evo, bound):
    evo.on("POST", _channel("sqs", "set"), status=201)
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await set_event_channel(channel="sqs", enabled=True, events=[])
    assert str(caught.value) == (
        "The sqs channel is disabled on this Evolution server (SQS_ENABLED=false), so nothing was saved."
    )


@pytest.mark.anyio
async def test_set_event_channel_without_an_answer_is_uncertain_with_the_stored_channel(evo, bound):
    evo.fail("POST", _channel("websocket", "set"), httpx2.ReadTimeout("timed out"))
    evo.on("GET", _channel("websocket", "find"), json={"enabled": False, "events": []})
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await set_event_channel(channel="websocket", enabled=True, events=[])
    message = str(caught.value)
    assert message.startswith("UNCERTAIN:")
    assert '{"channel": "websocket", "enabled": false, "events": []}' in message
