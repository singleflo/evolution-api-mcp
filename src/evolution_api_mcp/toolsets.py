"""Toolsets: named groups of tools a connection can enable (the ms-365 "preset" idea)."""

TOOLSET_ORDER = (
    "instance",
    "messaging",
    "chats",
    "contacts",
    "groups",
    "labels",
    "profile",
    "status",
    "catalog",
    "templates",
    "settings",
    "events",
    "integrations",
)

TOOLSETS: dict[str, str] = {
    "instance": "Connection status, QR pairing, restart, logout and online presence of the instance.",
    "messaging": (
        "Send text, media, voice notes, locations, contact cards, polls, list and button messages; "
        "react, edit and delete sent messages."
    ),
    "chats": (
        "List chats, show the newest messages across chats, read and search message history, delivery status, "
        "read/unread and archive state, received media."
    ),
    "contacts": (
        "Find chats by name or number, check numbers on WhatsApp, profiles and profile pictures, block and unblock."
    ),
    "groups": "Group details, participants, invite links, creation, settings and membership.",
    "labels": "WhatsApp Business app labels on chats.",
    "profile": "The instance's own profile name, about text, picture and privacy settings.",
    "status": "Post status updates.",
    "catalog": "WhatsApp Business app product catalog and collections.",
    "templates": "WhatsApp Business Platform message templates: list, create, edit, delete and send.",
    "settings": "Instance behaviour settings and proxy.",
    "events": "Webhook and event-stream (WebSocket, RabbitMQ, NATS, SQS, Kafka, Pusher) configuration.",
    "integrations": (
        "Evolution chatbots (Evolution Bot, Typebot, OpenAI, Dify, Flowise, n8n, EvoAI), their sessions, and Chatwoot."
    ),
}

DEFAULT_TOOLSETS = frozenset({"messaging", "chats", "contacts"})

PRESETS: dict[str, frozenset[str]] = {"core": DEFAULT_TOOLSETS, "all": frozenset(TOOLSET_ORDER)}


def parse_toolsets(value: str | None) -> frozenset[str]:
    """Turn a comma list of toolset and preset names into the set of toolsets; blank means the default set.

    Raises `config.ConfigError` for an unknown name.
    """
    names = [part.strip().lower() for part in (value or "").split(",")]
    names = [name for name in names if name]
    if not names:
        return DEFAULT_TOOLSETS
    selected: set[str] = set()
    unknown: list[str] = []
    for name in names:
        if name in PRESETS:
            selected |= PRESETS[name]
        elif name in TOOLSETS:
            selected.add(name)
        else:
            unknown.append(name)
    if unknown:
        # Imported here because config imports this module.
        from evolution_api_mcp.config import ConfigError

        raise ConfigError(f"Unknown toolset(s): {', '.join(unknown)}. Valid: {', '.join(TOOLSET_ORDER)}, core, all.")
    return frozenset(selected)
