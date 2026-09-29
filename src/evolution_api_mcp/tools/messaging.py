"""Messaging tools: send text, media, voice notes, locations, contacts, polls, lists, buttons; react, edit, delete."""

import base64
import re
import time
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

import anyio
from pydantic import BaseModel, Field, field_validator

from evolution_api_mcp import calls, context, media, messages, sending
from evolution_api_mcp.client import EvolutionError
from evolution_api_mcp.context import Connection
from evolution_api_mcp.errors import ToolExecutionError, raise_evolution_failure, tool_result
from evolution_api_mcp.registry import ALL, BAILEYS, BUSINESS, tool
from evolution_api_mcp.tools import Chat, DelayMs, MediaUrl, Mention, MentionEveryone, MessageId, ReplyTo

BAI = frozenset({BAILEYS})
BAI_BUS = frozenset({BAILEYS, BUSINESS})

EDIT_WINDOW_SECONDS = 900
_NOT_FOUND = "Message {id} was not found in this chat. Use read_messages to find its id."
_AUDIO_NO_CAPTION = "Audio messages carry no caption; send the text separately."
_MAX_BUSINESS_LIST_ROWS = 10
_NON_DIGITS = re.compile(r"[\s+().-]")


class ContactCard(BaseModel):
    """One contact shared as a card."""

    full_name: Annotated[str, Field(min_length=1, max_length=256, description="Display name shown on the card.")]
    phone_number: Annotated[
        str,
        Field(
            description=(
                "International phone number, country code first (spaces, +, dashes and brackets are ignored). "
                "10 to 15 digits."
            )
        ),
    ]
    organization: Annotated[str | None, Field(max_length=256, description="Company shown on the card.")] = None
    email: Annotated[str | None, Field(max_length=256, description="Email address shown on the card.")] = None
    url: Annotated[str | None, Field(max_length=2048, description="Web address shown on the card.")] = None

    @field_validator("phone_number")
    @classmethod
    def _digits(cls, value: str) -> str:
        digits = _NON_DIGITS.sub("", value)
        if not digits.isdigit() or not 10 <= len(digits) <= 15:
            raise ValueError("phone_number must be an international phone number of 10 to 15 digits")
        return digits


class ListRow(BaseModel):
    """One selectable row of a list message."""

    row_id: Annotated[
        str,
        Field(min_length=1, max_length=200, description="Id returned to the sender when the recipient picks this row."),
    ]
    title: Annotated[str, Field(min_length=1, max_length=24, description="Row title.")]
    description: Annotated[str | None, Field(max_length=72, description="Second line under the title.")] = None


class ListSection(BaseModel):
    """A titled group of rows in a list message."""

    title: Annotated[str, Field(min_length=1, max_length=24, description="Section title.")]
    rows: Annotated[list[ListRow], Field(min_length=1, max_length=10, description="Rows of this section (1 to 10).")]


class MessageButton(BaseModel):
    """One button of a button message."""

    type: Annotated[
        Literal["reply", "url", "call", "copy"],
        Field(description="reply sends back reply_id; url opens url; call dials phone_number; copy copies copy_code."),
    ]
    text: Annotated[str, Field(min_length=1, max_length=20, description="Text shown on the button.")]
    reply_id: Annotated[str | None, Field(max_length=256, description="Required for reply buttons.")] = None
    url: Annotated[str | None, Field(max_length=2048, description="Required for url buttons.")] = None
    phone_number: Annotated[
        str | None, Field(max_length=32, description="Required for call buttons (international format).")
    ] = None
    copy_code: Annotated[str | None, Field(max_length=256, description="Required for copy buttons.")] = None


_BUTTON_FIELD = {"reply": "reply_id", "url": "url", "call": "phone_number", "copy": "copy_code"}
_BUTTON_WIRE = {"reply": "id", "url": "url", "call": "phoneNumber", "copy": "copyCode"}


