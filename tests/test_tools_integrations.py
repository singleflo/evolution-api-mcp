import json
import re

import httpx2
import pytest

from evolution_api_mcp.context import InstanceIdentity
from evolution_api_mcp.errors import ToolExecutionError
from evolution_api_mcp.registry import BAILEYS
from evolution_api_mcp.tools import integrations
from tests.conftest import BASE, INSTANCE

PERSON = "393331234567@s.whatsapp.net"
OTHER = "393339999999@s.whatsapp.net"
SECRET_KEY = "sk-secret-key-123456789"
SECRET_PASSWORD = "n8n-secret-password"
DISABLED = {"status": 400, "error": "Bad Request", "response": {"message": ["Typebot is disabled"]}}

TYPEBOT_ROW = {
    "id": "bot-typebot-1",
    "enabled": True,
    "description": "Support flow",
    "url": "https://typebot.example.com",
    "typebot": "support-flow",
    "expire": 20,
    "keywordFinish": "bye",
    "delayMessage": 1000,
    "unknownMessage": "Sorry",
    "listeningFromMe": False,
    "stopBotFromMe": False,
    "keepOpen": False,
    "debounceTime": 1,
    "ignoreJids": [],
    "triggerType": "keyword",
    "triggerOperator": "contains",
    "triggerValue": "help",
    "splitMessages": False,
    "timePerChar": 50,
    "instanceId": "cuid-instance",
    "createdAt": "2026-09-01T10:00:00.000Z",
    "updatedAt": "2026-09-02T10:00:00.000Z",
}
EVOLUTION_BOT_ROW = {
    "id": "bot-evo-1",
    "enabled": True,
    "description": "Evo bot",
    "apiUrl": "https://bot.example.com/run",
    "apiKey": SECRET_KEY,
    "triggerType": "all",
    "triggerOperator": None,
    "triggerValue": None,
    "expire": 0,
    "keywordFinish": "bye",
    "delayMessage": 500,
    "unknownMessage": None,
    "listeningFromMe": False,
    "stopBotFromMe": True,
    "keepOpen": False,
    "debounceTime": 2,
    "ignoreJids": ["@g.us"],
    "splitMessages": True,
    "timePerChar": 40,
    "instanceId": "cuid-instance",
    "createdAt": "2026-09-01T10:00:00.000Z",
    "updatedAt": "2026-09-02T10:00:00.000Z",
}
SETTINGS_ROW = {
    "id": "settings-1",
    "expire": 300,
    "keywordFinish": "bye",
    "delayMessage": 1000,
    "unknownMessage": "Sorry, I dont understand",
    "listeningFromMe": True,
    "stopBotFromMe": True,
    "keepOpen": False,
    "debounceTime": 1,
    "ignoreJids": [],
    "splitMessages": False,
    "timePerChar": 50,
    "fallbackId": None,
    "fallback": None,
    "Fallback": None,
    "instanceId": "cuid-instance",
}


def _path(endpoint: str, *segments: str) -> str:
    return "/" + "/".join([endpoint.strip("/"), *segments, INSTANCE])


def _create_kwargs(**overrides):
    kwargs = {"description": "Bot", "trigger_type": "all"}
    kwargs.update(overrides)
    return kwargs


# --- list_chatbots ---------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_chatbots_merges_providers_reports_disabled_and_hides_secrets(evo, bound):
    for name in ("evolutionBot", "dify", "flowise", "n8n", "evoai", "openai"):
        evo.on("GET", _path(f"/{name}/find"), json=[])
    evo.on("GET", _path("/evolutionBot/find"), json=[EVOLUTION_BOT_ROW])
    evo.on(
        "GET",
        _path("/openai/find"),
        json=[
            {
                "id": "bot-openai-1",
                "enabled": False,
                "description": "GPT",
                "botType": "chatCompletion",
                "model": "gpt-4o",
                "openaiCredsId": "cred-1",
                "triggerType": "keyword",
                "triggerOperator": "equals",
                "triggerValue": "gpt",
                "maxTokens": 300,
            }
        ],
    )
    evo.on("GET", _path("/typebot/find"), status=400, json=DISABLED)
    with bound(evo):
        text = await integrations.list_chatbots()
    result = json.loads(text)
    assert result["disabled_providers"] == ["typebot"]
    assert result["chatbots"] == [
        {
            "provider": "evolution_bot",
            "bot_id": "bot-evo-1",
            "enabled": True,
            "description": "Evo bot",
            "trigger_type": "all",
            "endpoint_url": "https://bot.example.com/run",
        },
        {
            "provider": "openai",
            "bot_id": "bot-openai-1",
            "enabled": False,
            "description": "GPT",
            "trigger_type": "keyword",
            "trigger_operator": "equals",
            "trigger_value": "gpt",
            "bot_type": "chatCompletion",
            "model": "gpt-4o",
        },
    ]
    assert SECRET_KEY not in text
    assert "cuid-instance" not in text


@pytest.mark.anyio
async def test_list_chatbots_single_provider_asks_only_that_provider(evo, bound):
    evo.on("GET", _path("/n8n/find"), json=[])
    with bound(evo):
        result = json.loads(await integrations.list_chatbots(provider="n8n"))
    assert result == {"chatbots": [], "disabled_providers": []}
    assert [(r.method, r.path) for r in evo.requests] == [("GET", f"/n8n/find/{INSTANCE}")]


@pytest.mark.anyio
async def test_list_chatbots_single_disabled_provider_is_reported_not_failed(evo, bound):
    evo.on("GET", _path("/typebot/find"), status=400, json=DISABLED)
    with bound(evo):
        result = json.loads(await integrations.list_chatbots(provider="typebot"))
    assert result == {"chatbots": [], "disabled_providers": ["typebot"]}


