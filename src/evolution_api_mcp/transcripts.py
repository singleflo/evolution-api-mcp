"""Chat export transcripts: Markdown, JSON and WhatsApp-style text, rendered from projected messages.

Every function takes the messages `messages.project_rows` produced (oldest first, text uncut) and `media_files`, the
path of each exported attachment relative to the transcript file, keyed by message id. Times are cut from the
projected ISO timestamps, which are already in the display zone.
"""

import json
from collections.abc import Mapping, Sequence
from urllib.parse import quote

from evolution_api_mcp.directory import Directory

ATTACHMENT_TYPES = frozenset({"image", "video", "video_note", "audio", "voice_note", "document", "sticker"})
TRANSCRIPT_FILES = {"markdown": "chat.md", "json": "chat.json", "whatsapp_txt": "chat.txt"}

_LRM = "\u200e"  # WhatsApp's own exports start attachment and system lines with it
_DELETED_MARKDOWN = "(message deleted)"
_DELETED_TEXT = f"{_LRM}This message was deleted."
_OWN_NAME = "Me"


def _timestamp(message: dict) -> str:
    value = message.get("timestamp")
    return value if isinstance(value, str) else ""


def _sender(message: dict, names: Directory, own_name: str) -> str:
    """Who wrote a message: this number's own name, else the known name, else `+digits`, else the jid."""
    if message.get("from_me"):
        return own_name
    if isinstance(name := message.get("sender_name"), str) and name:
        return name
    sender = message.get("sender")
    if not isinstance(sender, str) or not sender:
        return "unknown"
    digits = names.phone_of(sender)
    return f"+{digits}" if digits else sender


def _described(message: dict) -> str | None:
    """A one-line stand-in for a message that carries no text of its own."""
    kind = message.get("type")
    if kind == "location":
        place = message.get("location")
        place = place if isinstance(place, dict) else {}
        coordinates = ", ".join(str(place[key]) for key in ("latitude", "longitude") if key in place)
        return "(location: " + " ".join(part for part in (place.get("name"), coordinates) if part) + ")"
    if kind == "contact":
        card = message.get("contact")
        name = card.get("name") if isinstance(card, dict) else None
        return f"(contact: {name})" if name else "(contact)"
    if kind == "poll":
        poll = message.get("poll")
        poll = poll if isinstance(poll, dict) else {}
        options = poll.get("options")
        shown = " / ".join(str(option) for option in options) if isinstance(options, list) else ""
        return "(poll: " + " - ".join(part for part in (poll.get("question"), shown) if part) + ")"
    if kind == "reaction":
        reaction = message.get("reaction")
        emoji = reaction.get("emoji") if isinstance(reaction, dict) else None
        return f"(reaction: {emoji})" if emoji else None
    if kind in ATTACHMENT_TYPES:
        media = message.get("media")
        file_name = media.get("file_name") if isinstance(media, dict) else None
        label = str(kind).replace("_", " ")
        return f"({label}: {file_name})" if file_name else f"({label})"
    return None


def _first_last(messages: Sequence[dict]) -> str:
    stamps = [stamp for message in messages if (stamp := _timestamp(message))]
    return f"{stamps[0]} \u2013 {stamps[-1]} \u00b7 " if stamps else ""


def _quoted_sender(who: str, names: Directory, own_name: str) -> str:
    """A quoted sender as the directory knows it: this number's own name, `+digits` for an unnamed jid, else as is."""
    if who in names.me:
        return own_name
    if "@" in who and (digits := names.phone_of(who)):
        return f"+{digits}"
    return who


