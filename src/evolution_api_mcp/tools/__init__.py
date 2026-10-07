"""Tool modules, one per toolset, and the parameter types they share.

The shared aliases carry the schema (bounds and description) only. A tool that makes a parameter optional writes the
default in its own signature (`reply_to_message_id: ReplyTo = None`), so the function stays callable from Python
without it.
"""

import importlib
import pkgutil
from typing import Annotated

from pydantic import Field

Chat = Annotated[
    str,
    Field(
        min_length=2,
        max_length=128,
        description=(
            "Chat: an international phone number (country code first, e.g. 393331234567 or +39 333 123 4567), a "
            "chat_id from list_chats or find_chats (…@s.whatsapp.net, …@g.us, …@lid), or a contact or group name. A "
            "name shared by several chats is refused with their chat_ids; tools that send or change something accept "
            "only an exact name."
        ),
    ),
]
Group = Annotated[
    str,
    Field(
        min_length=2,
        max_length=128,
        description="Group: its chat_id (…@g.us), its digits, or its exact name, from list_groups or find_chats.",
    ),
]
MessageId = Annotated[
    str,
    Field(
        min_length=6,
        max_length=128,
        description="Message id from read_messages, search_messages or list_chats.",
    ),
]
ReplyTo = Annotated[
    str | None,
    Field(
        description=(
            "Quote this earlier message (message id). When chat is omitted the message is sent in the chat of the "
            "quoted message; when both are given the message must belong to that chat."
        )
    ),
]
Mention = Annotated[
    list[str] | None,
    Field(
        max_length=50,
        description="Phone numbers or exact contact names to @-mention. WhatsApp Web (Baileys) instances only.",
    ),
]
MentionEveryone = Annotated[
    bool,
    Field(description="Mention every group participant. WhatsApp Web (Baileys) instances only."),
]
DelayMs = Annotated[
    int | None,
    Field(
        ge=0,
        le=20000,
        description=(
            "Milliseconds of 'typing…' shown before the message is sent. Default: the server's pacing (1200 ms)."
        ),
    ),
]
MediaUrl = Annotated[
    str,
    Field(
        min_length=10,
        max_length=2048,
        pattern=r"^https?://",
        description="Public http(s) URL Evolution downloads the file from.",
    ),
]


def load_all() -> None:
    """Import every tool module so its `@registry.tool` decorators run."""
    for module in pkgutil.iter_modules(__path__):
        importlib.import_module(f"{__name__}.{module.name}")
