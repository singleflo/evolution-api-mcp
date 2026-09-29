"""Events toolset: webhook and event-stream (WebSocket, RabbitMQ, NATS, SQS, Kafka, Pusher) configuration."""

from typing import Annotated, Literal

from pydantic import Field

from evolution_api_mcp import calls, context, redact, registry
from evolution_api_mcp.errors import ToolExecutionError, tool_result

EVENT_NAMES = (
    "APPLICATION_STARTUP",
    "QRCODE_UPDATED",
    "MESSAGES_SET",
    "MESSAGES_UPSERT",
    "MESSAGES_EDITED",
    "MESSAGES_UPDATE",
    "MESSAGES_DELETE",
    "SEND_MESSAGE",
    "SEND_MESSAGE_UPDATE",
    "CONTACTS_SET",
    "CONTACTS_UPSERT",
    "CONTACTS_UPDATE",
    "PRESENCE_UPDATE",
    "CHATS_SET",
    "CHATS_UPSERT",
    "CHATS_UPDATE",
    "CHATS_DELETE",
    "GROUPS_UPSERT",
    "GROUP_UPDATE",
    "GROUP_PARTICIPANTS_UPDATE",
    "CONNECTION_UPDATE",
    "LABELS_EDIT",
    "LABELS_ASSOCIATION",
    "CALL",
    "TYPEBOT_START",
    "TYPEBOT_CHANGE_STATUS",
    "REMOVE_INSTANCE",
    "LOGOUT_INSTANCE",
    "INSTANCE_CREATE",
    "INSTANCE_DELETE",
    "STATUS_INSTANCE",
)

EventName = Literal[
    "APPLICATION_STARTUP",
    "QRCODE_UPDATED",
    "MESSAGES_SET",
    "MESSAGES_UPSERT",
    "MESSAGES_EDITED",
    "MESSAGES_UPDATE",
    "MESSAGES_DELETE",
    "SEND_MESSAGE",
    "SEND_MESSAGE_UPDATE",
    "CONTACTS_SET",
    "CONTACTS_UPSERT",
    "CONTACTS_UPDATE",
    "PRESENCE_UPDATE",
    "CHATS_SET",
    "CHATS_UPSERT",
    "CHATS_UPDATE",
    "CHATS_DELETE",
    "GROUPS_UPSERT",
    "GROUP_UPDATE",
    "GROUP_PARTICIPANTS_UPDATE",
    "CONNECTION_UPDATE",
    "LABELS_EDIT",
    "LABELS_ASSOCIATION",
    "CALL",
    "TYPEBOT_START",
    "TYPEBOT_CHANGE_STATUS",
    "REMOVE_INSTANCE",
    "LOGOUT_INSTANCE",
    "INSTANCE_CREATE",
    "INSTANCE_DELETE",
    "STATUS_INSTANCE",
]

EventChannel = Literal["websocket", "rabbitmq", "nats", "sqs", "kafka", "pusher"]

_CHANNEL_ENV = {
    "websocket": "WEBSOCKET_ENABLED",
    "rabbitmq": "RABBITMQ_ENABLED",
    "nats": "NATS_ENABLED",
    "sqs": "SQS_ENABLED",
    "kafka": "KAFKA_ENABLED",
    "pusher": "PUSHER_ENABLED",
}


def _webhook_view(row: dict) -> dict:
    """The webhook without header values: header names are kept, every value becomes "[redacted]"."""
    headers = row.get("headers")
    return {
        "enabled": bool(row.get("enabled")),
        "url": row.get("url"),
        "by_events": bool(row.get("webhookByEvents")),
        "include_media_base64": bool(row.get("webhookBase64")),
        "events": row.get("events") or [],
        "headers": {str(name): redact.REDACTED for name in headers} if isinstance(headers, dict) else {},
    }


def _channel_view(channel: str, row: dict) -> dict:
    """An event channel row reduced to its public fields; the Pusher secret is reported only as `secret_set`."""
    row = redact.redact(row)
    view: dict[str, object] = {
        "channel": channel,
        "enabled": bool(row.get("enabled")),
        "events": row.get("events") or [],
    }
    if channel == "pusher":
        view.update(
            {
                "app_id": row.get("appId"),
                "key": row.get("key"),
                "cluster": row.get("cluster"),
                "use_tls": bool(row.get("useTLS")),
                "secret_set": bool(row.get("secret")),
            }
        )
    return view


@registry.tool(
    title="Get webhook",
    toolset="events",
    kind="read",
    idempotent=True,
    integrations=registry.ALL,
)
async def get_webhook() -> str:
    """Read the webhook this Evolution instance calls when WhatsApp events happen.

    Returns whether it is enabled, its URL, whether one URL per event is used, whether media is included as base64,
    the subscribed events (an enabled webhook with no explicit list receives all 31 events) and the names of the
    custom headers; header values are never returned. Answers configured=false when no webhook is stored.
    """
    conn, client = await context.resolve()
    row = await calls.call(client, conn.identity, "GET", "webhook/find")
    if not isinstance(row, dict):
        return tool_result({"configured": False})
    return tool_result(_webhook_view(row))