async def _deliver(
    conn: Connection,
    client: Any,
    endpoint: str,
    chat: str,
    fields: dict,
    *,
    delay_ms: int | None,
    reply_to_message_id: str | None = None,
    mention: list[str] | None = None,
    mention_everyone: bool = False,
    link_preview: bool | None = None,
    timeout: float | None = None,  # noqa: ASYNC109 - forwarded to the HTTP client, not an anyio cancel scope
    extra: dict | None = None,
) -> str:
    """The plan's send flow: normalize the chat, pick the recipient form, build options, send, report."""
    identity = conn.identity
    chat_jid = calls.chat(chat)
    number = sending.recipient(identity, chat_jid)
    options = await sending.build_options(
        client,
        identity,
        conn,
        chat_jid,
        delay_ms=delay_ms,
        reply_to_message_id=reply_to_message_id,
        mention=mention,
        mention_everyone=mention_everyone,
        link_preview=link_preview,
    )
    result = await sending.send(
        client, identity, conn, endpoint, {"number": number, **fields, **options}, chat_jid, timeout=timeout
    )
    return tool_result({**result, **(extra or {})})


async def _stored_message(client: Any, identity: Any, chat_jid: str, message_id: str) -> dict:
    """The stored row of `message_id` in the chat, or a tool error naming how to find its id."""
    try:
        row = await messages.find_message(client, identity, message_id, chat_jid)
    except EvolutionError as exc:
        await raise_evolution_failure(exc, phase="before_mutation")
    if row is None:
        raise ToolExecutionError(_NOT_FOUND.format(id=message_id))
    return row


def _read_base64(path: Path) -> tuple[str, int]:
    data = path.read_bytes()
    return base64.b64encode(data).decode(), len(data)


@tool(
    title="Send text message",
    toolset="messaging",
    kind="destructive",
    idempotent=False,
    integrations=ALL,
)
async def send_text_message(
    chat: Chat,
    text: Annotated[str, Field(min_length=1, max_length=4096, description="Message text.")],
    reply_to_message_id: ReplyTo = None,
    mention: Mention = None,
    mention_everyone: MentionEveryone = False,
    link_preview: Annotated[
        bool | None,
        Field(description="Show a preview card for the first link in the text. Default: Evolution's own default."),
    ] = None,
    delay_ms: DelayMs = None,
) -> str:
    """Send a text message to a person or group from the connected number.

    The message is delivered to the recipient immediately and cannot be recalled by this server;
    delete_message_for_everyone removes it only on WhatsApp Web (Baileys) instances and only while WhatsApp allows it.
    Wording and recipient come from the user. reply_to_message_id quotes an earlier message of the same chat.
    Mentions work only on WhatsApp Web (Baileys) instances. On WhatsApp Business Platform instances free-form text
    reaches a person only within 24 hours of their last message; send_template_message covers other cases.
    Returns message_id, chat_id, status and timestamp.
    """
    conn, client = await context.resolve()
    return await _deliver(
        conn,
        client,
        "message/sendText",
        chat,
        {"text": text},
        delay_ms=delay_ms,
        reply_to_message_id=reply_to_message_id,
        mention=mention,
        mention_everyone=mention_everyone,
        link_preview=link_preview,
    )


