"""Messaging tools: send text, media, voice notes, locations, contacts, polls, lists, buttons; react, edit, delete."""

import base64
import re
import time
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

import anyio
from pydantic import BaseModel, Field, field_validator

from evolution_api_mcp import calls, context, directory, media, messages, ratelimit, sending
from evolution_api_mcp.context import Connection
from evolution_api_mcp.errors import ToolExecutionError, tool_result
from evolution_api_mcp.registry import ALL, BAILEYS, BUSINESS, tool
from evolution_api_mcp.tools import Chat, DelayMs, MediaUrl, Mention, MentionEveryone, MessageId, ReplyTo

BAI = frozenset({BAILEYS})
BAI_BUS = frozenset({BAILEYS, BUSINESS})

EDIT_WINDOW_SECONDS = 900
MAX_BATCH_BYTES = 314_572_800  # 300 MiB across the files of one send_local_files call
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
    chat: str | None,
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
    """The plan's send flow: pick the chat (or the chat of the replied message), build options, send, report."""
    identity = conn.identity
    chat_jid, quoted_row = await sending.reply_target(client, conn, chat, reply_to_message_id)
    number = sending.recipient(identity, chat_jid)
    options = await sending.build_options(
        client,
        identity,
        conn,
        delay_ms=delay_ms,
        quoted_row=quoted_row,
        mention=mention,
        mention_everyone=mention_everyone,
        link_preview=link_preview,
    )
    result = await sending.send(
        client, identity, conn, endpoint, {"number": number, **fields, **options}, chat_jid, timeout=timeout
    )
    return tool_result({**result, **(extra or {})})


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
    text: Annotated[str, Field(min_length=1, max_length=4096, description="Message text.")],
    chat: Chat | None = None,
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
    Wording and recipient come from the user. reply_to_message_id quotes an earlier message; chat may be omitted
    when reply_to_message_id is given, and the message is then sent in the chat of the message it answers.
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
    media_url: MediaUrl,
    media_type: Annotated[
        Literal["image", "video", "audio", "document"],
        Field(description="How the file is delivered: as an image, a video, an audio file or a document."),
    ],
    chat: Chat | None = None,
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

    Use send_voice_note for a voice message and send_local_files (local server only) for files on this computer.
    Audio files carry no caption. The message is delivered immediately and cannot be recalled by this server.
    Mentions work only on WhatsApp Web (Baileys) instances.
    chat may be omitted when reply_to_message_id is given: the message goes to the chat of the message it answers.
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
    title="Send local files",
    toolset="messaging",
    kind="destructive",
    idempotent=False,
    integrations=ALL,
    local_only=True,
)
async def send_local_files(
    paths: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=4096)]],
        Field(
            min_length=1,
            max_length=10,
            description="Absolute paths (or starting with ~) of 1 to 10 files inside the allowed folders.",
        ),
    ],
    chat: Chat | None = None,
    caption: Annotated[
        str | None, Field(max_length=1024, description="Text shown under the first file; the others carry none.")
    ] = None,
    send_as: Annotated[
        Literal["auto", "document", "voice_note"],
        Field(
            description=(
                "How every file is sent: auto picks image, video, audio or document from the file type; document "
                "always sends a document; voice_note sends audio files as voice messages."
            )
        ),
    ] = "auto",
    reply_to_message_id: ReplyTo = None,
    mention: Mention = None,
    mention_everyone: MentionEveryone = False,
    delay_ms: DelayMs = None,
) -> str:
    """Send 1 to 10 files from this computer to a person or group, one message per file.

    Available only on the local server. Every file must sit inside the folders in EVOLUTION_MCP_FILE_ROOTS, must not
    be a hidden file or inside a hidden folder, and may be at most 100 MiB; one call carries at most 300 MiB in
    total. All files are checked before the first one is sent, so a refused path sends nothing. Each file is uploaded
    to the Evolution server as base64 and counts as one change against the per-minute write limit. The caption, the
    quoted message (reply_to_message_id) and the mentions go with the first file only. Audio files carry no caption,
    and send_as voice_note needs audio files. chat may be omitted when reply_to_message_id is given: the files go to
    the chat of the message it answers. The messages are delivered immediately and cannot be recalled by this
    server. If a send fails part-way, the error names the files already sent and those not sent. Mentions work only
    on WhatsApp Web (Baileys) instances. Returns chat_id and files, each with file, size_bytes, message_id and
    status.
    """
    conn, client = await context.resolve()
    roots = context.local_config().file_roots
    plan: list[tuple[Path, str, str, str]] = []
    total = 0
    for index, path in enumerate(paths):
        file = media.resolve_local_file(path, roots)
        mimetype, kind = media.media_kind(file)
        if send_as == "voice_note":
            if kind != "audio":
                raise ToolExecutionError(f"send_as='voice_note' needs an audio file; {file.name} is {mimetype}.")
            if caption and index == 0:
                raise ToolExecutionError("Voice notes carry no caption; send the text separately.")
            endpoint, mediatype = "message/sendWhatsAppAudio", "audio"
        else:
            endpoint, mediatype = "message/sendMedia", "document" if send_as == "document" else kind
            if mediatype == "audio" and caption and index == 0:
                raise ToolExecutionError(_AUDIO_NO_CAPTION)
        total += file.stat().st_size
        plan.append((file, mimetype, endpoint, mediatype))
    if total > MAX_BATCH_BYTES:
        raise ToolExecutionError(
            f"These files total {total} bytes; one call sends at most {MAX_BATCH_BYTES} bytes (300 MiB). "
            "Nothing was sent."
        )

    identity = conn.identity
    chat_jid, quoted_row = await sending.reply_target(client, conn, chat, reply_to_message_id)
    number = sending.recipient(identity, chat_jid)
    first_options = await sending.build_options(
        client,
        identity,
        conn,
        delay_ms=delay_ms,
        quoted_row=quoted_row,
        mention=mention,
        mention_everyone=mention_everyone,
        link_preview=None,
    )
    ratelimit.charge(conn, writes=len(plan) - 1)

    async def send_one(index: int) -> dict:
        file, mimetype, endpoint, mediatype = plan[index]
        try:
            data, size = await anyio.to_thread.run_sync(_read_base64, file)
        except OSError as exc:
            reason = f"Could not read {file.name}: {exc.strerror or exc}."
            raise ToolExecutionError(f"{reason} Nothing was sent." if index == 0 else reason) from exc
        if endpoint == "message/sendWhatsAppAudio":
            fields: dict = {"audio": data}
        else:
            fields = {"mediatype": mediatype, "mimetype": mimetype, "fileName": file.name, "media": data}
            if caption and index == 0:
                fields["caption"] = caption
        options = first_options if index == 0 else {"delay": first_options["delay"]}
        result = await sending.send(
            client, identity, conn, endpoint, {"number": number, **fields, **options}, chat_jid, timeout=600
        )
        return {"file": file.name, "size_bytes": size, "message_id": result["message_id"], "status": result["status"]}

    sent: list[dict] = []
    for index in range(len(plan)):
        try:
            sent.append(await send_one(index))
        except ToolExecutionError as exc:
            if len(plan) == 1:
                raise
            done = ", ".join(entry["file"] for entry in sent) or "none"
            rest = ", ".join(item[0].name for item in plan[index + 1 :]) or "none"
            raise ToolExecutionError(
                f"Sent {done}. Sending {plan[index][0].name} failed: {exc} Not sent: {rest}."
            ) from exc
    return tool_result({"chat_id": chat_jid, "files": sent})