@pytest.mark.anyio
async def test_list_chatbots_other_refusals_still_fail(evo, bound):
    evo.on(
        "GET",
        _path("/typebot/find"),
        status=400,
        json={"status": 400, "error": "Bad Request", "response": {"message": ["Something else"]}},
    )
    with bound(evo), pytest.raises(ToolExecutionError, match=re.escape("Evolution refused the request (400)")):
        await integrations.list_chatbots(provider="typebot")


# --- get_chatbot -----------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_chatbot_projects_snake_case_and_shows_secret_only_as_flag(evo, bound):
    evo.on("GET", _path("/evolutionBot/fetch", "bot-evo-1"), json=EVOLUTION_BOT_ROW)
    with bound(evo):
        text = await integrations.get_chatbot(provider="evolution_bot", bot_id="bot-evo-1")
    assert json.loads(text) == {
        "provider": "evolution_bot",
        "bot_id": "bot-evo-1",
        "enabled": True,
        "description": "Evo bot",
        "trigger_type": "all",
        "expire_minutes": 0,
        "keyword_finish": "bye",
        "delay_message_ms": 500,
        "listening_from_me": False,
        "stop_bot_from_me": True,
        "keep_open": False,
        "debounce_seconds": 2,
        "ignored_chats": ["@g.us"],
        "split_messages": True,
        "time_per_char_ms": 40,
        "endpoint_url": "https://bot.example.com/run",
        "api_key_set": True,
    }
    assert SECRET_KEY not in text
    assert "cuid-instance" not in text
    assert "createdAt" not in text


@pytest.mark.anyio
async def test_get_chatbot_n8n_password_is_a_flag_and_openai_fields_map(evo, bound):
    evo.on(
        "GET",
        _path("/n8n/fetch", "n8n-1"),
        json={
            "id": "n8n-1",
            "enabled": True,
            "webhookUrl": "https://n8n.example.com/webhook/x",
            "basicAuthUser": "robot",
            "basicAuthPass": SECRET_PASSWORD,
            "triggerType": "none",
        },
    )
    evo.on(
        "GET",
        _path("/openai/fetch", "oa-1"),
        json={
            "id": "oa-1",
            "enabled": True,
            "openaiCredsId": "cred-1",
            "botType": "assistant",
            "assistantId": "asst_1",
            "systemMessages": ["Be brief."],
            "assistantMessages": ["ignored by the projection"],
            "maxTokens": 400,
            "triggerType": "all",
        },
    )
    with bound(evo):
        n8n_text = await integrations.get_chatbot(provider="n8n", bot_id="n8n-1")
        openai_text = await integrations.get_chatbot(provider="openai", bot_id="oa-1")
    n8n = json.loads(n8n_text)
    assert n8n["endpoint_url"] == "https://n8n.example.com/webhook/x"
    assert n8n["basic_auth_user"] == "robot"
    assert n8n["basic_auth_password_set"] is True
    assert SECRET_PASSWORD not in n8n_text
    openai = json.loads(openai_text)
    assert openai["openai_credential_id"] == "cred-1"
    assert openai["assistant_id"] == "asst_1"
    assert openai["system_messages"] == ["Be brief."]
    assert openai["max_tokens"] == 400
    assert "api_key_set" not in openai
    assert "assistantMessages" not in openai_text


@pytest.mark.anyio
async def test_get_chatbot_unknown_id_is_refused(evo, bound):
    evo.on("GET", _path("/typebot/fetch", "nope"))
    with (
        bound(evo),
        pytest.raises(
            ToolExecutionError, match=re.escape("Chatbot nope was not found for provider typebot. Use list_chatbots")
        ),
    ):
        await integrations.get_chatbot(provider="typebot", bot_id="nope")


@pytest.mark.anyio
async def test_disabled_provider_message_on_any_route(evo, bound):
    evo.on("GET", _path("/typebot/fetch", "b1"), status=400, json=DISABLED)
    with (
        bound(evo),
        pytest.raises(
            ToolExecutionError, match=re.escape("The typebot integration is disabled on this Evolution server.")
        ),
    ):
        await integrations.get_chatbot(provider="typebot", bot_id="b1")


# --- create_chatbot --------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_typebot_sends_the_camel_case_body_and_normalises_ignored_chats(evo, bound):
    evo.on("POST", _path("/typebot/create"), status=201, json={**TYPEBOT_ROW, "ignoreJids": [PERSON, "@g.us"]})
    with bound(evo):
        text = await integrations.create_chatbot(
            provider="typebot",
            description="Support flow",
            trigger_type="keyword",
            trigger_operator="contains",
            trigger_value="help",
            endpoint_url="https://typebot.example.com",
            typebot_flow="support-flow",
            expire_minutes=20,
            ignored_chats=["+39 333 123 4567", "@g.us", PERSON],
        )
    assert evo.last("POST", _path("/typebot/create")).json == {
        "description": "Support flow",
        "triggerType": "keyword",
        "triggerOperator": "contains",
        "triggerValue": "help",
        "enabled": True,
        "url": "https://typebot.example.com",
        "typebot": "support-flow",
        "expire": 20,
        "ignoreJids": [PERSON, "@g.us"],
    }
    result = json.loads(text)
    assert result["bot_id"] == "bot-typebot-1"
    assert result["typebot_flow"] == "support-flow"
    assert result["endpoint_url"] == "https://typebot.example.com"
    assert result["ignored_chats"] == [PERSON, "@g.us"]
    assert "cuid-instance" not in text