@tool(
    title="Send media from URL",
    toolset="messaging",
    kind="destructive",
    idempotent=False,
    integrations=ALL,
)
async def send_media_message(
    chat: Chat,
    media_url: MediaUrl,
    media_type: Annotated[
        Literal["image", "video", "audio", "document"],
        Field(description="How the file is delivered: as an image, a video, an audio file or a document."),
    ],
    caption: Annotated[str | None, Field(max_length=1024, description="Text shown under the file.")] = None,
    file_name: Annotated[
        str | None,
        Field(max_length=255, description="File name shown for documents. Default: the last part of the URL."),
    ] = None,
    mimetype: Annotated[
        str | None,
        Field(max_length=100, description="MIME type, e.g. application/pdf. Default: detected by Evolution."),
    ] = None,
    reply_to_message_id: ReplyTo = None,
    mention: Mention = None,
    mention_everyone: MentionEveryone = False,
    delay_ms: DelayMs = None,
) -> str:
    """Send an image, video, audio file or document that Evolution downloads from a public URL.

    Use send_voice_note for a voice message and send_local_file (local server only) for a file on this computer.
    Audio files carry no caption. The message is delivered immediately and cannot be recalled by this server.
    Mentions work only on WhatsApp Web (Baileys) instances.
    Returns message_id, chat_id, status and timestamp.
    """
    if media_type == "audio" and caption:
        raise ToolExecutionError(_AUDIO_NO_CAPTION)
    conn, client = await context.resolve()
    fields: dict = {"mediatype": media_type, "media": media_url}
    if caption:
        fields["caption"] = caption
    if file_name:
        fields["fileName"] = file_name
    elif media_type == "document":
        segment = urlparse(media_url).path.rsplit("/", 1)[-1]
        fields["fileName"] = media.safe_file_name(segment) if segment else "document"
    if mimetype:
        fields["mimetype"] = mimetype
    return await _deliver(
        conn,
        client,
        "message/sendMedia",
        chat,
        fields,
        delay_ms=delay_ms,
        reply_to_message_id=reply_to_message_id,
        mention=mention,
        mention_everyone=mention_everyone,
    )


@tool(
    title="Send local file",
    toolset="messaging",
    kind="destructive",
    idempotent=False,
    integrations=ALL,
    local_only=True,
)
async def send_local_file(
    chat: Chat,
    path: Annotated[
        str,
        Field(min_length=1, max_length=4096, description="Absolute path (or starting with ~) of the file to send."),
    ],
    caption: Annotated[str | None, Field(max_length=1024, description="Text shown under the file.")] = None,
    send_as: Annotated[
        Literal["auto", "document", "voice_note"],
        Field(
            description=(
                "auto picks image, video, audio or document from the file type; document always sends a document; "
                "voice_note sends an audio file as a voice message."
            )
        ),
    ] = "auto",
    reply_to_message_id: ReplyTo = None,
    mention: Mention = None,
    mention_everyone: MentionEveryone = False,
    delay_ms: DelayMs = None,
) -> str:
    """Send a file from this computer to a person or group.

    Available only on the local server. The file must sit inside the folders in EVOLUTION_MCP_FILE_ROOTS, must not be
    a hidden file or inside a hidden folder, and may be at most 100 MiB. It is uploaded to the Evolution server as
    base64. Audio files carry no caption, and send_as voice_note needs an audio file. The message is delivered
    immediately and cannot be recalled by this server. Mentions work only on WhatsApp Web (Baileys) instances.
    Returns message_id, chat_id, status, timestamp, file and size_bytes.
    """
    conn, client = await context.resolve()
    file = media.resolve_local_file(path, context.local_config().file_roots)
    mimetype, kind = media.media_kind(file)
    if send_as == "voice_note":
        if kind != "audio":
            raise ToolExecutionError(f"send_as='voice_note' needs an audio file; {file.name} is {mimetype}.")
        if caption:
            raise ToolExecutionError("Voice notes carry no caption; send the text separately.")
        endpoint, mediatype = "message/sendWhatsAppAudio", "audio"
    else:
        endpoint, mediatype = "message/sendMedia", "document" if send_as == "document" else kind
        if mediatype == "audio" and caption:
            raise ToolExecutionError(_AUDIO_NO_CAPTION)
    try:
        data, size = await anyio.to_thread.run_sync(_read_base64, file)
    except OSError as exc:
        raise ToolExecutionError(f"Could not read {file.name}: {exc.strerror or exc}. Nothing was sent.") from exc
    if endpoint == "message/sendWhatsAppAudio":
        fields: dict = {"audio": data}
    else:
        fields = {"mediatype": mediatype, "mimetype": mimetype, "fileName": file.name, "media": data}
        if caption:
            fields["caption"] = caption
    return await _deliver(
        conn,
        client,
        endpoint,
        chat,
        fields,
        delay_ms=delay_ms,
        reply_to_message_id=reply_to_message_id,
        mention=mention,
        mention_everyone=mention_everyone,
        timeout=600,
        extra={"file": file.name, "size_bytes": size},
    )