@tool(
    title="Send voice note",
    toolset="messaging",
    kind="destructive",
    idempotent=False,
    integrations=ALL,
)
async def send_voice_note(
    audio_url: MediaUrl,
    chat: Chat | None = None,
    reply_to_message_id: ReplyTo = None,
    delay_ms: DelayMs = None,
) -> str:
    """Send an audio file from a public URL as a voice message.

    Use send_media_message with media_type audio to send the file as a plain audio attachment. WhatsApp Business
    Platform instances deliver it as an audio file, not as a voice note. The message is delivered immediately and
    cannot be recalled by this server. Returns message_id, chat_id, status and timestamp.
    chat may be omitted when reply_to_message_id is given: the message goes to the chat of the message it answers.
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
    video_url: MediaUrl,
    chat: Chat | None = None,
    reply_to_message_id: ReplyTo = None,
    delay_ms: DelayMs = None,
) -> str:
    """Send a video from a public URL as a round video note (WhatsApp Web instances only).

    Use send_media_message with media_type video for an ordinary video attachment. The message is delivered
    immediately and cannot be recalled by this server. Returns message_id, chat_id, status and timestamp.
    chat may be omitted when reply_to_message_id is given: the message goes to the chat of the message it answers.
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
    sticker_url: MediaUrl,
    chat: Chat | None = None,
    reply_to_message_id: ReplyTo = None,
    delay_ms: DelayMs = None,
) -> str:
    """Send a sticker from a public image URL (WhatsApp Web instances only).

    The URL should point to a WebP sticker or an image Evolution can convert. The message is delivered immediately and
    cannot be recalled by this server. Returns message_id, chat_id, status and timestamp.
    chat may be omitted when reply_to_message_id is given: the message goes to the chat of the message it answers.
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
    latitude: Annotated[float, Field(ge=-90, le=90, description="Latitude in decimal degrees.")],
    longitude: Annotated[float, Field(ge=-180, le=180, description="Longitude in decimal degrees.")],
    name: Annotated[str, Field(min_length=1, max_length=256, description="Place name shown on the pin.")],
    address: Annotated[str, Field(min_length=1, max_length=512, description="Street address shown under the name.")],
    chat: Chat | None = None,
    reply_to_message_id: ReplyTo = None,
    delay_ms: DelayMs = None,
) -> str:
    """Send a map location pin with a name and address.

    The message is delivered immediately and cannot be recalled by this server.
    Returns message_id, chat_id, status and timestamp.
    chat may be omitted when reply_to_message_id is given: the message goes to the chat of the message it answers.
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
    contacts: Annotated[
        list[ContactCard], Field(min_length=1, max_length=10, description="Contacts to share (1 to 10).")
    ],
    chat: Chat | None = None,
    reply_to_message_id: ReplyTo = None,
    delay_ms: DelayMs = None,
) -> str:
    """Send one or more contact cards (name and phone number) to a person or group.

    The cards are delivered immediately and cannot be recalled by this server; the recipient sees the phone numbers
    shared, so only share numbers the user has approved. Returns message_id, chat_id, status and timestamp.
    chat may be omitted when reply_to_message_id is given: the message goes to the chat of the message it answers.
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
    chat: Chat | None = None,
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
    chat may be omitted when reply_to_message_id is given: the message goes to the chat of the message it answers.
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
    title: Annotated[str, Field(min_length=1, max_length=60, description="Heading of the message.")],
    description: Annotated[str, Field(min_length=1, max_length=1024, description="Body text of the message.")],
    button_text: Annotated[
        str, Field(min_length=1, max_length=20, description="Label of the button that opens the list.")
    ],
    sections: Annotated[
        list[ListSection], Field(min_length=1, max_length=10, description="Sections of the list (1 to 10).")
    ],
    chat: Chat | None = None,
    footer: Annotated[str, Field(max_length=60, description="Small text under the body. Default: none.")] = "",
    reply_to_message_id: ReplyTo = None,
    delay_ms: DelayMs = None,
) -> str:
    """Send a message with a button that opens a list of selectable rows.

    The person's choice comes back as a message that read_messages shows as a response. WhatsApp Business Platform
    instances accept at most 10 rows in total. Recipients' apps may not display list and button messages sent from
    WhatsApp Web (Baileys) instances. The message is delivered immediately and cannot be recalled by this server.
    Returns message_id, chat_id, status and timestamp.
    chat may be omitted when reply_to_message_id is given: the message goes to the chat of the message it answers.
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
    title: Annotated[str, Field(min_length=1, max_length=60, description="Heading of the message.")],
    description: Annotated[str, Field(min_length=1, max_length=1024, description="Body text of the message.")],
    buttons: Annotated[
        list[MessageButton], Field(min_length=1, max_length=3, description="Buttons under the message (1 to 3).")
    ],
    chat: Chat | None = None,
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
    chat may be omitted when reply_to_message_id is given: the message goes to the chat of the message it answers.
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