@pytest.mark.anyio
async def test_create_evolution_bot_keeps_key_out_of_the_answer(evo, bound):
    evo.on("POST", _path("/evolutionBot/create"), status=201, json=EVOLUTION_BOT_ROW)
    with bound(evo):
        text = await integrations.create_chatbot(
            provider="evolution_bot",
            **_create_kwargs(endpoint_url="https://bot.example.com/run", api_key=SECRET_KEY, split_messages=True),
        )
    assert evo.last("POST", _path("/evolutionBot/create")).json == {
        "description": "Bot",
        "triggerType": "all",
        "enabled": True,
        "apiUrl": "https://bot.example.com/run",
        "apiKey": SECRET_KEY,
        "splitMessages": True,
    }
    result = json.loads(text)
    assert result["api_key_set"] is True
    assert SECRET_KEY not in text


@pytest.mark.anyio
async def test_create_n8n_sends_basic_auth_as_evolution_reads_it(evo, bound):
    evo.on(
        "POST",
        _path("/n8n/create"),
        status=201,
        json={"id": "n8n-1", "enabled": True, "basicAuthPass": SECRET_PASSWORD},
    )
    with bound(evo):
        text = await integrations.create_chatbot(
            provider="n8n",
            **_create_kwargs(
                endpoint_url="https://n8n.example.com/webhook/x",
                basic_auth_user="robot",
                basic_auth_password=SECRET_PASSWORD,
                api_key=SECRET_KEY,
            ),
        )
    body = evo.last("POST", _path("/n8n/create")).json
    assert body["webhookUrl"] == "https://n8n.example.com/webhook/x"
    assert body["basicAuthUser"] == "robot"
    assert body["basicAuthPass"] == SECRET_PASSWORD
    assert "apiKey" not in body
    assert json.loads(text)["basic_auth_password_set"] is True
    assert SECRET_PASSWORD not in text


@pytest.mark.anyio
async def test_create_typebot_leaves_out_fields_it_does_not_support(evo, bound):
    evo.on("POST", _path("/typebot/create"), status=201, json=TYPEBOT_ROW)
    with bound(evo):
        await integrations.create_chatbot(
            provider="typebot",
            **_create_kwargs(
                endpoint_url="https://typebot.example.com",
                typebot_flow="f",
                api_key=SECRET_KEY,
                split_messages=True,
                time_per_char_ms=10,
            ),
        )
    body = evo.last("POST", _path("/typebot/create")).json
    assert set(body) == {"description", "triggerType", "enabled", "url", "typebot"}


@pytest.mark.anyio
async def test_create_openai_chat_completion_body(evo, bound):
    evo.on(
        "POST", _path("/openai/create"), status=201, json={"id": "oa-1", "enabled": True, "botType": "chatCompletion"}
    )
    with bound(evo):
        await integrations.create_chatbot(
            provider="openai",
            **_create_kwargs(
                openai_credential_id="cred-1",
                bot_type="chatCompletion",
                model="gpt-4o",
                max_tokens=300,
                system_messages=["Be brief."],
                enabled=False,
            ),
        )
    assert evo.last("POST", _path("/openai/create")).json == {
        "description": "Bot",
        "triggerType": "all",
        "enabled": False,
        "openaiCredsId": "cred-1",
        "botType": "chatCompletion",
        "model": "gpt-4o",
        "maxTokens": 300,
        "systemMessages": ["Be brief."],
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider", "kwargs", "missing"),
    [
        ("typebot", {}, "endpoint_url, typebot_flow"),
        ("typebot", {"endpoint_url": "https://t.example.com"}, "typebot_flow"),
        ("evolution_bot", {}, "endpoint_url"),
        ("flowise", {}, "endpoint_url"),
        ("evoai", {}, "endpoint_url"),
        ("n8n", {}, "endpoint_url"),
        ("dify", {}, "bot_type"),
        ("openai", {}, "openai_credential_id, bot_type"),
        ("openai", {"openai_credential_id": "c", "bot_type": "assistant"}, "assistant_id"),
        ("openai", {"openai_credential_id": "c", "bot_type": "chatCompletion"}, "model, max_tokens"),
        ("openai", {"openai_credential_id": "c", "bot_type": "chatCompletion", "model": "m"}, "max_tokens"),
    ],
)
async def test_create_chatbot_lists_the_missing_required_fields(evo, bound, provider, kwargs, missing):
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await integrations.create_chatbot(provider=provider, **_create_kwargs(**kwargs))
    assert str(caught.value) == f"A {provider} chatbot needs {missing}. Nothing was changed."
    assert evo.requests == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider", "bot_type", "extra"),
    [
        ("typebot", "chatBot", {"endpoint_url": "https://t.example.com", "typebot_flow": "f"}),
        ("n8n", "agent", {"endpoint_url": "https://n8n.example.com/x"}),
        ("dify", "assistant", {}),
        ("openai", "workflow", {"openai_credential_id": "c"}),
    ],
)
async def test_create_chatbot_refuses_a_bot_type_the_provider_does_not_have(evo, bound, provider, bot_type, extra):
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await integrations.create_chatbot(provider=provider, **_create_kwargs(bot_type=bot_type, **extra))
    assert str(caught.value) == (
        "bot_type applies to dify (chatBot, textGenerator, agent, workflow) and openai (assistant, chatCompletion) "
        "only."
    )
    assert evo.requests == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("trigger", "message"),
    [
        ({"trigger_type": "keyword"}, "trigger_type 'keyword' needs trigger_operator and trigger_value."),
        (
            {"trigger_type": "keyword", "trigger_operator": "equals"},
            "trigger_type 'keyword' needs trigger_operator and trigger_value.",
        ),
        ({"trigger_type": "advanced"}, "trigger_type 'advanced' needs trigger_value."),
    ],
)
async def test_create_chatbot_trigger_rules(evo, bound, trigger, message):
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await integrations.create_chatbot(
            provider="flowise", **{"description": "Bot", "endpoint_url": "https://f.example.com", **trigger}
        )
    assert str(caught.value) == message
    assert evo.requests == []