@registry.tool(
    title="Set webhook",
    toolset="events",
    kind="destructive",
    idempotent=True,
    integrations=registry.ALL,
    local_only=True,
)
async def set_webhook(
    enabled: Annotated[bool, Field(description="true delivers events to the URL; false stops deliveries.")],
    url: Annotated[
        str,
        Field(
            min_length=10, max_length=2048, pattern=r"^https?://", description="http(s) URL that receives the events."
        ),
    ],
    events: Annotated[
        list[EventName],
        Field(max_length=31, description="Events to deliver. An empty list subscribes to all 31 events."),
    ],
    by_events: Annotated[
        bool, Field(description="Append the event name to the URL, so each event type goes to its own path.")
    ] = False,
    include_media_base64: Annotated[
        bool, Field(description="Include media files as base64 in the event bodies.")
    ] = False,
    headers: Annotated[
        dict[str, str] | None,
        Field(max_length=20, description="Custom HTTP headers sent with every delivery, e.g. an Authorization header."),
    ] = None,
) -> str:
    """Set the webhook this Evolution instance calls when WhatsApp events happen, replacing the stored one.

    Event bodies carry message contents and contacts, so the URL receives everything the selected events contain.
    An empty events list subscribes to all events; disabling the webhook stores an empty list. Custom headers are
    stored in Evolution and never returned. Available on the local server only because headers may hold credentials.
    Returns the stored webhook with header values hidden.
    """
    webhook: dict[str, object] = {
        "enabled": enabled,
        "url": url,
        "byEvents": by_events,
        "base64": include_media_base64,
        "events": list(events),
    }
    if headers is not None:
        webhook["headers"] = headers
    conn, client = await context.resolve()
    identity = conn.identity

    async def reread() -> object:
        row = await calls.call(client, identity, "GET", "webhook/find")
        return _webhook_view(row) if isinstance(row, dict) else {"configured": False}

    answer = await calls.call(
        client, identity, "POST", "webhook/set", json={"webhook": webhook}, write=True, reread=reread
    )
    if not isinstance(answer, dict):
        return tool_result(await reread())
    return tool_result(_webhook_view(answer))


@registry.tool(
    title="Get event channel",
    toolset="events",
    kind="read",
    idempotent=True,
    integrations=registry.ALL,
)
async def get_event_channel(
    channel: Annotated[EventChannel, Field(description="Which event stream to read.")],
) -> str:
    """Read the configuration of one event stream of this Evolution instance.

    Streams are websocket, rabbitmq, nats, sqs, kafka and pusher; use get_webhook for the webhook. Returns whether the
    stream is enabled and which events it carries; for pusher also the app id, key, cluster and TLS flag, with the
    secret reported only as secret_set. Answers configured=false when nothing is stored or when the Evolution server
    has that stream turned off.
    """
    conn, client = await context.resolve()
    row = await calls.call(client, conn.identity, "GET", f"{channel}/find")
    if not isinstance(row, dict):
        return tool_result(
            {
                "channel": channel,
                "configured": False,
                "note": f"Not configured, or disabled on the Evolution server ({_CHANNEL_ENV[channel]}=false).",
            }
        )
    return tool_result(_channel_view(channel, row))


@registry.tool(
    title="Set event channel",
    toolset="events",
    kind="destructive",
    idempotent=True,
    integrations=registry.ALL,
    local_only=True,
)
async def set_event_channel(
    channel: Annotated[EventChannel, Field(description="Which event stream to configure.")],
    enabled: Annotated[bool, Field(description="true streams events to the channel; false stops the stream.")],
    events: Annotated[
        list[EventName],
        Field(max_length=31, description="Events to stream. An empty list subscribes to all 31 events."),
    ],
    pusher_app_id: Annotated[
        str | None, Field(max_length=100, description="Pusher app id (pusher channel only).")
    ] = None,
    pusher_key: Annotated[
        str | None, Field(max_length=100, description="Pusher app key (pusher channel only).")
    ] = None,
    pusher_secret: Annotated[
        str | None, Field(max_length=100, description="Pusher app secret (pusher channel only).")
    ] = None,
    pusher_cluster: Annotated[
        str | None, Field(max_length=100, description="Pusher cluster, e.g. eu (pusher channel only).")
    ] = None,
    pusher_use_tls: Annotated[bool, Field(description="Connect to Pusher over TLS (pusher channel only).")] = True,
) -> str:
    """Set one event stream (websocket, rabbitmq, nats, sqs, kafka or pusher) of this Evolution instance.

    Use set_webhook for the webhook. The stream carries message contents and contacts to whatever consumes it. An
    empty events list subscribes to all events. The Evolution server must have the stream turned on in its own
    environment; when it has not, nothing is saved and the tool says so. The pusher channel needs app id, key, secret
    and cluster. Available on the local server only because the pusher secret is a credential. Returns the stored
    configuration; the secret is reported only as secret_set.
    """
    if channel == "pusher":
        given = {
            "pusher_app_id": pusher_app_id,
            "pusher_key": pusher_key,
            "pusher_secret": pusher_secret,
            "pusher_cluster": pusher_cluster,
        }
        missing = [name for name, value in given.items() if not value]
        if missing:
            raise ToolExecutionError(f"The pusher channel needs {', '.join(missing)}. Nothing was changed.")
        body: dict[str, object] = {
            "pusher": {
                "enabled": enabled,
                "appId": pusher_app_id,
                "key": pusher_key,
                "secret": pusher_secret,
                "cluster": pusher_cluster,
                "useTLS": pusher_use_tls,
                "events": list(events),
            }
        }
    else:
        body = {channel: {"enabled": enabled, "events": list(events)}}
    conn, client = await context.resolve()
    identity = conn.identity

    async def reread() -> object:
        row = await calls.call(client, identity, "GET", f"{channel}/find")
        return _channel_view(channel, row) if isinstance(row, dict) else {"channel": channel, "configured": False}

    answer = await calls.call(client, identity, "POST", f"{channel}/set", json=body, write=True, reread=reread)
    if not isinstance(answer, dict):
        raise ToolExecutionError(
            f"The {channel} channel is disabled on this Evolution server ({_CHANNEL_ENV[channel]}=false), "
            "so nothing was saved."
        )
    return tool_result(_channel_view(channel, answer))