_FORWARD_MEDIA_TYPES = frozenset({"image", "video", "audio", "voice_note", "video_note", "document", "sticker"})
_FORWARDABLE_TYPES = _FORWARD_MEDIA_TYPES | {"text", "location", "contact"}
_FORWARD_NOTE = "WhatsApp shows these as new messages, without the Forwarded label."
_DEFAULT_MIMETYPE = {"image": "image/jpeg", "video": "video/mp4", "audio": "audio/mpeg"}
_VCARD_PROPERTY = re.compile(r"^(?:[\w-]+\.)?([A-Za-z-]+)")
_VCARD_WAID = re.compile(r"waid=(\d+)", re.IGNORECASE)


def _vcard_card(vcard: str, display_name: str | None) -> dict | None:
    """The `sendContact` entry of one vCard (name from FN, phone from the first TEL), or None without a phone."""
    name: str | None = None
    waid: str | None = None
    plain: str | None = None
    for line in vcard.replace("\r", "").split("\n"):
        head, separator, value = line.strip().partition(":")
        found = _VCARD_PROPERTY.match(head)
        if not separator or found is None:
            continue
        prop = found.group(1).upper()
        if prop == "FN" and name is None:
            name = value.strip() or None
        elif prop == "TEL":
            tagged = _VCARD_WAID.search(head)
            if tagged and waid is None:
                waid = tagged.group(1)
            digits = re.sub(r"\D", "", value)
            if digits and plain is None:
                plain = digits
    digits = waid or plain
    if digits is None:
        return None
    return {"fullName": name or display_name or f"+{digits}", "wuid": digits, "phoneNumber": f"+{digits}"}