@pytest.mark.anyio
async def test_create_chatbot_refuses_a_bad_ignored_chat_before_calling_evolution(evo, bound):
    with bound(evo), pytest.raises(ToolExecutionError, match="Unsupported chat id"):
        await integrations.create_chatbot(
            provider="flowise", **_create_kwargs(endpoint_url="https://f.example.com", ignored_chats=["nonsense"])
        )
    assert evo.requests == []


@pytest.mark.anyio
async def test_create_chatbot_on_a_disabled_provider(evo, bound):
    evo.on("POST", _path("/dify/create"), status=400, json={"response": {"message": ["Dify is disabled"]}})
    with (
        bound(evo),
        pytest.raises(
            ToolExecutionError, match=re.escape("The dify integration is disabled on this Evolution server.")
        ),
    ):
        await integrations.create_chatbot(provider="dify", **_create_kwargs(bot_type="agent"))


@pytest.mark.anyio
async def test_create_chatbot_timeout_is_reported_as_uncertain(evo, bound):
    evo.fail("POST", _path("/flowise/create"), httpx2.ReadTimeout("no answer"))
    with bound(evo), pytest.raises(ToolExecutionError, match="UNCERTAIN"):
        await integrations.create_chatbot(provider="flowise", **_create_kwargs(endpoint_url="https://f.example.com"))


# --- update_chatbot --------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_update_chatbot_overlays_and_reuses_the_stored_secret(evo, bound):
    evo.on("GET", _path("/evolutionBot/fetch", "bot-evo-1"), json=EVOLUTION_BOT_ROW)
    evo.on("PUT", _path("/evolutionBot/update", "bot-evo-1"), json={**EVOLUTION_BOT_ROW, "enabled": False})
    with bound(evo):
        text = await integrations.update_chatbot(provider="evolution_bot", bot_id="bot-evo-1", enabled=False)
    body = evo.last("PUT", _path("/evolutionBot/update", "bot-evo-1")).json
    assert body == {
        "enabled": False,
        "description": "Evo bot",
        "apiUrl": "https://bot.example.com/run",
        "apiKey": SECRET_KEY,
        "triggerType": "all",
        "expire": 0,
        "keywordFinish": "bye",
        "delayMessage": 500,
        "listeningFromMe": False,
        "stopBotFromMe": True,
        "keepOpen": False,
        "debounceTime": 2,
        "ignoreJids": ["@g.us"],
        "splitMessages": True,
        "timePerChar": 40,
    }
    result = json.loads(text)
    assert result["enabled"] is False
    assert result["api_key_set"] is True
    assert SECRET_KEY not in text


@pytest.mark.anyio
async def test_update_chatbot_given_secret_and_endpoint_win_over_stored_ones(evo, bound):
    evo.on("GET", _path("/evolutionBot/fetch", "bot-evo-1"), json=EVOLUTION_BOT_ROW)
    evo.on("PUT", _path("/evolutionBot/update", "bot-evo-1"), json=EVOLUTION_BOT_ROW)
    with bound(evo):
        await integrations.update_chatbot(
            provider="evolution_bot",
            bot_id="bot-evo-1",
            api_key="sk-new-key-abcdefghij",
            endpoint_url="https://new.example.com/run",
        )
    body = evo.last("PUT", _path("/evolutionBot/update", "bot-evo-1")).json
    assert body["apiKey"] == "sk-new-key-abcdefghij"
    assert body["apiUrl"] == "https://new.example.com/run"


@pytest.mark.anyio
async def test_update_n8n_keeps_the_stored_basic_auth_password(evo, bound):
    row = {
        "id": "n8n-1",
        "enabled": True,
        "webhookUrl": "https://n8n.example.com/webhook/x",
        "basicAuthUser": "robot",
        "basicAuthPass": SECRET_PASSWORD,
        "triggerType": "all",
        "instanceId": "cuid-instance",
    }
    evo.on("GET", _path("/n8n/fetch", "n8n-1"), json=row)
    evo.on("PUT", _path("/n8n/update", "n8n-1"), json=row)
    with bound(evo):
        await integrations.update_chatbot(provider="n8n", bot_id="n8n-1", description="Renamed")
    assert evo.last("PUT", _path("/n8n/update", "n8n-1")).json == {
        "enabled": True,
        "webhookUrl": "https://n8n.example.com/webhook/x",
        "basicAuthUser": "robot",
        "basicAuthPass": SECRET_PASSWORD,
        "triggerType": "all",
        "description": "Renamed",
    }


@pytest.mark.anyio
async def test_update_openai_bot_drops_fields_evolution_does_not_take_back(evo, bound):
    row = {
        "id": "oa-1",
        "enabled": True,
        "openaiCredsId": "cred-1",
        "botType": "assistant",
        "assistantId": "asst_1",
        "assistantMessages": ["x"],
        "userMessages": ["y"],
        "triggerType": "all",
        "splitMessages": False,
    }
    evo.on("GET", _path("/openai/fetch", "oa-1"), json=row)
    evo.on("PUT", _path("/openai/update", "oa-1"), json=row)
    with bound(evo):
        await integrations.update_chatbot(provider="openai", bot_id="oa-1", assistant_id="asst_2")
    assert evo.last("PUT", _path("/openai/update", "oa-1")).json == {
        "enabled": True,
        "openaiCredsId": "cred-1",
        "botType": "assistant",
        "assistantId": "asst_2",
        "triggerType": "all",
    }


@pytest.mark.anyio
async def test_update_chatbot_checks_trigger_rules_against_the_merged_bot(evo, bound):
    row = {**TYPEBOT_ROW, "triggerType": "all", "triggerOperator": None, "triggerValue": None}
    evo.on("GET", _path("/typebot/fetch", "bot-typebot-1"), json=row)
    with bound(evo), pytest.raises(ToolExecutionError, match="trigger_type 'keyword' needs trigger_operator"):
        await integrations.update_chatbot(provider="typebot", bot_id="bot-typebot-1", trigger_type="keyword")
    assert [r.method for r in evo.requests] == ["GET"]