@tool(
    title="Send voice note",
    toolset="messaging",
    kind="destructive",
    idempotent=False,
    integrations=ALL,
)
async def send_voice_note(
    chat: Chat,
    audio_url: MediaUrl,
    reply_to_message_id: ReplyTo = None,
    delay_ms: DelayMs = None,
) -> str:
    """Send an audio file from a public URL as a voice message.

    Use send_media_message with media_type audio to send the file as a plain audio attachment. WhatsApp Business
    Platform instances deliver it as an audio file, not as a voice note. The message is delivered immediately and
    cannot be recalled by this server. Returns message_id, chat_id, status and timestamp.
    """
    conn, client = await context.resolve()
    return await _deliver(
        conn,
        client,
        "message/sendWhatsAppAudio",
        chat,
        {"audio": audio_url},
        delay_ms=delay_ms,
        reply_to_message_id=reply_to_message_id,
    )


@tool(title="Send video note", toolset="messaging", kind="destructive", idempotent=False, integrations=BAI)
async def send_video_note(
    chat: Chat,
    video_url: MediaUrl,
    reply_to_message_id: ReplyTo = None,
    delay_ms: DelayMs = None,
) -> str:
    """Send a video from a public URL as a round video note (WhatsApp Web instances only).

    Use send_media_message with media_type video for an ordinary video attachment. The message is delivered
    immediately and cannot be recalled by this server. Returns message_id, chat_id, status and timestamp.
    """
    conn, client = await context.resolve()
    return await _deliver(
        conn,
        client,
        "message/sendPtv",
        chat,
        {"video": video_url},
        delay_ms=delay_ms,
        reply_to_message_id=reply_to_message_id,
    )


@tool(title="Send sticker", toolset="messaging", kind="destructive", idempotent=False, integrations=BAI)
async def send_sticker(
    chat: Chat,
    sticker_url: MediaUrl,
    reply_to_message_id: ReplyTo = None,
    delay_ms: DelayMs = None,
) -> str:
    """Send a sticker from a public image URL (WhatsApp Web instances only).

    The URL should point to a WebP sticker or an image Evolution can convert. The message is delivered immediately and
    cannot be recalled by this server. Returns message_id, chat_id, status and timestamp.
    """
    conn, client = await context.resolve()
    return await _deliver(
        conn,
        client,
        "message/sendSticker",
        chat,
        {"sticker": sticker_url},
        delay_ms=delay_ms,
        reply_to_message_id=reply_to_message_id,
    )


@tool(title="Send location", toolset="messaging", kind="destructive", idempotent=False, integrations=BAI_BUS)
async def send_location(
    chat: Chat,
    latitude: Annotated[float, Field(ge=-90, le=90, description="Latitude in decimal degrees.")],
    longitude: Annotated[float, Field(ge=-180, le=180, description="Longitude in decimal degrees.")],
    name: Annotated[str, Field(min_length=1, max_length=256, description="Place name shown on the pin.")],
    address: Annotated[str, Field(min_length=1, max_length=512, description="Street address shown under the name.")],
    reply_to_message_id: ReplyTo = None,
    delay_ms: DelayMs = None,
) -> str:
    """Send a map location pin with a name and address.

    The message is delivered immediately and cannot be recalled by this server.
    Returns message_id, chat_id, status and timestamp.
    """
    conn, client = await context.resolve()
    return await _deliver(
        conn,
        client,
        "message/sendLocation",
        chat,
        {"latitude": latitude, "longitude": longitude, "name": name, "address": address},
        delay_ms=delay_ms,
        reply_to_message_id=reply_to_message_id,
    )


