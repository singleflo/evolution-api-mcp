"""Integrations toolset: Evolution chatbots, their sessions and defaults, OpenAI credentials and Chatwoot.

Chatbot secrets (bot API keys, n8n basic-auth passwords, OpenAI keys, the Chatwoot token) are input only. They are
sent to Evolution and reused on updates, and they leave this module only as `*_set` booleans.
"""

from datetime import datetime, timezone
from typing import Annotated, Any, Literal
from urllib.parse import quote

from pydantic import Field

from evolution_api_mcp import calls, clock, context, messages, registry
from evolution_api_mcp.client import EvolutionClient, EvolutionHTTPError
from evolution_api_mcp.context import InstanceIdentity
from evolution_api_mcp.errors import ToolExecutionError, tool_result
from evolution_api_mcp.redact import redact
from evolution_api_mcp.tools import Chat

ChatbotProvider = Literal["evolution_bot", "typebot", "openai", "dify", "flowise", "n8n", "evoai"]

PROVIDER_PATH = {
    "evolution_bot": "evolutionBot",
    "typebot": "typebot",
    "openai": "openai",
    "dify": "dify",
    "flowise": "flowise",
    "n8n": "n8n",
    "evoai": "evoai",
}
ENDPOINT_FIELD = {
    "typebot": "url",
    "evolution_bot": "apiUrl",
    "flowise": "apiUrl",
    "dify": "apiUrl",
    "n8n": "webhookUrl",
    "evoai": "agentUrl",
}
# Evolution stores each provider's fallback bot under its own settings column (prisma schema, *Setting models).
FALLBACK_FIELD = {
    "typebot": "typebotIdFallback",
    "dify": "difyIdFallback",
    "flowise": "flowiseIdFallback",
    "openai": "openaiIdFallback",
    "evolution_bot": "botIdFallback",
    "n8n": "n8nIdFallback",
    "evoai": "evoaiIdFallback",
}
# Evolution field (camelCase) -> parameter and result key (snake_case).
BOT_FIELDS = {
    "enabled": "enabled",
    "description": "description",
    "triggerType": "trigger_type",
    "triggerOperator": "trigger_operator",
    "triggerValue": "trigger_value",
    "expire": "expire_minutes",
    "keywordFinish": "keyword_finish",
    "delayMessage": "delay_message_ms",
    "unknownMessage": "unknown_message",
    "listeningFromMe": "listening_from_me",
    "stopBotFromMe": "stop_bot_from_me",
    "keepOpen": "keep_open",
    "debounceTime": "debounce_seconds",
    "ignoreJids": "ignored_chats",
    "splitMessages": "split_messages",
    "timePerChar": "time_per_char_ms",
    "typebot": "typebot_flow",
    "botType": "bot_type",
    "openaiCredsId": "openai_credential_id",
    "assistantId": "assistant_id",
    "functionUrl": "function_url",
    "model": "model",
    "systemMessages": "system_messages",
    "maxTokens": "max_tokens",
    "basicAuthUser": "basic_auth_user",
}
_SNAKE_TO_CAMEL = {snake: camel for camel, snake in BOT_FIELDS.items()}

_COMMON_FIELDS = frozenset(
    {
        "enabled",
        "description",
        "triggerType",
        "triggerOperator",
        "triggerValue",
        "expire",
        "keywordFinish",
        "delayMessage",
        "unknownMessage",
        "listeningFromMe",
        "stopBotFromMe",
        "keepOpen",
        "debounceTime",
        "ignoreJids",
    }
)
_SPLIT_FIELDS = frozenset({"splitMessages", "timePerChar"})
# Every field a provider's create/update accepts (the DTOs and validate schemas under
# src/api/integrations/chatbot/<provider>). `apiKey` and `basicAuthPass` are secrets.
_PROVIDER_FIELDS = {
    "typebot": _COMMON_FIELDS | {"url", "typebot"},
    "evolution_bot": _COMMON_FIELDS | _SPLIT_FIELDS | {"apiUrl", "apiKey"},
    "dify": _COMMON_FIELDS | _SPLIT_FIELDS | {"botType", "apiUrl", "apiKey"},
    "flowise": _COMMON_FIELDS | _SPLIT_FIELDS | {"apiUrl", "apiKey"},
    "n8n": _COMMON_FIELDS | _SPLIT_FIELDS | {"webhookUrl", "basicAuthUser", "basicAuthPass"},
    "evoai": _COMMON_FIELDS | _SPLIT_FIELDS | {"agentUrl", "apiKey"},
    "openai": _COMMON_FIELDS
    | {"openaiCredsId", "botType", "assistantId", "functionUrl", "model", "systemMessages", "maxTokens"},
}
_BOT_TYPES = {
    "dify": ("chatBot", "textGenerator", "agent", "workflow"),
    "openai": ("assistant", "chatCompletion"),
}
_BOT_TYPE_REFUSAL = (
    "bot_type applies to dify (chatBot, textGenerator, agent, workflow) and openai (assistant, chatCompletion) only."
)

_SETTINGS_FIELDS = {
    "expire": "expire_minutes",
    "keywordFinish": "keyword_finish",
    "delayMessage": "delay_message_ms",
    "unknownMessage": "unknown_message",
    "listeningFromMe": "listening_from_me",
    "stopBotFromMe": "stop_bot_from_me",
    "keepOpen": "keep_open",
    "debounceTime": "debounce_seconds",
    "ignoreJids": "ignored_chats",
    "splitMessages": "split_messages",
    "timePerChar": "time_per_char_ms",
}
_SETTINGS_DEFAULTS: dict[str, Any] = {
    "expire": 0,
    "keywordFinish": "",
    "delayMessage": 1000,
    "unknownMessage": "",
    "listeningFromMe": False,
    "stopBotFromMe": False,
    "keepOpen": False,
    "debounceTime": 0,
    "ignoreJids": [],
    "splitMessages": False,
    "timePerChar": 0,
}
_BASE_SETTINGS = (
    "expire",
    "keywordFinish",
    "delayMessage",
    "unknownMessage",
    "listeningFromMe",
    "stopBotFromMe",
    "keepOpen",
    "debounceTime",
    "ignoreJids",
)
# The keys each provider's settings schema declares (validate/*.schema.ts); the fallback bot travels as `fallbackId`.
_SETTINGS_KEYS = {
    "typebot": _BASE_SETTINGS,
    "flowise": _BASE_SETTINGS,
    "openai": _BASE_SETTINGS,
    "evolution_bot": (*_BASE_SETTINGS, "splitMessages", "timePerChar"),
    "dify": (*_BASE_SETTINGS, "splitMessages", "timePerChar"),
    "n8n": (*_BASE_SETTINGS, "splitMessages", "timePerChar"),
    "evoai": (*_BASE_SETTINGS, "splitMessages", "timePerChar"),
}
_BRIEF_KEYS = (
    "enabled",
    "description",
    "trigger_type",
    "trigger_operator",
    "trigger_value",
    "endpoint_url",
    "bot_type",
    "model",
)
_WILDCARD_JIDS = ("@g.us", "@s.whatsapp.net")