@pytest.mark.anyio
async def test_update_chatbot_keeps_a_stored_keyword_trigger(evo, bound):
    evo.on("GET", _path("/typebot/fetch", "bot-typebot-1"), json=TYPEBOT_ROW)
    evo.on("PUT", _path("/typebot/update", "bot-typebot-1"), json=TYPEBOT_ROW)
    with bound(evo):
        await integrations.update_chatbot(provider="typebot", bot_id="bot-typebot-1", trigger_value="assist")
    body = evo.last("PUT", _path("/typebot/update", "bot-typebot-1")).json
    assert body["triggerType"] == "keyword"
    assert body["triggerOperator"] == "contains"
    assert body["triggerValue"] == "assist"


@pytest.mark.anyio
async def test_update_chatbot_refuses_a_foreign_bot_type_and_unknown_bot(evo, bound):
    evo.on("GET", _path("/typebot/fetch", "gone"))
    with bound(evo):
        with pytest.raises(ToolExecutionError, match="bot_type applies to dify"):
            await integrations.update_chatbot(provider="typebot", bot_id="gone", bot_type="agent")
        with pytest.raises(ToolExecutionError, match="Chatbot gone was not found for provider typebot"):
            await integrations.update_chatbot(provider="typebot", bot_id="gone", enabled=True)


# --- delete_chatbot --------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_delete_chatbot(evo, bound):
    evo.on("DELETE", _path("/flowise/delete", "fl-1"), json={"bot": {"id": "fl-1"}})
    with bound(evo):
        result = json.loads(await integrations.delete_chatbot(provider="flowise", bot_id="fl-1"))
    assert result == {"deleted": True, "provider": "flowise", "bot_id": "fl-1"}
    assert evo.last("DELETE", _path("/flowise/delete", "fl-1")).json is None


# --- chatbot defaults ------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_chatbot_settings_projects_defaults_and_fallback(evo, bound):
    evo.on(
        "GET",
        _path("/typebot/fetchSettings"),
        json={**SETTINGS_ROW, "typebotIdFallback": "bot-typebot-1", "fallbackId": "bot-typebot-1"},
    )
    with bound(evo):
        result = json.loads(await integrations.get_chatbot_settings(provider="typebot"))
    assert result == {
        "provider": "typebot",
        "expire_minutes": 300,
        "keyword_finish": "bye",
        "delay_message_ms": 1000,
        "unknown_message": "Sorry, I dont understand",
        "listening_from_me": True,
        "stop_bot_from_me": True,
        "keep_open": False,
        "debounce_seconds": 1,
        "ignored_chats": [],
        "fallback_bot_id": "bot-typebot-1",
    }


@pytest.mark.anyio
async def test_get_chatbot_settings_reads_the_providers_own_fallback_column(evo, bound):
    evo.on("GET", _path("/n8n/fetchSettings"), json={**SETTINGS_ROW, "n8nIdFallback": "n8n-9", "fallbackId": None})
    evo.on(
        "GET",
        _path("/openai/fetchSettings"),
        json={**SETTINGS_ROW, "openaiCredsId": "cred-1", "speechToText": True, "fallbackId": ""},
    )
    with bound(evo):
        n8n = json.loads(await integrations.get_chatbot_settings(provider="n8n"))
        openai = json.loads(await integrations.get_chatbot_settings(provider="openai"))
    assert n8n["fallback_bot_id"] == "n8n-9"
    assert n8n["split_messages"] is False
    assert n8n["time_per_char_ms"] == 50
    assert openai["openai_credential_id"] == "cred-1"
    assert openai["speech_to_text"] is True
    assert "fallback_bot_id" not in openai
    assert "split_messages" not in openai


@pytest.mark.anyio
async def test_update_chatbot_settings_typebot_sends_only_its_schema_keys(evo, bound):
    evo.on("GET", _path("/typebot/fetchSettings"), json=SETTINGS_ROW)
    evo.on("POST", _path("/typebot/settings"), json={**SETTINGS_ROW, "expire": 30})
    with bound(evo):
        result = json.loads(
            await integrations.update_chatbot_settings(provider="typebot", expire_minutes=30, split_messages=True)
        )
    assert evo.last("POST", _path("/typebot/settings")).json == {
        "expire": 30,
        "keywordFinish": "bye",
        "delayMessage": 1000,
        "unknownMessage": "Sorry, I dont understand",
        "listeningFromMe": True,
        "stopBotFromMe": True,
        "keepOpen": False,
        "debounceTime": 1,
        "ignoreJids": [],
    }
    assert result["expire_minutes"] == 30


@pytest.mark.anyio
async def test_update_chatbot_settings_full_provider_schema_and_fallback(evo, bound):
    evo.on("GET", _path("/evolutionBot/fetchSettings"), json=SETTINGS_ROW)
    evo.on("POST", _path("/evolutionBot/settings"), json={**SETTINGS_ROW, "fallbackId": "bot-evo-1"})
    with bound(evo):
        result = json.loads(
            await integrations.update_chatbot_settings(
                provider="evolution_bot",
                fallback_bot_id="bot-evo-1",
                ignored_chats=["+39 333 123 4567"],
                keep_open=True,
            )
        )
    assert evo.last("POST", _path("/evolutionBot/settings")).json == {
        "expire": 300,
        "keywordFinish": "bye",
        "delayMessage": 1000,
        "unknownMessage": "Sorry, I dont understand",
        "listeningFromMe": True,
        "stopBotFromMe": True,
        "keepOpen": True,
        "debounceTime": 1,
        "ignoreJids": [PERSON],
        "splitMessages": False,
        "timePerChar": 50,
        "fallbackId": "bot-evo-1",
    }
    assert result["fallback_bot_id"] == "bot-evo-1"