@tool(title="Send contact card", toolset="messaging", kind="destructive", idempotent=False, integrations=BAI_BUS)
async def send_contact_card(
    chat: Chat,
    contacts: Annotated[
        list[ContactCard], Field(min_length=1, max_length=10, description="Contacts to share (1 to 10).")
    ],
    reply_to_message_id: ReplyTo = None,
    delay_ms: DelayMs = None,
) -> str:
    """Send one or more contact cards (name and phone number) to a person or group.

    The cards are delivered immediately and cannot be recalled by this server; the recipient sees the phone numbers
    shared, so only share numbers the user has approved. Returns message_id, chat_id, status and timestamp.
    """
    conn, client = await context.resolve()
    cards = []
    for card in contacts:
        entry: dict = {"fullName": card.full_name, "wuid": card.phone_number, "phoneNumber": f"+{card.phone_number}"}
        if card.organization:
            entry["organization"] = card.organization
        if card.email:
            entry["email"] = card.email
        if card.url:
            entry["url"] = card.url
        cards.append(entry)
    return await _deliver(
        conn,
        client,
        "message/sendContact",
        chat,
        {"contact": cards},
        delay_ms=delay_ms,
        reply_to_message_id=reply_to_message_id,
    )


@tool(title="Send poll", toolset="messaging", kind="destructive", idempotent=False, integrations=BAI)
async def send_poll(
    chat: Chat,
    question: Annotated[str, Field(min_length=1, max_length=255, description="The poll question.")],
    options: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=100)]],
        Field(
            min_length=2,
            max_length=10,
            json_schema_extra={"uniqueItems": True},
            description="Answer options (2 to 10, each different).",
        ),
    ],
    max_selections: Annotated[
        int,
        Field(ge=0, le=10, description="How many options a voter may pick. 0 allows any number. Default 1."),
    ] = 1,
    reply_to_message_id: ReplyTo = None,
    delay_ms: DelayMs = None,
) -> str:
    """Send a poll to a person or group (WhatsApp Web instances only).

    The poll is delivered immediately and cannot be recalled by this server. Votes arrive as messages and are not
    tallied by this server. Returns message_id, chat_id, status and timestamp.
    """
    if len(set(options)) != len(options):
        raise ToolExecutionError("Poll options must all be different. Nothing was sent.")
    conn, client = await context.resolve()
    return await _deliver(
        conn,
        client,
        "message/sendPoll",
        chat,
        {"name": question, "selectableCount": max_selections, "values": options},
        delay_ms=delay_ms,
        reply_to_message_id=reply_to_message_id,
    )


@tool(title="Send list message", toolset="messaging", kind="destructive", idempotent=False, integrations=BAI_BUS)
async def send_list_message(
    chat: Chat,
    title: Annotated[str, Field(min_length=1, max_length=60, description="Heading of the message.")],
    description: Annotated[str, Field(min_length=1, max_length=1024, description="Body text of the message.")],
    button_text: Annotated[
        str, Field(min_length=1, max_length=20, description="Label of the button that opens the list.")
    ],
    sections: Annotated[
        list[ListSection], Field(min_length=1, max_length=10, description="Sections of the list (1 to 10).")
    ],
    footer: Annotated[str, Field(max_length=60, description="Small text under the body. Default: none.")] = "",
    reply_to_message_id: ReplyTo = None,
    delay_ms: DelayMs = None,
) -> str:
    """Send a message with a button that opens a list of selectable rows.

    The person's choice comes back as a message that read_messages shows as a response. WhatsApp Business Platform
    instances accept at most 10 rows in total. Recipients' apps may not display list and button messages sent from
    WhatsApp Web (Baileys) instances. The message is delivered immediately and cannot be recalled by this server.
    Returns message_id, chat_id, status and timestamp.
    """
    conn, client = await context.resolve()
    if conn.identity.integration == BUSINESS and sum(len(s.rows) for s in sections) > _MAX_BUSINESS_LIST_ROWS:
        raise ToolExecutionError("WhatsApp Business Platform lists hold at most 10 rows in total.")
    wire_sections = []
    for section in sections:
        rows = []
        for row in section.rows:
            entry = {"title": row.title, "rowId": row.row_id}
            if row.description:
                entry["description"] = row.description
            rows.append(entry)
        wire_sections.append({"title": section.title, "rows": rows})
    return await _deliver(
        conn,
        client,
        "message/sendList",
        chat,
        {
            "title": title,
            "description": description,
            "buttonText": button_text,
            "footerText": footer,
            "sections": wire_sections,
        },
        delay_ms=delay_ms,
        reply_to_message_id=reply_to_message_id,
    )


