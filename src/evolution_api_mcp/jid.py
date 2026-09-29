"""WhatsApp JID helpers: a port of Evolution's ``createJid`` plus input normalisation for tools."""

from __future__ import annotations

import re

_UNSUPPORTED_CHAT = (
    "Unsupported chat id '{value}'. Use an international phone number (country code first) "
    "or a chat id from list_chats."
)
_ACCEPTED_SUFFIXES = ("@s.whatsapp.net", "@g.us", "@lid")
_BR_NUMBER = re.compile(r"^(\d{2})(\d{2})\d{1}(\d{8})$", re.ASCII)
_PLAIN_DIGITS = re.compile(r"\d{7,15}", re.ASCII)
_GROUP_DIGITS = re.compile(r"\d+(?:-\d+)*", re.ASCII)
_INVITE_CODE = re.compile(r"[A-Za-z0-9_-]{22}", re.ASCII)
_INVITE_URL = re.compile(r"https://chat\.whatsapp\.com/([A-Za-z0-9_-]{22})(?:[/?#].*)?", re.ASCII)


def _format_mx_or_ar_number(jid: str) -> str:
    country_code = jid[:2]
    if country_code in ("52", "54"):
        if len(jid) == 13:
            return country_code + jid[3:]
        return jid
    return jid


def _format_br_number(jid: str) -> str:
    match = _BR_NUMBER.fullmatch(jid)
    if match is None:
        return jid
    if match.group(1) != "55":
        return jid
    joker = int(match.group(3)[0])
    ddd = int(match.group(2))
    if joker < 7 or ddd < 31:
        return match.group(0)
    return match.group(1) + match.group(2) + match.group(3)


def create_jid(number: str) -> str:
    """Port of Evolution's ``createJid`` (src/utils/createJid.ts), step by step."""
    number = re.sub(r":\d+", "", number, count=1)

    if "@g.us" in number or "@s.whatsapp.net" in number or "@lid" in number:
        return number

    if "@broadcast" in number:
        return number

    number = re.sub(r"\s", "", number)
    number = number.replace("+", "").replace("(", "").replace(")", "")
    number = number.split(":")[0].split("@")[0]

    if "-" in number and len(number) >= 24:
        number = re.sub(r"[^\d-]", "", number, flags=re.ASCII)
        return f"{number}@g.us"

    number = re.sub(r"\D", "", number, flags=re.ASCII)

    if len(number) >= 18:
        number = re.sub(r"[^\d-]", "", number, flags=re.ASCII)
        return f"{number}@g.us"

    number = _format_mx_or_ar_number(number)
    number = _format_br_number(number)

    return f"{number}@s.whatsapp.net"


def normalize_chat(chat: str) -> str:
    """Turn user input (phone number or chat id) into a JID; raise ValueError with a usable message."""
    value = chat.strip()
    if "@" in value:
        cleaned = re.sub(r":\d+(?=@)", "", value, count=1, flags=re.ASCII)
        local = cleaned.split("@", 1)[0]
        if local and any(cleaned.endswith(suffix) for suffix in _ACCEPTED_SUFFIXES) and cleaned.count("@") == 1:
            return cleaned
        raise ValueError(_UNSUPPORTED_CHAT.format(value=value))
    digits = re.sub(r"[\s+()\-.]", "", value)
    if not _PLAIN_DIGITS.fullmatch(digits):
        raise ValueError(_UNSUPPORTED_CHAT.format(value=value))
    return create_jid(digits)


def normalize_group(group: str) -> str:
    """Accept ``1203…`` or ``1203…@g.us`` and return ``1203…@g.us``; raise ValueError otherwise."""
    value = group.strip()
    bare = value[: -len("@g.us")] if value.endswith("@g.us") else value
    if not _GROUP_DIGITS.fullmatch(bare):
        raise ValueError(f"Unsupported group id '{value}'. Use a group_id from list_groups (…@g.us).")
    return f"{bare}@g.us"


def phone_of(jid: str) -> str | None:
    """Digits of a ``…@s.whatsapp.net`` JID; ``None`` for groups, @lid ids and anything else."""
    suffix = "@s.whatsapp.net"
    if not jid.endswith(suffix):
        return None
    local = jid[: -len(suffix)].split(":", 1)[0]
    if not local or not local.isascii() or not local.isdigit():
        return None
    return local


def is_group(jid: str) -> bool:
    return jid.endswith("@g.us")


def parse_invite(invite: str) -> str:
    """Return the 22-character invite code from a bare code or a chat.whatsapp.com URL."""
    value = invite.strip()
    if _INVITE_CODE.fullmatch(value):
        return value
    match = _INVITE_URL.fullmatch(value)
    if match:
        return match.group(1)
    raise ValueError(
        f"Unsupported invite '{value}'. Use the 22-character invite code or a https://chat.whatsapp.com/<code> link."
    )