@pytest.mark.anyio
async def test_update_chatbot_settings_starts_from_defaults_when_nothing_is_stored(evo, bound):
    evo.on("GET", _path("/dify/fetchSettings"))
    evo.on("POST", _path("/dify/settings"), json=SETTINGS_ROW)
    with bound(evo):
        await integrations.update_chatbot_settings(provider="dify", keep_open=True)
    assert evo.last("POST", _path("/dify/settings")).json == {
        "expire": 0,
        "keywordFinish": "",
        "delayMessage": 1000,
        "unknownMessage": "",
        "listeningFromMe": False,
        "stopBotFromMe": False,
        "keepOpen": True,
        "debounceTime": 0,
        "ignoreJids": [],
        "splitMessages": False,
        "timePerChar": 0,
    }


@pytest.mark.anyio
async def test_update_chatbot_settings_flowise_has_no_split_keys(evo, bound):
    evo.on("GET", _path("/flowise/fetchSettings"), json=SETTINGS_ROW)
    evo.on("POST", _path("/flowise/settings"), json=SETTINGS_ROW)
    with bound(evo):
        await integrations.update_chatbot_settings(provider="flowise", debounce_seconds=5)
    body = evo.last("POST", _path("/flowise/settings")).json
    assert body["debounceTime"] == 5
    assert "splitMessages" not in body
    assert "timePerChar" not in body


@pytest.mark.anyio
async def test_update_chatbot_settings_openai_needs_a_credential(evo, bound):
    evo.on("GET", _path("/openai/fetchSettings"), json=SETTINGS_ROW)
    with (
        bound(evo),
        pytest.raises(
            ToolExecutionError, match=re.escape("openai_credential_id is required for OpenAI chatbot defaults.")
        ),
    ):
        await integrations.update_chatbot_settings(provider="openai", expire_minutes=10)
    assert [r.method for r in evo.requests] == ["GET"]


@pytest.mark.anyio
async def test_update_chatbot_settings_openai_reuses_stored_credential_and_takes_a_new_one(evo, bound):
    stored = {**SETTINGS_ROW, "openaiCredsId": "cred-1", "speechToText": True}
    evo.on("GET", _path("/openai/fetchSettings"), json=stored)
    evo.on("POST", _path("/openai/settings"), json=stored)
    with bound(evo):
        await integrations.update_chatbot_settings(provider="openai", expire_minutes=10)
        kept = evo.last("POST", _path("/openai/settings")).json
        await integrations.update_chatbot_settings(
            provider="openai", openai_credential_id="cred-2", speech_to_text=False
        )
        changed = evo.last("POST", _path("/openai/settings")).json
    assert kept["openaiCredsId"] == "cred-1"
    assert kept["speechToText"] is True
    assert kept["expire"] == 10
    assert changed["openaiCredsId"] == "cred-2"
    assert changed["speechToText"] is False


@pytest.mark.anyio
async def test_update_chatbot_settings_needs_at_least_one_value(evo, bound):
    with bound(evo), pytest.raises(ToolExecutionError, match="Give at least one setting to change"):
        await integrations.update_chatbot_settings(provider="typebot")
    assert evo.requests == []


# --- sessions --------------------------------------------------------------------------------------------------


def _session(session_id, chat, bot="bot-1", status="opened"):
    return {
        "id": f"row-{session_id}",
        "sessionId": session_id,
        "remoteJid": chat,
        "pushName": "Ana",
        "status": status,
        "awaitUser": False,
        "context": {"secret": "x"},
        "type": "typebot",
        "createdAt": "2026-09-29T21:04:05.123Z",
        "updatedAt": "2026-09-29T21:10:00.000Z",
        "instanceId": "cuid-instance",
        "botId": bot,
    }


@pytest.mark.anyio
async def test_list_chatbot_sessions_projects_and_filters_by_chat(evo, bound):
    evo.on(
        "GET",
        _path("/typebot/fetchSessions", "bot-1"),
        json=[_session("s1", PERSON), _session("s2", OTHER, status="paused"), _session("s3", PERSON, bot="bot-2")],
    )
    with bound(evo):
        everything = json.loads(await integrations.list_chatbot_sessions(provider="typebot", bot_id="bot-1"))
        one_chat = json.loads(
            await integrations.list_chatbot_sessions(provider="typebot", bot_id="bot-1", chat="+39 333 123 4567")
        )
    assert everything["sessions"] == [
        {
            "session_id": "s1",
            "chat_id": PERSON,
            "status": "opened",
            "started_at": "2026-09-29T21:04:05Z",
            "updated_at": "2026-09-29T21:10:00Z",
        },
        {
            "session_id": "s2",
            "chat_id": OTHER,
            "status": "paused",
            "started_at": "2026-09-29T21:04:05Z",
            "updated_at": "2026-09-29T21:10:00Z",
        },
    ]
    assert [s["session_id"] for s in one_chat["sessions"]] == ["s1"]


@pytest.mark.anyio
async def test_list_chatbot_sessions_empty(evo, bound):
    evo.on("GET", _path("/n8n/fetchSessions", "bot-1"), json=[])
    with bound(evo):
        assert json.loads(await integrations.list_chatbot_sessions(provider="n8n", bot_id="bot-1")) == {"sessions": []}


@pytest.mark.anyio
async def test_change_chatbot_session(evo, bound):
    evo.on("POST", _path("/dify/changeStatus"), json={"bot": {"remoteJid": PERSON, "status": "paused"}})
    with bound(evo):
        result = json.loads(
            await integrations.change_chatbot_session(provider="dify", chat="393331234567", status="paused")
        )
    assert result == {"chat_id": PERSON, "status": "paused"}
    assert evo.last("POST", _path("/dify/changeStatus")).json == {"remoteJid": PERSON, "status": "paused"}