@tool(
    title="Send button message",
    toolset="messaging",
    kind="destructive",
    idempotent=False,
    integrations=ALL,
)
async def send_button_message(
    chat: Chat,
    title: Annotated[str, Field(min_length=1, max_length=60, description="Heading of the message.")],
    description: Annotated[str, Field(min_length=1, max_length=1024, description="Body text of the message.")],
    buttons: Annotated[
        list[MessageButton], Field(min_length=1, max_length=3, description="Buttons under the message (1 to 3).")
    ],
    footer: Annotated[str | None, Field(max_length=60, description="Small text under the body.")] = None,
    reply_to_message_id: ReplyTo = None,
    delay_ms: DelayMs = None,
) -> str:
    """Send a message with up to three buttons: quick replies, links, call buttons or copy-code buttons.

    Each button needs the field that matches its type: reply_id for reply, url for url, phone_number for call and
    copy_code for copy. WhatsApp Business Platform instances support reply buttons only. Recipients' apps may not
    display button messages sent from WhatsApp Web (Baileys) instances. Payment buttons are not offered. The message
    is delivered immediately and cannot be recalled by this server.
    Returns message_id, chat_id, status and timestamp.
    """
    conn, client = await context.resolve()
    wire_buttons = []
    for button in buttons:
        field = _BUTTON_FIELD[button.type]
        value = getattr(button, field)
        if not value:
            raise ToolExecutionError(f"A {button.type} button ('{button.text}') needs {field}. Nothing was sent.")
        wire_buttons.append({"type": button.type, "displayText": button.text, _BUTTON_WIRE[button.type]: value})
    if conn.identity.integration == BUSINESS and any(b.type != "reply" for b in buttons):
        raise ToolExecutionError("WhatsApp Business Platform instances support reply buttons only.")
    fields: dict = {"title": title, "description": description, "buttons": wire_buttons}
    if footer:
        fields["footer"] = footer
    return await _deliver(
        conn,
        client,
        "message/sendButtons",
        chat,
        fields,
        delay_ms=delay_ms,
        reply_to_message_id=reply_to_message_id,
    )


@tool(title="Show typing or recording", toolset="messaging", kind="write", idempotent=False, integrations=BAI)
async def send_chat_presence(
    chat: Chat,
    presence: Annotated[
        Literal["composing", "recording", "paused"],
        Field(description="composing shows 'typing…', recording shows 'recording audio…', paused clears it."),
    ],
    duration_ms: Annotated[
        int, Field(ge=500, le=20000, description="How long the indicator stays visible, in milliseconds. Default 3000.")
    ] = 3000,
) -> str:
    """Show a typing or recording indicator in a chat for a few seconds (WhatsApp Web instances only).

    The indicator is temporary and carries no content. The call returns after the duration has elapsed.
    Returns chat_id, presence and duration_ms.
    """
    conn, client = await context.resolve()
    chat_jid = calls.chat(chat)
    number = sending.recipient(conn.identity, chat_jid)
    await calls.call(
        client,
        conn.identity,
        "POST",
        "chat/sendPresence",
        json={"number": number, "presence": presence, "delay": duration_ms},
        timeout=duration_ms / 1000 + 30,
        write=True,
    )
    return tool_result({"chat_id": chat_jid, "presence": presence, "duration_ms": duration_ms})


