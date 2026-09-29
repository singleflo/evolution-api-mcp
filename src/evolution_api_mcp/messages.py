"""Reading Evolution message rows: timestamps, types, text, projection and the findMessages wrapper."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from evolution_api_mcp.context import instance_path

if TYPE_CHECKING:
    from evolution_api_mcp.client import EvolutionClient
    from evolution_api_mcp.context import InstanceIdentity

_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

_TYPE_BY_RAW = {
    "conversation": "text",
    "extendedTextMessage": "text",
    "imageMessage": "image",
    "videoMessage": "video",
    "ptvMessage": "video_note",
    "documentMessage": "document",
    "documentWithCaptionMessage": "document",
    "stickerMessage": "sticker",
    "locationMessage": "location",
    "liveLocationMessage": "location",
    "contactMessage": "contact",
    "contactsArrayMessage": "contact",
    "pollCreationMessage": "poll",
    "pollCreationMessageV2": "poll",
    "pollCreationMessageV3": "poll",
    "reactionMessage": "reaction",
    "protocolMessage": "system",
    "buttonsResponseMessage": "response",
    "listResponseMessage": "response",
    "templateButtonReplyMessage": "response",
    "interactiveResponseMessage": "response",
    "templateMessage": "template",
}

_POLL_KEYS = ("pollCreationMessage", "pollCreationMessageV2", "pollCreationMessageV3")
_MEDIA_TYPES = frozenset({"image", "video", "video_note", "audio", "voice_note", "document", "sticker"})
_MEDIA_KEYS = (
    "imageMessage",
    "videoMessage",
    "ptvMessage",
    "audioMessage",
    "documentMessage",
    "stickerMessage",
)


def ts_seconds(value: object) -> int | None:
    """Unix seconds from an int/float, a numeric string or a protobuf Long ``{"low", "high"}``."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) else None
    if isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
        return int(number) if math.isfinite(number) else None
    if isinstance(value, dict) and "low" in value and "high" in value:
        low, high = value["low"], value["high"]
        if isinstance(low, int) and isinstance(high, int) and not isinstance(low, bool) and not isinstance(high, bool):
            return (high << 32) | (low & 0xFFFFFFFF)
    return None


def iso(ts: object) -> str | None:
    """ISO-8601 UTC string (``2026-09-29T21:04:05Z``) of a timestamp value, or ``None``."""
    seconds = ts_seconds(ts)
    if seconds is None:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime(_TIME_FORMAT)
    except (OverflowError, OSError, ValueError):
        return None


def chat_key_filter(chat_jid: str) -> dict:
    """Key filter for one chat. Both keys are sent: Baileys ORs ``remoteJid`` with ``remoteJidAlt`` and an
    absent ``remoteJidAlt`` becomes an empty object that matches every message."""
    return {"remoteJid": chat_jid, "remoteJidAlt": chat_jid}