@pytest.mark.anyio
@pytest.mark.parametrize(("ignored", "action"), [(True, "add"), (False, "remove")])
async def test_set_chatbot_ignored_chat(evo, bound, ignored, action):
    evo.on("POST", _path("/evoai/ignoreJid"), json={"ignoreJids": [PERSON]})
    with bound(evo):
        result = json.loads(await integrations.set_chatbot_ignored_chat(provider="evoai", chat=PERSON, ignored=ignored))
    assert result == {"chat_id": PERSON, "ignored": ignored}
    assert evo.last("POST", _path("/evoai/ignoreJid")).json == {"remoteJid": PERSON, "action": action}


@pytest.mark.anyio
async def test_start_typebot_session_maps_variables(evo, bound):
    evo.on("POST", _path("/typebot/start"), json={"typebot": {"id": "x"}})
    with bound(evo):
        result = json.loads(
            await integrations.start_typebot_session(
                chat="393331234567",
                typebot_url="https://typebot.example.com",
                typebot_flow="support-flow",
                variables={"name": "Ana", "plan": "pro"},
                start_session=True,
            )
        )
    assert result == {"chat_id": PERSON, "started": True}
    assert evo.last("POST", _path("/typebot/start")).json == {
        "remoteJid": PERSON,
        "url": "https://typebot.example.com",
        "typebot": "support-flow",
        "startSession": True,
        "variables": [{"name": "name", "value": "Ana"}, {"name": "plan", "value": "pro"}],
    }


@pytest.mark.anyio
async def test_start_typebot_session_defaults_and_disabled_provider(evo, bound):
    evo.on("POST", _path("/typebot/start"), status=400, json=DISABLED)
    with (
        bound(evo),
        pytest.raises(
            ToolExecutionError, match=re.escape("The typebot integration is disabled on this Evolution server.")
        ),
    ):
        await integrations.start_typebot_session(
            chat=PERSON, typebot_url="https://typebot.example.com", typebot_flow="f"
        )
    assert evo.last("POST", _path("/typebot/start")).json["startSession"] is False
    assert evo.last("POST", _path("/typebot/start")).json["variables"] == []


# --- OpenAI credentials ----------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_openai_credentials_never_returns_keys(evo, bound):
    evo.on(
        "GET",
        _path("/openai/creds"),
        json=[
            {"id": "cred-1", "name": "Main", "apiKey": SECRET_KEY, "instanceId": "cuid", "OpenaiAssistant": []},
            {"id": "cred-2", "name": "Backup", "apiKey": "sk-other-secret-key-9999"},
        ],
    )
    with bound(evo):
        text = await integrations.list_openai_credentials()
    assert json.loads(text) == {
        "credentials": [{"credential_id": "cred-1", "name": "Main"}, {"credential_id": "cred-2", "name": "Backup"}]
    }
    assert "sk-" not in text


@pytest.mark.anyio
async def test_list_openai_credentials_empty_and_disabled(evo, bound):
    evo.on("GET", _path("/openai/creds"), json=[])
    with bound(evo):
        assert json.loads(await integrations.list_openai_credentials()) == {"credentials": []}
    evo.on("GET", _path("/openai/creds"), status=400, json={"response": {"message": ["Openai is disabled"]}})
    with bound(evo), pytest.raises(ToolExecutionError, match="The openai integration is disabled"):
        await integrations.list_openai_credentials()


@pytest.mark.anyio
async def test_create_openai_credential(evo, bound):
    evo.on("POST", _path("/openai/creds"), status=201, json={"id": "cred-1", "name": "Main", "apiKey": SECRET_KEY})
    with bound(evo):
        text = await integrations.create_openai_credential(name="Main", api_key=SECRET_KEY)
    assert evo.last("POST", _path("/openai/creds")).json == {"name": "Main", "apiKey": SECRET_KEY}
    assert json.loads(text) == {"credential_id": "cred-1", "name": "Main"}
    assert SECRET_KEY not in text


@pytest.mark.anyio
async def test_create_openai_credential_duplicate_key_is_refused(evo, bound):
    evo.on(
        "POST",
        _path("/openai/creds"),
        status=400,
        json={"response": {"message": ["This API key is already registered. Please use a different API key."]}},
    )
    with bound(evo), pytest.raises(ToolExecutionError, match="This API key is already registered"):
        await integrations.create_openai_credential(name="Main", api_key=SECRET_KEY)


@pytest.mark.anyio
async def test_delete_openai_credential(evo, bound):
    evo.on("DELETE", _path("/openai/creds", "cred-1"), json={"openaiCreds": {"id": "cred-1"}})
    with bound(evo):
        assert json.loads(await integrations.delete_openai_credential(credential_id="cred-1")) == {"deleted": True}


@pytest.mark.anyio
async def test_list_openai_models_sorted_with_and_without_credential(evo, bound):
    evo.on(
        "GET",
        _path("/openai/getModels"),
        json=[{"id": "gpt-4o", "object": "model"}, {"id": "gpt-3.5-turbo", "owned_by": "openai"}],
    )
    with bound(evo):
        default = json.loads(await integrations.list_openai_models())
        assert evo.last("GET", _path("/openai/getModels")).params == {}
        chosen = json.loads(await integrations.list_openai_models(credential_id="cred-1"))
    assert default == {"models": ["gpt-3.5-turbo", "gpt-4o"]}
    assert chosen == default
    assert evo.last("GET", _path("/openai/getModels")).params == {"openaiCredsId": "cred-1"}


# --- Chatwoot --------------------------------------------------------------------------------------------------