def _contact_cards(message: dict, message_id: str) -> list[dict]:
    """The `sendContact` entries of a stored contact message, or a refusal when a card has no phone number."""
    single = message.get("contactMessage")
    array = message.get("contactsArrayMessage")
    sources: list[dict] = []
    if isinstance(single, dict):
        sources.append(single)
    elif isinstance(array, dict) and isinstance(array.get("contacts"), list):
        sources.extend(item for item in array["contacts"] if isinstance(item, dict))
    cards = []
    for source in sources:
        vcard = source.get("vcard")
        name = source.get("displayName")
        card = _vcard_card(vcard if isinstance(vcard, str) else "", name if isinstance(name, str) else None)
        if card is None:
            raise ToolExecutionError("This contact card carries no phone number to forward. Nothing was sent.")
        cards.append(card)
    if not cards:
        raise ToolExecutionError(f"Message {message_id} carries no contact card to forward. Nothing was sent.")
    return cards


@tool(title="Forward message", toolset="messaging", kind="destructive", idempotent=False, integrations=BAI_BUS)
async def forward_message(
    message_id: MessageId,
    to: Annotated[
        list[Annotated[str, Field(min_length=2, max_length=128)]],
        Field(
            min_length=1,
            max_length=5,
            description="Chats to forward to, 1 to 5: numbers, chat ids or exact contact or group names.",
        ),
    ],
    chat: Chat | None = None,
    delay_ms: DelayMs = None,
) -> str:
    """Forward a stored message to 1 to 5 chats by sending its text, media, location or contact again.

    WhatsApp shows each copy as a new message from this number, without the Forwarded label. Text, images, videos,
    audio files, voice notes, documents, locations and contact cards can be forwarded; video notes and stickers
    only on WhatsApp Web (Baileys) instances; deleted messages and other types are refused. Recipients are checked
    before anything is sent, duplicates are sent once, and each copy counts as one change against the per-minute
    write limit. chat only tells apart messages that share an id. Attachments are fetched from Evolution once (up
    to 100 MiB) and uploaded for every recipient. The copies are delivered immediately and cannot be recalled by
    this server. If a send fails part-way, the error names the chats already forwarded to and those not attempted.
    Returns source_message_id, type and forwarded, each entry with chat_id, chat_name when known, message_id and
    status.
    """
    conn, client = await context.resolve()
    identity = conn.identity
    source_chat = await calls.resolve_chat(client, conn, chat, purpose="send") if chat is not None else None
    row = await calls.stored_message(client, conn, message_id, source_chat, purpose="send")
    projected = messages.project_message(row, text_limit=None)
    kind = projected["type"]
    if projected.get("deleted"):
        raise ToolExecutionError(f"Message {message_id} was deleted and cannot be forwarded. Nothing was sent.")
    if kind not in _FORWARDABLE_TYPES:
        raise ToolExecutionError(
            f"Message {message_id} is a {kind}; text, media, location and contact messages can be forwarded. "
            "Nothing was sent."
        )
    if kind in {"video_note", "sticker"} and identity.integration != BAILEYS:
        raise ToolExecutionError(
            "Video notes and stickers can be forwarded only on WhatsApp Web (Baileys) instances. Nothing was sent."
        )

    endpoint = "message/sendMedia"
    fields: dict = {}
    timeout: float | None = None
    stored = row.get("message")
    if kind == "text":
        text = projected.get("text")
        if text is None:
            raise ToolExecutionError(f"Message {message_id} has no text to forward. Nothing was sent.")
        endpoint, fields = "message/sendText", {"text": text}
    elif kind == "location":
        place = projected.get("location") or {}
        latitude, longitude = place.get("latitude"), place.get("longitude")
        if latitude is None or longitude is None:
            raise ToolExecutionError(f"Message {message_id} carries no coordinates to forward. Nothing was sent.")
        endpoint = "message/sendLocation"
        fields = {
            "latitude": latitude,
            "longitude": longitude,
            "name": place.get("name") or "Location",
            "address": place.get("address") or f"{latitude}, {longitude}",
        }
    elif kind == "contact":
        cards = _contact_cards(stored if isinstance(stored, dict) else {}, message_id)
        endpoint, fields = "message/sendContact", {"contact": cards}

    recipients: list[tuple[str, str]] = []
    for value in to:
        chat_id = await calls.resolve_chat(client, conn, value, purpose="send")
        if all(chat_id != known for known, _ in recipients):
            recipients.append((chat_id, sending.recipient(identity, chat_id)))
    names = None if all(calls.is_literal_chat(value) for value in to) else await directory.get(client, conn)

    if kind in _FORWARD_MEDIA_TYPES:
        _, answer, _ = await calls.fetch_media(
            client,
            conn,
            message_id,
            source_chat,
            allowed=_FORWARD_MEDIA_TYPES,
            not_allowed="Message {id} is a {type} message and carries no media to forward. Nothing was sent.",
            purpose="send",
            row=row,
        )
        encoded = answer["base64"]
        size = calls.decoded_size(encoded)
        if size > media.MAX_FILE_BYTES:
            raise ToolExecutionError(
                f"The file is {size} bytes; the limit is 104857600 bytes (100 MiB). Nothing was sent."
            )
        details = projected.get("media") or {}
        mimetype = answer.get("mimetype") or details.get("mimetype") or _DEFAULT_MIMETYPE.get(kind)
        timeout = 600
        if kind == "voice_note":
            endpoint, fields = "message/sendWhatsAppAudio", {"audio": encoded}
        elif kind == "video_note":
            endpoint, fields = "message/sendPtv", {"video": encoded}
        elif kind == "sticker":
            endpoint, fields = "message/sendSticker", {"sticker": encoded}
        else:
            fields = {"mediatype": kind, "media": encoded}
            if mimetype:
                fields["mimetype"] = mimetype
            if kind == "document":
                fields["fileName"] = answer.get("fileName") or details.get("file_name") or "document"
            caption = projected.get("text") or answer.get("caption")
            if caption and kind != "audio":
                fields["caption"] = caption

    options = await sending.build_options(
        client,
        identity,
        conn,
        delay_ms=delay_ms,
        quoted_row=None,
        mention=None,
        mention_everyone=False,
        link_preview=None,
    )
    ratelimit.charge(conn, writes=len(recipients) - 1)

    forwarded: list[dict] = []
    for index, (chat_id, number) in enumerate(recipients):
        try:
            result = await sending.send(
                client, identity, conn, endpoint, {"number": number, **fields, **options}, chat_id, timeout=timeout
            )
        except ToolExecutionError as exc:
            if len(recipients) == 1:
                raise
            done = ", ".join(entry["chat_id"] for entry in forwarded) or "none"
            rest = ", ".join(pending for pending, _ in recipients[index + 1 :]) or "none"
            raise ToolExecutionError(
                f"Forwarded to {done}. Forwarding to {chat_id} failed: {exc} Not attempted: {rest}."
            ) from exc
        forwarded.append(
            {
                "chat_id": chat_id,
                "chat_name": names.name_of(chat_id) if names is not None else None,
                "message_id": result["message_id"],
                "status": result["status"],
            }
        )
    return tool_result(
        {
            "source_message_id": message_id,
            "type": kind,
            "forwarded": [{k: v for k, v in entry.items() if v is not None} for entry in forwarded],
            "note": _FORWARD_NOTE,
        }
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
    chat_jid = await calls.resolve_chat(client, conn, chat, purpose="send")
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
    message_id: MessageId,
    emoji: Annotated[
        str,
        Field(max_length=16, description="A single emoji. An empty string removes the reaction."),
    ],
    chat: Chat | None = None,
) -> str:
    """React to a message with an emoji, or remove the reaction with an empty emoji.

    The person or group sees the reaction. A message can carry one reaction from this number, so reacting again
    replaces the earlier one. chat may be omitted: the message id then finds the chat, and an id shared by several
    chats is refused with their chat_ids. Returns message_id, chat_id, status and timestamp of the reaction, plus
    reacted_to and emoji.
    """
    conn, client = await context.resolve()
    chat_jid = await calls.resolve_chat(client, conn, chat, purpose="send") if chat is not None else None
    row = await calls.stored_message(client, conn, message_id, chat_jid, purpose="send")
    sent = await sending.send(
        client,
        conn.identity,
        conn,
        "message/sendReaction",
        {"key": sending.reply_key(row)["key"], "reaction": emoji},
        chat_jid or row["key"]["remoteJid"],
    )
    return tool_result({**sent, "reacted_to": message_id, "emoji": emoji})