Provider = Annotated[
    ChatbotProvider,
    Field(description="Chatbot integration: evolution_bot, typebot, openai, dify, flowise, n8n or evoai."),
]
BotId = Annotated[str, Field(min_length=1, max_length=128, description="Chatbot id from list_chatbots.")]
OptionalChat = Annotated[
    str | None,
    Field(
        min_length=3,
        max_length=128,
        description="Only sessions of this chat: phone number, chat_id or chat name from list_chats.",
    ),
]
_Enabled = Annotated[bool | None, Field(description="Whether the chatbot answers messages.")]
_Description = Annotated[str | None, Field(min_length=1, max_length=255, description="Name of the chatbot.")]
_TriggerType = Annotated[
    Literal["all", "keyword", "none", "advanced"] | None,
    Field(
        description=(
            "When the chatbot starts: all (every chat; only one enabled bot may use it), keyword (needs "
            "trigger_operator and trigger_value), advanced (regular expression in trigger_value) or none."
        )
    ),
]
_TriggerOperator = Annotated[
    Literal["equals", "contains", "startsWith", "endsWith", "regex"] | None,
    Field(description="How trigger_value is compared with the incoming text. Needed for the keyword trigger."),
]
_TriggerValue = Annotated[
    str | None,
    Field(min_length=1, max_length=255, description="Keyword or expression that starts the chatbot."),
]
_EndpointUrl = Annotated[
    str | None,
    Field(
        min_length=10,
        max_length=500,
        pattern=r"^https?://",
        description=(
            "Where the chatbot runs: the Typebot server URL, the Evolution Bot / Dify / Flowise API URL, the n8n "
            "webhook URL or the EvoAI agent URL. Not used by openai."
        ),
    ),
]
_ApiKey = Annotated[
    str | None,
    Field(
        min_length=1,
        max_length=500,
        description=(
            "API key of the chatbot service (evolution_bot, dify, flowise, evoai). It is stored by Evolution and never "
            "returned."
        ),
    ),
]
_TypebotFlow = Annotated[
    str | None,
    Field(min_length=1, max_length=100, description="Public id of the Typebot flow. typebot only."),
]
_BotType = Annotated[
    Literal["chatBot", "textGenerator", "agent", "workflow", "assistant", "chatCompletion"] | None,
    Field(
        description=(
            "dify: chatBot, textGenerator, agent or workflow. openai: assistant or chatCompletion. Other providers "
            "have no bot type."
        )
    ),
]
_OpenaiCredentialId = Annotated[
    str | None,
    Field(min_length=1, max_length=100, description="OpenAI credential id from list_openai_credentials."),
]
_AssistantId = Annotated[
    str | None,
    Field(min_length=1, max_length=100, description="OpenAI assistant id. openai with bot_type assistant."),
]
_FunctionUrl = Annotated[
    str | None,
    Field(
        min_length=10,
        max_length=500,
        pattern=r"^https?://",
        description="URL Evolution calls for OpenAI function calling. openai only.",
    ),
]
_Model = Annotated[
    str | None,
    Field(min_length=1, max_length=100, description="OpenAI model id (list_openai_models). openai chatCompletion."),
]
_SystemMessages = Annotated[
    list[str] | None,
    Field(max_length=20, description="System prompts of an OpenAI chatCompletion bot."),
]
_MaxTokens = Annotated[
    int | None,
    Field(ge=1, le=200000, description="Reply token limit of an OpenAI chatCompletion bot."),
]
_BasicAuthUser = Annotated[
    str | None,
    Field(min_length=1, max_length=200, description="Basic-auth user of the n8n webhook. n8n only."),
]
_BasicAuthPassword = Annotated[
    str | None,
    Field(
        min_length=1,
        max_length=500,
        description="Basic-auth password of the n8n webhook. It is stored by Evolution and never returned.",
    ),
]
_Expire = Annotated[
    int | None,
    Field(ge=0, le=100000, description="Minutes of inactivity after which a session ends; 0 keeps it open."),
]
_KeywordFinish = Annotated[
    str | None,
    Field(min_length=1, max_length=100, description="Message text from the user that ends the session."),
]
_DelayMessage = Annotated[
    int | None,
    Field(ge=0, le=60000, description="Milliseconds the bot waits before each reply."),
]
_UnknownMessage = Annotated[
    str | None,
    Field(min_length=1, max_length=100, description="Reply sent when the bot does not understand a message."),
]
_ListeningFromMe = Annotated[
    bool | None,
    Field(description="Whether the bot also reacts to messages sent from this number."),
]
_StopBotFromMe = Annotated[
    bool | None,
    Field(description="Whether a message sent from this number pauses the bot's session in that chat."),
]
_KeepOpen = Annotated[
    bool | None,
    Field(description="Whether closed sessions are kept (status closed) instead of deleted."),
]
_DebounceTime = Annotated[
    int | None,
    Field(ge=0, le=3600, description="Seconds to wait so several quick messages are answered as one."),
]
_IgnoredChats = Annotated[
    list[str] | None,
    Field(
        max_length=200,
        description=(
            "Chats the bot skips: phone numbers or chat_ids from list_chats. The wildcards @g.us (every group) and "
            "@s.whatsapp.net (every person) are accepted as they are."
        ),
    ),
]
_SplitMessages = Annotated[
    bool | None,
    Field(description="Whether long replies are split into several messages. Not used by typebot and openai."),
]
_TimePerChar = Annotated[
    int | None,
    Field(ge=0, le=5000, description="Milliseconds of typing time per character. Not used by typebot and openai."),
]
_FallbackBotId = Annotated[
    str | None,
    Field(min_length=1, max_length=100, description="Chatbot id that answers when no trigger matches."),
]
_SpeechToText = Annotated[
    bool | None,
    Field(description="Whether voice notes are transcribed before the OpenAI bot answers. openai only."),
]