CHATWOOT_ROW = {
    "enabled": True,
    "accountId": "7",
    "token": "cw-secret-token",
    "url": "https://chatwoot.example.com",
    "nameInbox": "inst",
    "signMsg": True,
    "signDelimiter": "\n",
    "reopenConversation": True,
    "conversationPending": False,
    "mergeBrazilContacts": False,
    "importContacts": False,
    "importMessages": True,
    "daysLimitImportMessages": 5,
    "organization": None,
    "logo": None,
    "ignoreJids": ["@g.us"],
    "webhook_url": "http://elsewhere.example/chatwoot/webhook/inst",
}


@pytest.mark.anyio
async def test_get_chatwoot_config_derives_the_webhook_url_and_hides_the_token(evo, bound):
    evo.on("GET", _path("/chatwoot/find"), json=CHATWOOT_ROW)
    with bound(evo):
        text = await integrations.get_chatwoot_config()
    assert json.loads(text) == {
        "enabled": True,
        "url": "https://chatwoot.example.com",
        "account_id": "7",
        "inbox_name": "inst",
        "sign_messages": True,
        "reopen_conversation": True,
        "conversation_pending": False,
        "import_contacts": False,
        "import_messages": True,
        "days_limit_import_messages": 5,
        "ignored_chats": ["@g.us"],
        "token_set": True,
        "webhook_url": f"{BASE}/chatwoot/webhook/{INSTANCE}",
    }
    assert "cw-secret-token" not in text


@pytest.mark.anyio
async def test_get_chatwoot_config_quotes_the_instance_name_in_the_webhook_url(evo, bound):
    evo.on("GET", "/chatwoot/find/my inst", json=CHATWOOT_ROW)
    with bound(evo, identity=InstanceIdentity(name="my inst", integration=BAILEYS)):
        result = json.loads(await integrations.get_chatwoot_config())
    assert result["webhook_url"] == f"{BASE}/chatwoot/webhook/my%20inst"


@pytest.mark.anyio
async def test_get_chatwoot_config_when_not_configured(evo, bound):
    evo.on(
        "GET",
        _path("/chatwoot/find"),
        json={
            "enabled": False,
            "url": "",
            "accountId": "",
            "token": "",
            "signMsg": False,
            "nameInbox": "",
            "webhook_url": "",
        },
    )
    with bound(evo):
        result = json.loads(await integrations.get_chatwoot_config())
    assert result == {"configured": False, "enabled": False, "webhook_url": f"{BASE}/chatwoot/webhook/{INSTANCE}"}


@pytest.mark.anyio
async def test_set_chatwoot_config_sends_the_full_body_and_hides_the_token(evo, bound):
    evo.on("POST", _path("/chatwoot/set"), status=201, json={**CHATWOOT_ROW, "autoCreate": True})
    with bound(evo):
        text = await integrations.set_chatwoot_config(
            enabled=True,
            account_id="7",
            url="https://chatwoot.example.com",
            token="cw-secret-token",
            sign_messages=True,
            import_messages=True,
            days_limit_import_messages=5,
            inbox_name="Support",
            organization="Acme",
            ignored_chats=["@g.us", "+39 333 123 4567"],
        )
    assert evo.last("POST", _path("/chatwoot/set")).json == {
        "enabled": True,
        "accountId": "7",
        "token": "cw-secret-token",
        "url": "https://chatwoot.example.com",
        "signMsg": True,
        "reopenConversation": True,
        "conversationPending": False,
        "importContacts": False,
        "importMessages": True,
        "daysLimitImportMessages": 5,
        "autoCreate": True,
        "nameInbox": "Support",
        "organization": "Acme",
        "ignoreJids": ["@g.us", PERSON],
    }
    result = json.loads(text)
    assert result["token_set"] is True
    assert result["auto_create"] is True
    assert result["webhook_url"] == f"{BASE}/chatwoot/webhook/{INSTANCE}"
    assert "cw-secret-token" not in text
    assert [r.method for r in evo.requests] == ["POST"]


@pytest.mark.anyio
async def test_set_chatwoot_config_reuses_the_stored_token(evo, bound):
    evo.on("GET", _path("/chatwoot/find"), json=CHATWOOT_ROW)
    evo.on("POST", _path("/chatwoot/set"), status=201, json=CHATWOOT_ROW)
    with bound(evo):
        await integrations.set_chatwoot_config(enabled=False, account_id="7", url="https://chatwoot.example.com")
    body = evo.last("POST", _path("/chatwoot/set")).json
    assert body["token"] == "cw-secret-token"
    assert body["enabled"] is False
    assert body["daysLimitImportMessages"] == 3
    assert body["autoCreate"] is True
    assert body["reopenConversation"] is True
    assert "nameInbox" not in body
    assert "ignoreJids" not in body


@pytest.mark.anyio
async def test_set_chatwoot_config_needs_a_token_when_none_is_stored(evo, bound):
    evo.on("GET", _path("/chatwoot/find"), json={"enabled": False, "url": "", "accountId": "", "token": ""})
    with bound(evo), pytest.raises(ToolExecutionError, match="token is required: no Chatwoot token is stored"):
        await integrations.set_chatwoot_config(enabled=True, account_id="7", url="https://chatwoot.example.com")
    assert [r.method for r in evo.requests] == ["GET"]


@pytest.mark.anyio
async def test_set_chatwoot_config_reports_a_disabled_chatwoot_module_as_refused(evo, bound):
    evo.on("POST", _path("/chatwoot/set"), status=400, json={"response": {"message": ["Chatwoot is disabled"]}})
    with (
        bound(evo),
        pytest.raises(ToolExecutionError, match="Evolution refused the request \\(400\\): Chatwoot is disabled"),
    ):
        await integrations.set_chatwoot_config(
            enabled=True, account_id="7", url="https://chatwoot.example.com", token="cw-secret-token"
        )