def _markdown_message(message: dict, names: Directory, media_files: Mapping[str, str]) -> str:
    stamp = _timestamp(message)
    sender = _sender(message, names, _OWN_NAME)
    head = f"**{stamp[11:16] or '--:--'}** {sender}: "
    if message.get("deleted"):
        return head + _DELETED_MARKDOWN
    text = message.get("text")
    lines = [head + (text if isinstance(text, str) and text else _described(message) or "")]
    quoted = message.get("quoted")
    if isinstance(quoted, dict):
        who = quoted.get("sender")
        said = str(quoted.get("text", "")).replace("\n", " ")
        shown = _quoted_sender(who, names, _OWN_NAME) if isinstance(who, str) and who else ""
        lines.append("> " + (": ".join(part for part in (shown, said) if part) or "(quoted message)"))
    path = media_files.get(str(message.get("message_id")))
    if path is not None:
        lines.append(f"Attachment: [{path.rsplit('/', 1)[-1]}]({quote(path)})")
    reactions = message.get("reactions")
    if isinstance(reactions, list) and reactions:
        lines.append("Reactions: " + ", ".join(f"{item['emoji']} {item['by']}" for item in reactions))
    return "\n".join(lines)


def render_markdown(
    chat_id: str,
    chat_name: str | None,
    exported_at: str,
    messages: Sequence[dict],
    names: Directory,
    media_files: Mapping[str, str],
) -> str:
    """`chat.md`: a heading, a summary line, then the messages under one `## YYYY-MM-DD` heading per day."""
    parts = [
        f"# {chat_name or chat_id}",
        f"Chat {chat_id} \u00b7 exported {exported_at} \u00b7 {_first_last(messages)}{len(messages)} messages",
    ]
    day = None
    for message in messages:
        message_day = _timestamp(message)[:10] or "unknown date"
        if message_day != day:
            day = message_day
            parts.append(f"## {day}")
        parts.append(_markdown_message(message, names, media_files))
    return "\n\n".join(parts) + "\n"


def render_json(
    chat_id: str,
    chat_name: str | None,
    exported_at: str,
    since: str | None,
    until: str | None,
    time_zone: str,
    messages: Sequence[dict],
    media_files: Mapping[str, str],
) -> str:
    """`chat.json`: the chat, the period, the zone and every projected message with its `media_file`."""
    shown = []
    for message in messages:
        path = media_files.get(str(message.get("message_id")))
        shown.append({**message, "media_file": path} if path is not None else message)
    document = {
        "chat_id": chat_id,
        "chat_name": chat_name,
        "exported_at": exported_at,
        "since": since,
        "until": until,
        "time_zone": time_zone,
        "messages": shown,
    }
    return json.dumps(document, ensure_ascii=False, indent=2) + "\n"


def _txt_message(message: dict, names: Directory, own_name: str, media_files: Mapping[str, str]) -> list[str]:
    stamp = _timestamp(message)
    when = f"{stamp[8:10] or '??'}/{stamp[5:7] or '??'}/{stamp[2:4] or '??'}, {stamp[11:19] or '??:??:??'}"
    head = f"[{when}] {_sender(message, names, own_name)}: "
    if message.get("deleted"):
        return [head + _DELETED_TEXT]
    kind = message.get("type")
    text = message.get("text")
    lines = text.split("\n") if isinstance(text, str) and text else []
    path = media_files.get(str(message.get("message_id")))
    if path is not None:
        lines.insert(0, f"{_LRM}<attached: {path.rsplit('/', 1)[-1]}>")
    elif kind in ATTACHMENT_TYPES:
        lines.insert(0, f"{_LRM}{str(kind).replace('_', ' ')} omitted")
    elif not lines and (described := _described(message)):
        lines = [described]
    lines = lines or [""]
    return [head + lines[0], *lines[1:]]


def render_whatsapp_txt(
    messages: Sequence[dict], names: Directory, media_files: Mapping[str, str], own_name: str | None
) -> str:
    """`chat.txt` in WhatsApp's own English export format; quotes and reactions have no place in it."""
    lines: list[str] = []
    for message in messages:
        lines.extend(_txt_message(message, names, own_name or _OWN_NAME, media_files))
    return "\n".join(lines) + "\n" if lines else ""