class _ProviderDisabled(Exception):
    """A provider's route answered that the integration is disabled on the Evolution server."""


def _is_disabled(exc: EvolutionHTTPError) -> bool:
    return exc.status == 400 and exc.message.rstrip(" .").endswith("is disabled")


def _disabled_message(provider: str) -> str:
    return f"The {provider} integration is disabled on this Evolution server."


def _refuse_disabled(provider: str):
    def hook(exc: EvolutionHTTPError) -> None:
        if _is_disabled(exc):
            raise ToolExecutionError(_disabled_message(provider))

    return hook


def _skip_disabled(exc: EvolutionHTTPError) -> None:
    if _is_disabled(exc):
        raise _ProviderDisabled


async def _provider_call(
    client: EvolutionClient,
    identity: InstanceIdentity,
    provider: str,
    method: Literal["GET", "POST", "PUT", "DELETE"],
    action: str,
    *segments: str,
    json: object | None = None,
    params: dict[str, str] | None = None,
    write: bool = False,
) -> object:
    return await calls.call(
        client,
        identity,
        method,
        f"{PROVIDER_PATH[provider]}/{action}",
        *segments,
        json=json,
        params=params,
        write=write,
        on_http_error=_refuse_disabled(provider),
    )


def _omit_none(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def _redacted(row: dict[str, Any]) -> dict[str, Any]:
    """A copy of a configuration row with every secret-named value replaced by the redaction marker."""
    safe = redact(row)
    return safe if isinstance(safe, dict) else {}


def _iso_date(value: object) -> str | None:
    """ISO-8601 time (display zone) of a JSON date (`2026-09-29T21:04:05.123Z`) or a unix timestamp."""
    if not isinstance(value, str):
        return messages.iso(value)
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return clock.fmt(moment)


def _ignored_jids(values: list[str] | None) -> list[str] | None:
    """Chat ids for an ignore list; the two wildcards Evolution understands pass through."""
    if values is None:
        return None
    result: list[str] = []
    for value in values:
        jid_value = value.strip() if value.strip() in _WILDCARD_JIDS else calls.parse_chat(value)
        if jid_value not in result:
            result.append(jid_value)
    return result


def _check_bot_type(provider: str, bot_type: str | None) -> None:
    if bot_type is None:
        return
    if bot_type not in _BOT_TYPES.get(provider, ()):
        raise ToolExecutionError(_BOT_TYPE_REFUSAL)


def _check_trigger(trigger_type: object, operator: object, value: object) -> None:
    if trigger_type == "keyword" and not (operator and value):
        raise ToolExecutionError("trigger_type 'keyword' needs trigger_operator and trigger_value.")
    if trigger_type == "advanced" and not value:
        raise ToolExecutionError("trigger_type 'advanced' needs trigger_value.")


def _to_body(provider: str, values: dict[str, Any]) -> dict[str, Any]:
    """The camelCase body Evolution expects for the provided snake_case values the provider accepts."""
    allowed = _PROVIDER_FIELDS[provider]
    body: dict[str, Any] = {}
    for snake, value in values.items():
        if value is None:
            continue
        if snake == "endpoint_url":
            camel = ENDPOINT_FIELD.get(provider)
        elif snake == "api_key":
            camel = "apiKey"
        elif snake == "basic_auth_password":
            camel = "basicAuthPass"
        else:
            camel = _SNAKE_TO_CAMEL[snake]
        if camel in allowed:
            body[camel] = value
    return body


def _bot_view(provider: str, answer: object, *, brief: bool = False) -> dict[str, Any]:
    """Project a chatbot row: snake_case keys, no ids other than bot_id, secrets only as *_set flags."""
    raw = answer if isinstance(answer, dict) else {}
    safe = _redacted(raw)
    result: dict[str, Any] = {"provider": provider, "bot_id": safe.get("id")}
    for camel, snake in BOT_FIELDS.items():
        if safe.get(camel) is not None:
            result[snake] = safe[camel]
    endpoint = ENDPOINT_FIELD.get(provider)
    if endpoint and safe.get(endpoint) is not None:
        result["endpoint_url"] = safe[endpoint]
    if brief:
        return {key: value for key, value in result.items() if key in ("provider", "bot_id", *_BRIEF_KEYS)}
    if "apiKey" in raw:
        result["api_key_set"] = bool(raw["apiKey"])
    if "basicAuthPass" in raw:
        result["basic_auth_password_set"] = bool(raw["basicAuthPass"])
    return result


def _settings_view(provider: str, answer: object) -> dict[str, Any]:
    raw = answer if isinstance(answer, dict) else {}
    safe = _redacted(raw)
    result: dict[str, Any] = {"provider": provider}
    for camel in _SETTINGS_KEYS[provider]:
        if safe.get(camel) is not None:
            result[_SETTINGS_FIELDS[camel]] = safe[camel]
    fallback = safe.get("fallbackId") or safe.get(FALLBACK_FIELD[provider])
    if fallback:
        result["fallback_bot_id"] = fallback
    if provider == "openai":
        if safe.get("openaiCredsId"):
            result["openai_credential_id"] = safe["openaiCredsId"]
        if safe.get("speechToText") is not None:
            result["speech_to_text"] = safe["speechToText"]
    return result


async def _fetch_bot(client: EvolutionClient, identity: InstanceIdentity, provider: str, bot_id: str) -> dict:
    row = await _provider_call(client, identity, provider, "GET", "fetch", bot_id)
    if not isinstance(row, dict) or not row:
        raise ToolExecutionError(
            f"Chatbot {bot_id} was not found for provider {provider}. Use list_chatbots to find its id."
        )
    return row


# --- chatbots ----------------------------------------------------------------------------------------------------


@registry.tool(
    title="List chatbots",
    toolset="integrations",
    kind="read",
    idempotent=True,
    integrations=registry.ALL,
)
async def list_chatbots(
    provider: Annotated[
        ChatbotProvider | None,
        Field(description="Only this integration. Omit to list the chatbots of every integration."),
    ] = None,
) -> str:
    """List the chatbots configured on this Evolution instance, across all integrations or one.

    The chatbot integrations are Evolution Bot, Typebot, OpenAI, Dify, Flowise, n8n and EvoAI. Each row carries the
    provider, the bot_id used by get_chatbot and the other chatbot tools, whether it is enabled, its trigger and where
    it runs. Integrations that the Evolution server has switched off are named in disabled_providers instead of
    failing the call. API keys and passwords are never returned. Use get_chatbot for one bot's full configuration.
    """
    conn, client = await context.resolve()
    selected = [provider] if provider else list(PROVIDER_PATH)
    chatbots: list[dict[str, Any]] = []
    disabled: list[str] = []
    for name in selected:
        try:
            rows = await calls.call(
                client, conn.identity, "GET", f"{PROVIDER_PATH[name]}/find", on_http_error=_skip_disabled
            )
        except _ProviderDisabled:
            disabled.append(name)
            continue
        if isinstance(rows, list):
            chatbots.extend(_bot_view(name, row, brief=True) for row in rows if isinstance(row, dict))
    return tool_result({"chatbots": chatbots, "disabled_providers": disabled})


@registry.tool(
    title="Get chatbot",
    toolset="integrations",
    kind="read",
    idempotent=True,
    integrations=registry.ALL,
)
async def get_chatbot(provider: Provider, bot_id: BotId) -> str:
    """Return the full configuration of one chatbot: trigger, behaviour, endpoint and provider-specific fields.

    Use list_chatbots to find the bot_id. The result shows api_key_set or basic_auth_password_set instead of the
    secrets themselves, so a key can be checked but never read. Session defaults shared by all bots of a provider are
    in get_chatbot_settings.
    """
    conn, client = await context.resolve()
    row = await _fetch_bot(client, conn.identity, provider, bot_id)
    return tool_result(_bot_view(provider, row))


@registry.tool(
    title="Create chatbot",
    toolset="integrations",
    kind="destructive",
    idempotent=False,
    integrations=registry.ALL,
    local_only=True,
)
async def create_chatbot(
    provider: Provider,
    description: Annotated[str, Field(min_length=1, max_length=255, description="Name of the chatbot.")],
    trigger_type: Annotated[
        Literal["all", "keyword", "none", "advanced"],
        Field(
            description=(
                "When the chatbot starts: all (every chat; only one enabled bot may use it), keyword (needs "
                "trigger_operator and trigger_value), advanced (regular expression in trigger_value) or none."
            )
        ),
    ],
    trigger_operator: _TriggerOperator = None,
    trigger_value: _TriggerValue = None,
    enabled: Annotated[bool, Field(description="Whether the chatbot answers messages.")] = True,
    endpoint_url: _EndpointUrl = None,
    api_key: _ApiKey = None,
    typebot_flow: _TypebotFlow = None,
    bot_type: _BotType = None,
    openai_credential_id: _OpenaiCredentialId = None,
    assistant_id: _AssistantId = None,
    function_url: _FunctionUrl = None,
    model: _Model = None,
    system_messages: _SystemMessages = None,
    max_tokens: _MaxTokens = None,
    basic_auth_user: _BasicAuthUser = None,
    basic_auth_password: _BasicAuthPassword = None,
    expire_minutes: _Expire = None,
    keyword_finish: _KeywordFinish = None,
    delay_message_ms: _DelayMessage = None,
    unknown_message: _UnknownMessage = None,
    listening_from_me: _ListeningFromMe = None,
    stop_bot_from_me: _StopBotFromMe = None,
    keep_open: _KeepOpen = None,
    debounce_seconds: _DebounceTime = None,
    ignored_chats: _IgnoredChats = None,
    split_messages: _SplitMessages = None,
    time_per_char_ms: _TimePerChar = None,
) -> str:
    """Create a chatbot that answers incoming WhatsApp messages through Evolution Bot, Typebot, OpenAI, Dify, etc.

    Once enabled, the bot replies on its own to messages from other people in the chats its trigger matches, using
    this WhatsApp number; those replies are real messages. Each provider needs its own fields: typebot endpoint_url
    and typebot_flow; evolution_bot, flowise, evoai and n8n endpoint_url; dify bot_type (chatBot, textGenerator, agent
    or workflow); openai openai_credential_id and bot_type (assistant with assistant_id, or chatCompletion with model
    and max_tokens). Fields that do not apply to the provider are ignored, and unset behaviour fields take Evolution's
    defaults. Secrets (api_key, basic_auth_password) are stored by Evolution and never returned, so this tool is
    available on the local server only. Returns the new chatbot with its bot_id. update_chatbot changes it later and
    delete_chatbot removes it.
    """
    conn, client = await context.resolve()
    values = dict(
        description=description,
        trigger_type=trigger_type,
        trigger_operator=trigger_operator,
        trigger_value=trigger_value,
        enabled=enabled,
        endpoint_url=endpoint_url,
        api_key=api_key,
        typebot_flow=typebot_flow,
        bot_type=bot_type,
        openai_credential_id=openai_credential_id,
        assistant_id=assistant_id,
        function_url=function_url,
        model=model,
        system_messages=system_messages,
        max_tokens=max_tokens,
        basic_auth_user=basic_auth_user,
        basic_auth_password=basic_auth_password,
        expire_minutes=expire_minutes,
        keyword_finish=keyword_finish,
        delay_message_ms=delay_message_ms,
        unknown_message=unknown_message,
        listening_from_me=listening_from_me,
        stop_bot_from_me=stop_bot_from_me,
        keep_open=keep_open,
        debounce_seconds=debounce_seconds,
        ignored_chats=_ignored_jids(ignored_chats),
        split_messages=split_messages,
        time_per_char_ms=time_per_char_ms,
    )
    _check_bot_type(provider, bot_type)
    missing = _missing_for_create(provider, values)
    if missing:
        raise ToolExecutionError(f"A {provider} chatbot needs {', '.join(missing)}. Nothing was changed.")
    _check_trigger(trigger_type, trigger_operator, trigger_value)
    answer = await _provider_call(
        client, conn.identity, provider, "POST", "create", json=_to_body(provider, values), write=True
    )
    return tool_result(_bot_view(provider, answer))


def _missing_for_create(provider: str, values: dict[str, Any]) -> list[str]:
    if provider == "typebot":
        needed = ["endpoint_url", "typebot_flow"]
    elif provider in ("evolution_bot", "flowise", "evoai", "n8n"):
        needed = ["endpoint_url"]
    elif provider == "dify":
        needed = ["bot_type"]
    else:
        needed = ["openai_credential_id", "bot_type"]
        if values.get("bot_type") == "assistant":
            needed.append("assistant_id")
        elif values.get("bot_type") == "chatCompletion":
            needed.extend(["model", "max_tokens"])
    return [name for name in needed if values.get(name) in (None, "")]


@registry.tool(
    title="Update chatbot",
    toolset="integrations",
    kind="destructive",
    idempotent=True,
    integrations=registry.ALL,
    local_only=True,
)
async def update_chatbot(
    provider: Provider,
    bot_id: BotId,
    description: _Description = None,
    trigger_type: _TriggerType = None,
    trigger_operator: _TriggerOperator = None,
    trigger_value: _TriggerValue = None,
    enabled: _Enabled = None,
    endpoint_url: _EndpointUrl = None,
    api_key: _ApiKey = None,
    typebot_flow: _TypebotFlow = None,
    bot_type: _BotType = None,
    openai_credential_id: _OpenaiCredentialId = None,
    assistant_id: _AssistantId = None,
    function_url: _FunctionUrl = None,
    model: _Model = None,
    system_messages: _SystemMessages = None,
    max_tokens: _MaxTokens = None,
    basic_auth_user: _BasicAuthUser = None,
    basic_auth_password: _BasicAuthPassword = None,
    expire_minutes: _Expire = None,
    keyword_finish: _KeywordFinish = None,
    delay_message_ms: _DelayMessage = None,
    unknown_message: _UnknownMessage = None,
    listening_from_me: _ListeningFromMe = None,
    stop_bot_from_me: _StopBotFromMe = None,
    keep_open: _KeepOpen = None,
    debounce_seconds: _DebounceTime = None,
    ignored_chats: _IgnoredChats = None,
    split_messages: _SplitMessages = None,
    time_per_char_ms: _TimePerChar = None,
) -> str:
    """Change fields of an existing chatbot; every field left out keeps its stored value, secrets included.

    The tool reads the bot, overlays the given fields and saves the merged configuration, so enabling or disabling a
    bot only needs enabled. Changing the trigger, endpoint or prompts changes how the bot answers people in
    their chats from this WhatsApp number, which is why the tool is available on the local server only. Fields that do
    not apply to the provider are ignored. Returns the updated chatbot without secrets. Use get_chatbot to review a
    bot first, and create_chatbot for a new one.
    """
    conn, client = await context.resolve()
    values = dict(
        description=description,
        trigger_type=trigger_type,
        trigger_operator=trigger_operator,
        trigger_value=trigger_value,
        enabled=enabled,
        endpoint_url=endpoint_url,
        api_key=api_key,
        typebot_flow=typebot_flow,
        bot_type=bot_type,
        openai_credential_id=openai_credential_id,
        assistant_id=assistant_id,
        function_url=function_url,
        model=model,
        system_messages=system_messages,
        max_tokens=max_tokens,
        basic_auth_user=basic_auth_user,
        basic_auth_password=basic_auth_password,
        expire_minutes=expire_minutes,
        keyword_finish=keyword_finish,
        delay_message_ms=delay_message_ms,
        unknown_message=unknown_message,
        listening_from_me=listening_from_me,
        stop_bot_from_me=stop_bot_from_me,
        keep_open=keep_open,
        debounce_seconds=debounce_seconds,
        ignored_chats=_ignored_jids(ignored_chats),
        split_messages=split_messages,
        time_per_char_ms=time_per_char_ms,
    )
    _check_bot_type(provider, bot_type)
    current = await _fetch_bot(client, conn.identity, provider, bot_id)
    allowed = _PROVIDER_FIELDS[provider]
    body = {key: value for key, value in current.items() if key in allowed and value is not None}
    body.update(_to_body(provider, values))
    _check_trigger(body.get("triggerType"), body.get("triggerOperator"), body.get("triggerValue"))
    answer = await _provider_call(client, conn.identity, provider, "PUT", "update", bot_id, json=body, write=True)
    return tool_result(_bot_view(provider, answer))


@registry.tool(
    title="Delete chatbot",
    toolset="integrations",
    kind="irreversible",
    idempotent=True,
    integrations=registry.ALL,
)
async def delete_chatbot(provider: Provider, bot_id: BotId) -> str:
    """Delete a chatbot together with all of its sessions; this cannot be undone from this server.

    People in chats the bot was handling stop receiving its replies. The configuration is not recoverable, so
    update_chatbot with enabled=false is the reversible way to stop a bot. Returns the provider and the deleted bot_id.
    """
    conn, client = await context.resolve()
    await _provider_call(client, conn.identity, provider, "DELETE", "delete", bot_id, write=True)
    return tool_result({"deleted": True, "provider": provider, "bot_id": bot_id})


# --- chatbot defaults and sessions -------------------------------------------------------------------------------


@registry.tool(
    title="Get chatbot defaults",
    toolset="integrations",
    kind="read",
    idempotent=True,
    integrations=registry.ALL,
)
async def get_chatbot_settings(provider: Provider) -> str:
    """Return the session defaults shared by all chatbots of one integration.

    The defaults cover session expiry, the finish keyword, reply delay, the unknown-message reply, whether the bot
    reacts to this number's own messages, debounce, the ignored chats and the fallback bot that answers when no trigger
    matches. OpenAI adds the default credential and speech-to-text. A bot's own fields override these defaults; they
    are shown by get_chatbot. update_chatbot_settings changes the defaults.
    """
    conn, client = await context.resolve()
    row = await _provider_call(client, conn.identity, provider, "GET", "fetchSettings")
    return tool_result(_settings_view(provider, row))


@registry.tool(
    title="Update chatbot defaults",
    toolset="integrations",
    kind="destructive",
    idempotent=True,
    integrations=registry.ALL,
)
async def update_chatbot_settings(
    provider: Provider,
    expire_minutes: _Expire = None,
    keyword_finish: _KeywordFinish = None,
    delay_message_ms: _DelayMessage = None,
    unknown_message: _UnknownMessage = None,
    listening_from_me: _ListeningFromMe = None,
    stop_bot_from_me: _StopBotFromMe = None,
    keep_open: _KeepOpen = None,
    debounce_seconds: _DebounceTime = None,
    ignored_chats: _IgnoredChats = None,
    split_messages: _SplitMessages = None,
    time_per_char_ms: _TimePerChar = None,
    fallback_bot_id: _FallbackBotId = None,
    speech_to_text: _SpeechToText = None,
    openai_credential_id: _OpenaiCredentialId = None,
) -> str:
    """Change the session defaults shared by all chatbots of one integration; at least one value is required.

    The tool reads the current defaults, overlays the given values and saves them all, so untouched values keep their
    setting. The defaults apply to every bot of the provider that does not set its own value, and they decide how the
    bots answer people from this WhatsApp number. fallback_bot_id names the bot that answers when no trigger matches
    and cannot be cleared through Evolution. split_messages and time_per_char_ms do not apply to typebot, flowise and
    openai; speech_to_text and openai_credential_id apply to openai only. Returns the saved defaults.
    """
    conn, client = await context.resolve()
    given = {
        "expire": expire_minutes,
        "keywordFinish": keyword_finish,
        "delayMessage": delay_message_ms,
        "unknownMessage": unknown_message,
        "listeningFromMe": listening_from_me,
        "stopBotFromMe": stop_bot_from_me,
        "keepOpen": keep_open,
        "debounceTime": debounce_seconds,
        "ignoreJids": _ignored_jids(ignored_chats),
        "splitMessages": split_messages,
        "timePerChar": time_per_char_ms,
    }
    extras = {
        "fallbackId": fallback_bot_id,
        "speechToText": speech_to_text,
        "openaiCredsId": openai_credential_id,
    }
    if all(value is None for value in (*given.values(), *extras.values())):
        raise ToolExecutionError("Give at least one setting to change. Nothing was changed.")
    current = await _provider_call(client, conn.identity, provider, "GET", "fetchSettings")
    current = current if isinstance(current, dict) else {}
    body: dict[str, Any] = {}
    for key in _SETTINGS_KEYS[provider]:
        if given[key] is not None:
            body[key] = given[key]
        elif current.get(key) is not None:
            body[key] = current[key]
        else:
            body[key] = _SETTINGS_DEFAULTS[key]
    if extras["fallbackId"] is not None:
        body["fallbackId"] = extras["fallbackId"]
    if provider == "openai":
        credential = extras["openaiCredsId"] or current.get("openaiCredsId")
        if not credential:
            raise ToolExecutionError("openai_credential_id is required for OpenAI chatbot defaults.")
        body["openaiCredsId"] = credential
        speech = extras["speechToText"] if extras["speechToText"] is not None else current.get("speechToText")
        if speech is not None:
            body["speechToText"] = speech
    answer = await _provider_call(client, conn.identity, provider, "POST", "settings", json=body, write=True)
    return tool_result(_settings_view(provider, answer))


@registry.tool(
    title="List chatbot sessions",
    toolset="integrations",
    kind="read",
    idempotent=True,
    integrations=registry.ALL,
)
async def list_chatbot_sessions(provider: Provider, bot_id: BotId, chat: OptionalChat = None) -> str:
    """List the conversations a chatbot is currently handling, optionally only those of one chat.

    A session is one chat talking to the bot. Each row gives the session_id, the chat_id, the status (opened, paused
    or closed) and when the session started and last changed. Use change_chatbot_session to pause, close or delete a
    session, and get_chatbot for the bot's own configuration.
    """
    conn, client = await context.resolve()
    chat_jid = await calls.resolve_chat(client, conn, chat, purpose="read") if chat else None
    rows = await _provider_call(client, conn.identity, provider, "GET", "fetchSessions", bot_id)
    sessions = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        if row.get("botId") not in (None, bot_id):
            continue
        if chat_jid is not None and row.get("remoteJid") != chat_jid:
            continue
        sessions.append(
            _omit_none(
                {
                    "session_id": row.get("sessionId"),
                    "chat_id": row.get("remoteJid"),
                    "status": row.get("status"),
                    "started_at": _iso_date(row.get("createdAt")),
                    "updated_at": _iso_date(row.get("updatedAt")),
                }
            )
        )
    return tool_result({"sessions": sessions})


@registry.tool(
    title="Change chatbot session",
    toolset="integrations",
    kind="destructive",
    idempotent=True,
    integrations=registry.ALL,
)
async def change_chatbot_session(
    provider: Provider,
    chat: Chat,
    status: Annotated[
        Literal["opened", "paused", "closed", "delete"],
        Field(
            description=(
                "opened resumes the bot in this chat, paused stops it from answering, closed ends the session and "
                "delete removes the chat's sessions."
            )
        ),
    ],
) -> str:
    """Resume, pause, close or delete the chatbot sessions of one chat for an integration.

    Pausing or closing makes the bot stop answering that person; opening lets it answer again. The status applies to
    every session of the chat for this provider, not to one bot. Deleting removes the session records and their
    context. Use list_chatbot_sessions to see the current statuses. Returns the chat_id and the status that was set.
    """
    conn, client = await context.resolve()
    chat_jid = await calls.resolve_chat(client, conn, chat, purpose="write")
    await _provider_call(
        client,
        conn.identity,
        provider,
        "POST",
        "changeStatus",
        json={"remoteJid": chat_jid, "status": status},
        write=True,
    )
    return tool_result({"chat_id": chat_jid, "status": status})


@registry.tool(
    title="Ignore chat for chatbot",
    toolset="integrations",
    kind="destructive",
    idempotent=True,
    integrations=registry.ALL,
)
async def set_chatbot_ignored_chat(
    provider: Provider,
    chat: Chat,
    ignored: Annotated[
        bool,
        Field(description="true adds the chat to the provider's ignore list; false removes it."),
    ],
) -> str:
    """Add a chat to, or remove it from, the list of chats the chatbots of one integration skip.

    Chats on the list receive no bot replies. The list is part of the provider's defaults (get_chatbot_settings shows
    it), so the change applies to all bots of that provider. Evolution needs the defaults to exist already.
    Returns the chat_id and whether it is now ignored.
    """
    conn, client = await context.resolve()
    chat_jid = await calls.resolve_chat(client, conn, chat, purpose="write")
    await _provider_call(
        client,
        conn.identity,
        provider,
        "POST",
        "ignoreJid",
        json={"remoteJid": chat_jid, "action": "add" if ignored else "remove"},
        write=True,
    )
    return tool_result({"chat_id": chat_jid, "ignored": ignored})


@registry.tool(
    title="Start Typebot session",
    toolset="integrations",
    kind="destructive",
    idempotent=False,
    integrations=registry.ALL,
)
async def start_typebot_session(
    chat: Chat,
    typebot_url: Annotated[
        str,
        Field(
            min_length=10,
            max_length=500,
            pattern=r"^https?://",
            description="URL of the Typebot server that hosts the flow.",
        ),
    ],
    typebot_flow: Annotated[
        str, Field(min_length=1, max_length=200, description="Public id (slug) of the Typebot flow to run.")
    ],
    variables: Annotated[
        dict[str, str] | None,
        Field(max_length=50, description="Values passed to the flow as prefilled variables, by variable name."),
    ] = None,
    start_session: Annotated[
        bool,
        Field(description="true starts the flow from its first block; false continues from the current state."),
    ] = False,
) -> str:
    """Start a Typebot flow in one chat, which sends that person the flow's first messages.

    The Typebot flow talks to the person from this WhatsApp number, so the recipient and the flow come from the user.
    Starting a session again sends the messages again. Returns the chat_id once Evolution accepted the start. The
    typebot integration must be enabled on the Evolution server; create_chatbot with provider typebot registers a
    flow that starts from triggers instead.
    """
    conn, client = await context.resolve()
    chat_jid = await calls.resolve_chat(client, conn, chat, purpose="send")
    body = {
        "remoteJid": chat_jid,
        "url": typebot_url,
        "typebot": typebot_flow,
        "startSession": start_session,
        "variables": [{"name": name, "value": value} for name, value in (variables or {}).items()],
    }
    await _provider_call(client, conn.identity, "typebot", "POST", "start", json=body, write=True)
    return tool_result({"chat_id": chat_jid, "started": True})


# --- OpenAI credentials ------------------------------------------------------------------------------------------


@registry.tool(
    title="List OpenAI credentials",
    toolset="integrations",
    kind="read",
    idempotent=True,
    integrations=registry.ALL,
)
async def list_openai_credentials() -> str:
    """List the OpenAI credentials stored on this Evolution instance by name and id.

    The credential_id is what create_chatbot and update_chatbot_settings take as openai_credential_id. API keys are
    never returned. The openai integration must be enabled on the Evolution server.
    """
    conn, client = await context.resolve()
    rows = await _provider_call(client, conn.identity, "openai", "GET", "creds")
    credentials = [
        {"credential_id": row.get("id"), "name": row.get("name")}
        for row in (rows if isinstance(rows, list) else [])
        if isinstance(row, dict)
    ]
    return tool_result({"credentials": credentials})


@registry.tool(
    title="Add OpenAI credential",
    toolset="integrations",
    kind="write",
    idempotent=False,
    integrations=registry.ALL,
    local_only=True,
)
async def create_openai_credential(
    name: Annotated[str, Field(min_length=1, max_length=100, description="Label for the credential, unique here.")],
    api_key: Annotated[
        str,
        Field(min_length=20, max_length=500, description="OpenAI API key. Evolution stores it and never returns it."),
    ],
) -> str:
    """Store an OpenAI API key on this Evolution instance so that OpenAI chatbots can use it.

    The key is kept by Evolution and used only for the chatbots that reference the returned credential_id, which is why
    this tool is available on the local server only. Evolution refuses a key or a name that is already registered.
    The credential can be removed with delete_openai_credential. Returns the credential_id and the name.
    """
    conn, client = await context.resolve()
    answer = await _provider_call(
        client, conn.identity, "openai", "POST", "creds", json={"name": name, "apiKey": api_key}, write=True
    )
    row = answer if isinstance(answer, dict) else {}
    return tool_result({"credential_id": row.get("id"), "name": row.get("name", name)})


@registry.tool(
    title="Delete OpenAI credential",
    toolset="integrations",
    kind="irreversible",
    idempotent=True,
    integrations=registry.ALL,
)
async def delete_openai_credential(
    credential_id: Annotated[
        str, Field(min_length=1, max_length=100, description="Credential id from list_openai_credentials.")
    ],
) -> str:
    """Delete a stored OpenAI credential; the key cannot be recovered from this server.

    OpenAI chatbots that reference the credential stop working until another credential is set on them. Returns
    deleted true when Evolution removed it.
    """
    conn, client = await context.resolve()
    await _provider_call(client, conn.identity, "openai", "DELETE", "creds", credential_id, write=True)
    return tool_result({"deleted": True})


@registry.tool(
    title="List OpenAI models",
    toolset="integrations",
    kind="read",
    idempotent=True,
    integrations=registry.ALL,
)
async def list_openai_models(
    credential_id: Annotated[
        str | None,
        Field(
            min_length=1,
            max_length=100,
            description="Credential id from list_openai_credentials. Omit for the default credential of the defaults.",
        ),
    ] = None,
) -> str:
    """List the OpenAI model ids that a stored credential can use, sorted alphabetically.

    Evolution asks OpenAI with the stored key, so the call fails when the credential is invalid or none is set. The
    ids are what create_chatbot takes as model for an OpenAI chatCompletion bot.
    """
    conn, client = await context.resolve()
    rows = await _provider_call(
        client,
        conn.identity,
        "openai",
        "GET",
        "getModels",
        params={"openaiCredsId": credential_id} if credential_id else None,
    )
    ids = sorted(
        row["id"] for row in (rows if isinstance(rows, list) else []) if isinstance(row, dict) and row.get("id")
    )
    return tool_result({"models": ids})


# --- Chatwoot ----------------------------------------------------------------------------------------------------


def _chatwoot_view(client: EvolutionClient, identity: InstanceIdentity, answer: object) -> dict[str, Any]:
    raw = answer if isinstance(answer, dict) else {}
    safe = _redacted(raw)
    webhook_url = f"{client.base_url}/chatwoot/webhook/{quote(identity.name, safe='')}"
    if not (raw.get("accountId") or raw.get("url") or raw.get("token")):
        return {"configured": False, "enabled": False, "webhook_url": webhook_url}
    result = _omit_none(
        {
            "enabled": safe.get("enabled"),
            "url": safe.get("url"),
            "account_id": safe.get("accountId"),
            "inbox_name": safe.get("nameInbox"),
            "sign_messages": safe.get("signMsg"),
            "reopen_conversation": safe.get("reopenConversation"),
            "conversation_pending": safe.get("conversationPending"),
            "import_contacts": safe.get("importContacts"),
            "import_messages": safe.get("importMessages"),
            "days_limit_import_messages": safe.get("daysLimitImportMessages"),
            "auto_create": safe.get("autoCreate"),
            "organization": safe.get("organization"),
            "ignored_chats": safe.get("ignoreJids"),
        }
    )
    result["token_set"] = bool(raw.get("token"))
    result["webhook_url"] = webhook_url
    return result


@registry.tool(
    title="Get Chatwoot integration",
    toolset="integrations",
    kind="read",
    idempotent=True,
    integrations=registry.ALL,
)
async def get_chatwoot_config() -> str:
    """Return the Chatwoot integration settings of this instance: account, inbox, sync options and webhook URL.

    Chatwoot mirrors this WhatsApp number's conversations into a Chatwoot inbox. The result shows token_set instead of
    the token, and webhook_url, the address Chatwoot must call back on this Evolution server. An instance without a
    Chatwoot setup returns configured false. set_chatwoot_config changes the settings.
    """
    conn, client = await context.resolve()
    row = await calls.call(client, conn.identity, "GET", "chatwoot/find")
    return tool_result(_chatwoot_view(client, conn.identity, row))


@registry.tool(
    title="Set Chatwoot integration",
    toolset="integrations",
    kind="destructive",
    idempotent=True,
    integrations=registry.ALL,
    local_only=True,
)
async def set_chatwoot_config(
    enabled: Annotated[bool, Field(description="Whether the Chatwoot integration is active.")],
    account_id: Annotated[str, Field(min_length=1, max_length=100, description="Chatwoot account id.")],
    url: Annotated[
        str,
        Field(
            min_length=10,
            max_length=500,
            pattern=r"^https?://",
            description="Base URL of the Chatwoot server.",
        ),
    ],
    token: Annotated[
        str | None,
        Field(
            min_length=1,
            max_length=100,
            description=(
                "Chatwoot access token. Omit to keep the stored token; required when none is stored yet. It is "
                "never returned."
            ),
        ),
    ] = None,
    sign_messages: Annotated[bool, Field(description="Add the agent's name to messages sent from Chatwoot.")] = False,
    reopen_conversation: Annotated[
        bool, Field(description="Reopen a resolved Chatwoot conversation when the person writes again.")
    ] = True,
    conversation_pending: Annotated[
        bool, Field(description="Create new Chatwoot conversations as pending instead of open.")
    ] = False,
    inbox_name: Annotated[
        str | None,
        Field(min_length=1, max_length=100, description="Chatwoot inbox name. Default: the instance name."),
    ] = None,
    import_contacts: Annotated[bool, Field(description="Import the WhatsApp contacts into Chatwoot.")] = False,
    import_messages: Annotated[bool, Field(description="Import recent message history into Chatwoot.")] = False,
    days_limit_import_messages: Annotated[
        int, Field(ge=1, le=365, description="How many days of history import_messages brings in.")
    ] = 3,
    auto_create: Annotated[
        bool, Field(description="Create the Chatwoot inbox and webhook automatically when saving.")
    ] = True,
    organization: Annotated[
        str | None,
        Field(min_length=1, max_length=100, description="Organization name used when the inbox is created."),
    ] = None,
    ignored_chats: Annotated[
        list[str] | None,
        Field(
            max_length=200,
            description=(
                "Chats that are not mirrored to Chatwoot: phone numbers or chat_ids from list_chats. @g.us (every "
                "group) is accepted as it is."
            ),
        ),
    ] = None,
) -> str:
    """Save the Chatwoot integration: connect this WhatsApp number to a Chatwoot account and inbox.

    Every setting is written as given, so pass the full configuration; the defaults apply to options left out. Once
    enabled, incoming WhatsApp conversations are copied into Chatwoot and replies typed there are sent to the people
    as real messages from this number. With auto_create Evolution also creates the inbox and its webhook. The token
    goes to Evolution and is never returned, so this tool is available on the local server only. Returns the saved
    settings and webhook_url.
    """
    conn, client = await context.resolve()
    if token is None:
        stored = await calls.call(client, conn.identity, "GET", "chatwoot/find")
        stored_token = stored.get("token") if isinstance(stored, dict) else None
        if not stored_token:
            raise ToolExecutionError(
                "token is required: no Chatwoot token is stored for this instance yet. Nothing was changed."
            )
        token = stored_token
    body: dict[str, Any] = {
        "enabled": enabled,
        "accountId": account_id,
        "token": token,
        "url": url,
        "signMsg": sign_messages,
        "reopenConversation": reopen_conversation,
        "conversationPending": conversation_pending,
        "importContacts": import_contacts,
        "importMessages": import_messages,
        "daysLimitImportMessages": days_limit_import_messages,
        "autoCreate": auto_create,
    }
    if inbox_name is not None:
        body["nameInbox"] = inbox_name
    if organization is not None:
        body["organization"] = organization
    ignore_jids = _ignored_jids(ignored_chats)
    if ignore_jids is not None:
        body["ignoreJids"] = ignore_jids
    answer = await calls.call(client, conn.identity, "POST", "chatwoot/set", json=body, write=True)
    return tool_result(_chatwoot_view(client, conn.identity, answer))
