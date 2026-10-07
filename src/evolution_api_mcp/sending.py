"""Shared send pipeline: recipient, pacing and quoting options, Business rejections, and the send call itself."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from evolution_api_mcp import calls, jid, messages
from evolution_api_mcp.client import EvolutionClient, EvolutionError, EvolutionHTTPError
from evolution_api_mcp.context import Connection, InstanceIdentity, instance_path
from evolution_api_mcp.errors import ToolExecutionError, raise_evolution_failure
from evolution_api_mcp.registry import BAILEYS

MAX_DELAY_MS = 20_000
_WINDOW_CLOSED_CODE = 131047
_WINDOW_CLOSED_HINT = (
    " The 24-hour customer-service window is closed for this person; an approved template "
    "(send_template_message) can reopen the conversation."
)


def recipient(identity: InstanceIdentity, chat_jid: str) -> str:
    """The `number` Evolution expects: the full JID on WhatsApp Web instances, bare digits otherwise."""
    if identity.integration == BAILEYS:
        return chat_jid
    phone = jid.phone_of(chat_jid)
    if phone is None:
        raise ToolExecutionError(
            "This instance can message phone numbers only; groups and @lid ids need a WhatsApp Web (Baileys) instance."
        )
    return phone


def reply_key(row: dict) -> dict:
    """The `quoted` option for a stored message row (also the `key` of reactions)."""
    stored = row["key"]
    key = {"id": stored["id"], "remoteJid": stored["remoteJid"], "fromMe": bool(stored.get("fromMe"))}
    if stored.get("participant"):
        key["participant"] = stored["participant"]
    return {"key": key}


async def reply_target(
    client: EvolutionClient, conn: Connection, chat: str | None, reply_to_message_id: str | None
) -> tuple[str, dict | None]:
    """`(chat JID, quoted row)` of a send: the named chat, or the chat of the message being answered.

    A reply may omit `chat`; when both are given the message must belong to that chat.
    """
    if reply_to_message_id is None:
        if chat is None:
            raise ToolExecutionError(
                "Give chat, or reply_to_message_id to answer in that message's chat. Nothing was sent."
            )
        return await calls.resolve_chat(client, conn, chat, purpose="send"), None
    chat_jid = await calls.resolve_chat(client, conn, chat, purpose="send") if chat is not None else None
    row = await calls.stored_message(client, conn, reply_to_message_id, chat_jid, purpose="send")
    key = row["key"] if isinstance(row.get("key"), dict) else {}
    row_chat = key.get("remoteJid")
    if not isinstance(row_chat, str) or not row_chat:
        raise ToolExecutionError(f"Message {reply_to_message_id} has no stored chat to reply in. Nothing was sent.")
    if chat_jid is not None and chat_jid not in (row_chat, key.get("remoteJidAlt")):
        raise ToolExecutionError(
            f"Message {reply_to_message_id} belongs to chat {row_chat}, not {chat_jid}. Nothing was sent."
        )
    return chat_jid or row_chat, row


async def build_options(
    client: EvolutionClient,
    identity: InstanceIdentity,
    conn: Connection,
    *,
    delay_ms: int | None,
    quoted_row: dict | None,
    mention: list[str] | None,
    mention_everyone: bool,
    link_preview: bool | None,
) -> dict:
    """Top-level send options: pacing delay, quoted message, mentions and link preview."""
    delay = conn.default_delay_ms if delay_ms is None else delay_ms
    options: dict = {"delay": max(0, min(delay, MAX_DELAY_MS))}

    if (mention or mention_everyone) and identity.integration != BAILEYS:
        raise ToolExecutionError("Mentions work only on WhatsApp Web (Baileys) instances.")

    if quoted_row is not None:
        options["quoted"] = reply_key(quoted_row)

    if mention:
        mentioned: list[str] = []
        for entry in mention:
            _, phone = await calls.resolve_person(client, conn, entry, purpose="send")
            if phone not in mentioned:
                mentioned.append(phone)
        options["mentioned"] = mentioned
    if mention_everyone:
        options["mentionsEveryOne"] = True  # the key Evolution's services read; its JSON schema names it everyOne
    if link_preview is not None:
        options["linkPreview"] = link_preview
    return options


def check_business_rejection(response: object) -> None:
    """WhatsApp Business Platform reports Meta rejections as a successful HTTP answer holding the Meta error object."""
    if not isinstance(response, dict):
        return
    if "error_data" not in response and not ("code" in response and "message" in response and "key" not in response):
        return
    code = response.get("code")
    message = response.get("message")
    text = (
        f"WhatsApp Business Platform refused the message (code {'unknown' if code is None else code}): "
        f"{'no reason given' if message is None else message}. Nothing was sent."
    )
    if str(code) == str(_WINDOW_CLOSED_CODE):
        text += _WINDOW_CLOSED_HINT
    raise ToolExecutionError(text)


def sent_result(response: object) -> dict:
    """The compact result every send tool returns."""
    body = response if isinstance(response, dict) else {}
    key = body.get("key") if isinstance(body.get("key"), dict) else {}
    return {
        "message_id": key.get("id"),
        "chat_id": key.get("remoteJid"),
        "status": body.get("status"),
        "timestamp": messages.iso(body.get("messageTimestamp")),
    }


def _absent_number(body: object) -> str | None:
    """The number Evolution reported as not on WhatsApp, or None when the 400 says something else.

    Evolution throws the lookup result as the exception payload, so on the wire it arrives wrapped in
    `response.message[]`; a bare `{jid, exists, number}` body is accepted as well.
    """
    if not isinstance(body, dict):
        return None
    candidates: list[object] = [body]
    wrapped = body.get("response")
    if isinstance(wrapped, dict):
        inner = wrapped.get("message")
        candidates.extend(inner if isinstance(inner, list) else [inner])
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate.get("exists") is False:
            return str(candidate.get("number") or candidate.get("jid") or "This number")
    return None


async def send(
    client: EvolutionClient,
    identity: InstanceIdentity,
    conn: Connection,
    endpoint: str,
    body: dict,
    chat_jid: str,
    *,
    timeout: float | None = None,  # noqa: ASYNC109 - forwarded to the HTTP client, not an anyio cancel scope
) -> dict:
    """POST one message to `endpoint` and return the compact result.

    A failure after the request left is reported as UNCERTAIN with the chat's recent outgoing messages re-read, so
    the caller can see whether the message went out before repeating it.
    """
    started = datetime.now(timezone.utc)

    async def reread() -> object:
        now = datetime.now(timezone.utc)
        window = messages.time_window(started - timedelta(seconds=5), now + timedelta(seconds=60))
        page = await messages.fetch_page(
            client,
            identity,
            where={"key": messages.chat_key_filter(chat_jid) | {"fromMe": True}, "messageTimestamp": window},
            page_size=5,
            page=1,
        )
        return [messages.project_message(row, text_limit=200) for row in page["records"]]

    try:
        response = await client.request("POST", instance_path(identity, endpoint), json=body, timeout=timeout)
    except EvolutionError as exc:
        if isinstance(exc, EvolutionHTTPError) and exc.status == 400:
            absent = _absent_number(exc.body)
            if absent is not None:
                raise ToolExecutionError(f"{absent} is not on WhatsApp. Nothing was sent.") from None
        await raise_evolution_failure(exc, phase="after_mutation_possible", reread=reread)
    check_business_rejection(response)
    return sent_result(response)