@tool(title="Edit sent message", toolset="messaging", kind="destructive", idempotent=True, integrations=BAI)
async def edit_message(
    message_id: MessageId,
    new_text: Annotated[str, Field(min_length=1, max_length=4096, description="The replacement text.")],
    chat: Chat | None = None,
) -> str:
    """Replace the text of a message this number sent, or the caption of its image or video.

    WhatsApp allows edits for 15 minutes after sending, and only on messages sent from this number. Everyone in the
    chat sees the edited text and that it was edited. chat may be omitted: the message id then finds the chat, and an
    id shared by several chats is refused with their chat_ids. Returns edited, message_id and chat_id.
    """
    conn, client = await context.resolve()
    identity = conn.identity
    requested = await calls.resolve_chat(client, conn, chat, purpose="send") if chat is not None else None
    row = await calls.stored_message(client, conn, message_id, requested, purpose="send")
    chat_jid = requested or row["key"]["remoteJid"]
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
async def delete_message_for_everyone(message_id: MessageId, chat: Chat | None = None) -> str:
    """Delete a message this number sent from the chat for every participant (WhatsApp Web instances only).

    Only messages sent from this number can be deleted. Recipients may already have read the message, and it cannot
    be restored afterwards. WhatsApp limits how long after sending this works. chat may be omitted: the message id
    then finds the chat, and an id shared by several chats is refused with their chat_ids. Returns
    deleted_for_everyone and message_id.
    """
    conn, client = await context.resolve()
    chat_jid = await calls.resolve_chat(client, conn, chat, purpose="write") if chat is not None else None
    row = await calls.stored_message(client, conn, message_id, chat_jid, purpose="write")
    key = row["key"]
    if not key.get("fromMe"):
        raise ToolExecutionError("Only messages sent from this number can be deleted for everyone.")
    body: dict = {"id": key["id"], "remoteJid": key["remoteJid"], "fromMe": True}
    if key.get("participant"):
        body["participant"] = key["participant"]
    await calls.call(client, conn.identity, "DELETE", "chat/deleteMessageForEveryone", json=body, write=True)
    return tool_result({"deleted_for_everyone": True, "message_id": key["id"]})