def _format_time(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime(_TIME_FORMAT)


def time_window(since: datetime | None, until: datetime | None) -> dict | None:
    """``messageTimestamp`` filter with both ends set (Evolution ignores the filter unless both are present)."""
    if since is None and until is None:
        return None
    return {
        "gte": _format_time(since if since is not None else _EPOCH),
        "lte": _format_time(until if until is not None else datetime.now(timezone.utc) + timedelta(days=1)),
    }


def _dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def normalize_type(message_type: str | None, message: dict | None) -> str:
    """Stable, readable message type from Evolution's ``messageType``."""
    if message_type == "audioMessage":
        return "voice_note" if _dict(_dict(message).get("audioMessage")).get("ptt") else "audio"
    return _TYPE_BY_RAW.get(message_type or "", "other")


def _text_of(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def extract_text(message: dict | None) -> str | None:
    """Human-readable text or caption of a stored message, or ``None`` when it carries none."""
    msg = _dict(message)
    candidates: list[object] = [
        msg.get("conversation"),
        _dict(msg.get("extendedTextMessage")).get("text"),
        _dict(msg.get("imageMessage")).get("caption"),
        _dict(msg.get("videoMessage")).get("caption"),
        _dict(msg.get("documentMessage")).get("caption"),
        _dict(_dict(_dict(msg.get("documentWithCaptionMessage")).get("message")).get("documentMessage")).get("caption"),
        _dict(msg.get("buttonsResponseMessage")).get("selectedDisplayText"),
        _dict(msg.get("listResponseMessage")).get("title"),
        _dict(msg.get("templateButtonReplyMessage")).get("selectedDisplayText"),
        _dict(_dict(msg.get("interactiveResponseMessage")).get("body")).get("text"),
        *(_dict(msg.get(key)).get("name") for key in _POLL_KEYS),
        _dict(msg.get("reactionMessage")).get("text"),
        msg.get("speechToText"),
    ]
    for candidate in candidates:
        text = _text_of(candidate)
        if text is not None:
            return text
    return None


def _media_payload(message: dict) -> dict:
    for key in _MEDIA_KEYS:
        payload = message.get(key)
        if isinstance(payload, dict):
            return payload
    wrapped = _dict(_dict(message.get("documentWithCaptionMessage")).get("message")).get("documentMessage")
    return _dict(wrapped)


def _context_info(row: dict, message: dict) -> dict:
    info = row.get("contextInfo")
    if isinstance(info, dict) and info:
        return info
    for key, value in message.items():
        if isinstance(value, dict) and isinstance(value.get("contextInfo"), dict):
            return value["contextInfo"]
        if key == "documentWithCaptionMessage":
            inner = _dict(_dict(value).get("message")).get("documentMessage")
            if isinstance(inner, dict) and isinstance(inner.get("contextInfo"), dict):
                return inner["contextInfo"]
    return {}


def _compact(values: dict) -> dict:
    return {key: value for key, value in values.items() if value is not None}


def _last_status(row: dict) -> str | None:
    updates = row.get("MessageUpdate")
    if isinstance(updates, list):
        for update in reversed(updates):
            status = _dict(update).get("status")
            if isinstance(status, str) and status:
                return status
    status = row.get("status")
    return status if isinstance(status, str) and status else None


def project_message(row: dict, *, text_limit: int | None = 1500) -> dict:
    """Compact, secret-free view of one stored message row (keys with no value are omitted)."""
    key = _dict(row.get("key"))
    message = _dict(row.get("message"))
    raw_type = row.get("messageType") if isinstance(row.get("messageType"), str) else None
    kind = normalize_type(raw_type, message)
    context = _context_info(row, message)

    from_me = key.get("fromMe")
    from_me = from_me if isinstance(from_me, bool) else None
    chat_id = key.get("remoteJid") if isinstance(key.get("remoteJid"), str) else None

    if from_me:
        sender: str | None = "me"
    elif chat_id is not None and chat_id.endswith("@g.us"):
        sender = key.get("participant") or context.get("participant") or chat_id
    else:
        sender = chat_id

    push_name = row.get("pushName")
    sender_name = None
    if isinstance(push_name, str) and push_name and not from_me and push_name != "Você":
        sender_name = push_name

    out: dict = {
        "message_id": key.get("id"),
        "chat_id": chat_id,
        "from_me": from_me,
        "sender": sender,
        "sender_name": sender_name,
        "timestamp": iso(row.get("messageTimestamp")),
        "type": kind,
    }
    if kind == "other":
        out["raw_type"] = raw_type

    text = extract_text(message)
    if text is not None:
        if text_limit is not None and len(text) > text_limit:
            out["text"] = text[:text_limit]
            out["text_truncated"] = True
        else:
            out["text"] = text

    if kind in _MEDIA_TYPES:
        payload = _media_payload(message)
        media = _compact(
            {
                "mimetype": payload.get("mimetype"),
                "file_name": payload.get("fileName"),
                "size_bytes": ts_seconds(payload.get("fileLength")),
                "seconds": payload.get("seconds"),
            }
        )
        if media:
            out["media"] = media
    elif kind == "location":
        payload = _dict(message.get("locationMessage")) or _dict(message.get("liveLocationMessage"))
        location = _compact(
            {
                "latitude": payload.get("degreesLatitude"),
                "longitude": payload.get("degreesLongitude"),
                "name": payload.get("name"),
                "address": payload.get("address"),
            }
        )
        if location:
            out["location"] = location
    elif kind == "contact":
        payload = _dict(message.get("contactMessage")) or _dict(message.get("contactsArrayMessage"))
        name = payload.get("displayName")
        if name is not None:
            out["contact"] = {"name": name}
    elif kind == "poll":
        payload = next((_dict(message[name]) for name in _POLL_KEYS if isinstance(message.get(name), dict)), {})
        raw_options = payload.get("options")
        options = [
            _dict(option).get("optionName")
            for option in (raw_options if isinstance(raw_options, list) else [])
            if _dict(option).get("optionName") is not None
        ]
        out["poll"] = _compact({"question": payload.get("name"), "options": options})
    elif kind == "reaction":
        payload = _dict(message.get("reactionMessage"))
        out["reaction"] = _compact(
            {"emoji": payload.get("text"), "target_message_id": _dict(payload.get("key")).get("id")}
        )

    out["quoted_message_id"] = context.get("stanzaId")
    out["status"] = _last_status(row)
    return {name: value for name, value in out.items() if value is not None}


async def fetch_page(
    client: EvolutionClient,
    identity: InstanceIdentity,
    *,
    where: dict,
    page_size: int,
    page: int,
) -> dict:
    """One page of ``chat/findMessages``, newest first. ``page_size`` is Evolution's ``offset`` parameter."""
    response = await client.request(
        "POST",
        instance_path(identity, "chat/findMessages"),
        json={"where": where, "offset": page_size, "page": page},
    )
    block = _dict(_dict(response).get("messages"))
    records = block.get("records")
    return {
        "total": block.get("total") or 0,
        "pages": block.get("pages") or 0,
        "page": block.get("currentPage") or page,
        "records": records if isinstance(records, list) else [],
    }


async def find_message(
    client: EvolutionClient,
    identity: InstanceIdentity,
    message_id: str,
    chat_jid: str | None = None,
) -> dict | None:
    """The stored row of a message id (optionally within one chat), or ``None``."""
    key: dict = {"id": message_id}
    if chat_jid is not None:
        key |= chat_key_filter(chat_jid)
    page = await fetch_page(client, identity, where={"key": key}, page_size=1, page=1)
    return page["records"][0] if page["records"] else None


async def latest_message(client: EvolutionClient, identity: InstanceIdentity, chat_jid: str) -> dict | None:
    """The newest stored row of a chat, or ``None`` when the chat has no stored messages."""
    page = await fetch_page(client, identity, where={"key": chat_key_filter(chat_jid)}, page_size=1, page=1)
    return page["records"][0] if page["records"] else None