@tool(title="React to message", toolset="messaging", kind="destructive", idempotent=True, integrations=BAI_BUS)
async def react_to_message(
    chat: Chat,
    message_id: MessageId,
    emoji: Annotated[
        str,
        Field(max_length=16, description="A single emoji. An empty string removes the reaction."),
    ],
) -> str:
    """React to a message with an emoji, or remove the reaction with an empty emoji.

    The person or group sees the reaction. A message can carry one reaction from this number, so reacting again
    replaces the earlier one. Returns message_id, chat_id, status and timestamp of the reaction, plus reacted_to and
    emoji.
    """
    conn, client = await context.resolve()
    chat_jid = calls.chat(chat)
    row = await _stored_message(client, conn.identity, chat_jid, message_id)
    sent = await sending.send(
        client,
        conn.identity,
        conn,
        "message/sendReaction",
        {"key": sending.reply_key(row)["key"], "reaction": emoji},
        chat_jid,
    )
    return tool_result({**sent, "reacted_to": message_id, "emoji": emoji})


@tool(title="Edit sent message", toolset="messaging", kind="destructive", idempotent=True, integrations=BAI)
async def edit_message(
    chat: Chat,
    message_id: MessageId,
    new_text: Annotated[str, Field(min_length=1, max_length=4096, description="The replacement text.")],
) -> str:
    """Replace the text of a message this number sent, or the caption of its image or video.

    WhatsApp allows edits for 15 minutes after sending, and only on messages sent from this number. Everyone in the
    chat sees the edited text and that it was edited. Returns edited, message_id and chat_id.
    """
    conn, client = await context.resolve()
    identity = conn.identity
    chat_jid = calls.chat(chat)
    row = await _stored_message(client, identity, chat_jid, message_id)
    key = row["key"]
    if not key.get("fromMe"):
        raise ToolExecutionError("Only messages sent from this number can be edited.")
    if messages.normalize_type(row.get("messageType"), row.get("message")) not in {"text", "image", "video"}:
        raise ToolExecutionError("Only text messages and image/video captions can be edited.")
    sent_at = messages.ts_seconds(row.get("messageTimestamp"))
    if sent_at is not None and time.time() - sent_at > EDIT_WINDOW_SECONDS:
        minutes = int((time.time() - sent_at) // 60)
        raise ToolExecutionError(
            f"WhatsApp allows edits for 15 minutes after sending; this message is {minutes} minutes old."
        )

    async def reread() -> object:
        current = await messages.find_message(client, identity, message_id, chat_jid)
        return messages.project_message(current, text_limit=200) if current else None

    await calls.call(
        client,
        identity,
        "POST",
        "chat/updateMessage",
        json={
            "number": sending.recipient(identity, chat_jid),
            "text": new_text,
            "key": {"id": key["id"], "remoteJid": key["remoteJid"], "fromMe": True},
        },
        write=True,
        reread=reread,
    )
    return tool_result({"edited": True, "message_id": key["id"], "chat_id": key["remoteJid"]})


@tool(
    title="Delete message for everyone",
    toolset="messaging",
    kind="irreversible",
    idempotent=True,
    integrations=BAI,
)
async def delete_message_for_everyone(chat: Chat, message_id: MessageId) -> str:
    """Delete a message this number sent from the chat for every participant (WhatsApp Web instances only).

    Only messages sent from this number can be deleted. Recipients may already have read the message, and it cannot
    be restored afterwards. WhatsApp limits how long after sending this works. Returns deleted_for_everyone and
    message_id.
    """
    conn, client = await context.resolve()
    chat_jid = calls.chat(chat)
    row = await _stored_message(client, conn.identity, chat_jid, message_id)
    key = row["key"]
    if not key.get("fromMe"):
        raise ToolExecutionError("Only messages sent from this number can be deleted for everyone.")
    body: dict = {"id": key["id"], "remoteJid": key["remoteJid"], "fromMe": True}
    if key.get("participant"):
        body["participant"] = key["participant"]
    await calls.call(client, conn.identity, "DELETE", "chat/deleteMessageForEveryone", json=body, write=True)
    return tool_result({"deleted_for_everyone": True, "message_id": key["id"]})
