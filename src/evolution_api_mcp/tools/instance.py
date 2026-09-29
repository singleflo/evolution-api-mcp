"""Instance toolset: connection status, pairing, restart, logout and online presence."""

from typing import Annotated, Literal

from mcp_types import ImageContent, TextContent
from pydantic import Field

from evolution_api_mcp import calls, context, discovery, jid, policy, registry
from evolution_api_mcp.client import EvolutionClient, EvolutionError
from evolution_api_mcp.context import InstanceIdentity
from evolution_api_mcp.errors import ToolExecutionError, raise_evolution_failure, tool_result
from evolution_api_mcp.toolsets import TOOLSET_ORDER

BAI = frozenset({registry.BAILEYS})

_NOT_CONNECTED_NOTE = (
    "The WhatsApp session is not connected; sends and live lookups fail until it is. "
    "start_pairing (instance toolset) links it again on WhatsApp Web instances."
)
_CODE_HOW_TO = (
    "On the phone: WhatsApp → Settings → Linked devices → Link a device → Link with phone number instead, "
    "then type the code."
)
_QR_HOW_TO = (
    "Scan with WhatsApp → Settings → Linked devices → Link a device. The code changes about every 20 seconds; "
    "call start_pairing again for a fresh one."
)


def _text(payload: object) -> TextContent:
    return TextContent(type="text", text=tool_result(payload))


async def _state(client: EvolutionClient, identity: InstanceIdentity) -> str | None:
    """The connection state Evolution reports ("open", "connecting", "close"), or None when it reports none."""
    body = await calls.call(client, identity, "GET", "instance/connectionState")
    instance = body.get("instance") if isinstance(body, dict) else None
    state = instance.get("state") if isinstance(instance, dict) else None
    return state if isinstance(state, str) else None


def _state_reread(client: EvolutionClient, identity: InstanceIdentity):
    async def reread() -> object:
        return {"state": await _state(client, identity)}

    return reread


def _error_body_message(body: object) -> str | None:
    """Evolution answers some failures of connect and restart with HTTP 200 and `{error: true, message}`."""
    if isinstance(body, dict) and body.get("error") is True:
        return str(body.get("message") or "no reason given").rstrip(" .")
    return None


@registry.tool(
    title="Get instance status",
    toolset="instance",
    kind="read",
    idempotent=True,
    integrations=registry.ALL,
    universal=True,
)
async def get_instance_status() -> str:
    """Report the connection state, integration and enabled toolsets of this Evolution instance.

    Use it first to learn whether the WhatsApp session is open and which integration this instance runs
    (WHATSAPP-BAILEYS, WHATSAPP-BUSINESS or EVOLUTION). Returns the instance name, its state (open, connecting or
    close), the linked phone number and profile name, the Evolution version, whether this is the local or hosted
    server, the safety policy, the enabled toolsets and how many tools are available on this connection. It reads
    only and works even when the session is closed.
    """
    conn, client = await context.resolve()
    identity = conn.identity
    try:
        row = await discovery.fetch_instance_row(client, identity)
        root = await client.request("GET", "/")
    except EvolutionError as exc:
        await raise_evolution_failure(exc, phase="before_mutation")
    state = await _state(client, identity)
    version = root.get("version") if isinstance(root, dict) else None
    result: dict[str, object] = {
        "instance": identity.name,
        "integration": identity.integration,
        "state": state or "unknown",
        "phone_number": jid.phone_of(row.get("ownerJid") or ""),
        "profile_name": row.get("profileName"),
        "evolution_version": version,
        "server": conn.mode,
        "policy": conn.policy,
        "toolsets": [name for name in TOOLSET_ORDER if name in conn.toolsets],
        "tools_available": sum(1 for spec in registry.specs() if policy.visible(spec, conn)),
    }
    if state != "open":
        result["note"] = _NOT_CONNECTED_NOTE
    return tool_result(result)


@registry.tool(
    title="Start device pairing",
    toolset="instance",
    kind="write",
    idempotent=False,
    integrations=BAI,
)
async def start_pairing(
    phone_number: Annotated[
        str | None,
        Field(
            min_length=7,
            max_length=24,
            description=(
                "Your own WhatsApp number in international format, to receive an 8-character pairing code instead "
                "of a QR code."
            ),
        ),
    ] = None,
) -> list[TextContent | ImageContent]:
    """Start linking this WhatsApp Web instance to a phone and return a QR code or a pairing code.

    Use it when get_instance_status reports that the session is not connected. Without phone_number the result is a
    QR image to scan under WhatsApp → Settings → Linked devices; with phone_number it is an 8-character code to type
    on the phone instead. QR codes change about every 20 seconds, so call it again for a fresh one. An instance that
    is already connected answers that there is nothing to pair. The code is shown to whoever reads this result and
    links the phone that scans or enters it.
    """
    conn, client = await context.resolve()
    identity = conn.identity
    params: dict[str, str] | None = None
    if phone_number is not None:
        digits = "".join(ch for ch in phone_number if ch.isdigit())
        if not 7 <= len(digits) <= 15:
            raise ToolExecutionError("phone_number must be an international phone number of 7 to 15 digits.")
        params = {"number": digits}
    body = await calls.call(
        client,
        identity,
        "GET",
        "instance/connect",
        params=params,
        write=True,
        reread=_state_reread(client, identity),
    )
    message = _error_body_message(body)
    if message is not None:
        raise ToolExecutionError(f"Evolution could not start pairing: {message}. Nothing was changed.")
    if not isinstance(body, dict):
        body = {}
    instance = body.get("instance")
    if isinstance(instance, dict) and instance.get("state") == "open":
        return [_text({"state": "open", "note": "Already connected; nothing to pair."})]
    if body.get("pairingCode"):
        return [_text({"pairing_code": body["pairingCode"], "how_to": _CODE_HOW_TO})]
    image = body.get("base64")
    if isinstance(image, str) and image:
        return [
            _text({"how_to": _QR_HOW_TO}),
            ImageContent(type="image", data=image.split(",", 1)[-1], mime_type="image/png"),
        ]
    return [
        _text(
            {
                "state": "connecting",
                "note": "Evolution has not produced a pairing code yet; call start_pairing again in a few seconds.",
            }
        )
    ]


@registry.tool(
    title="Restart instance connection",
    toolset="instance",
    kind="destructive",
    idempotent=False,
    integrations=BAI,
)
async def restart_instance() -> str:
    """Restart the WhatsApp connection of this instance and return the connection state afterwards.

    Use it when an open session stops delivering or receiving messages. The session stays linked; messages arriving
    during the restart of a few seconds may be delayed. Evolution refuses a session that is already closed: a closed
    session needs start_pairing, not a restart.
    """
    conn, client = await context.resolve()
    identity = conn.identity
    body = await calls.call(
        client,
        identity,
        "POST",
        "instance/restart",
        json={},
        write=True,
        reread=_state_reread(client, identity),
    )
    message = _error_body_message(body)
    if message is not None:
        raise ToolExecutionError(
            f"Evolution could not restart the instance: {message}. Nothing was changed. "
            "A session that is not connected needs start_pairing, not a restart."
        )
    return tool_result({"restarted": True, "state": await _state(client, identity) or "unknown"})


@registry.tool(
    title="Log out WhatsApp session",
    toolset="instance",
    kind="irreversible",
    idempotent=True,
    integrations=BAI,
)
async def logout_instance() -> str:
    """Unlink the WhatsApp session from this instance so that it stops receiving and sending messages.

    The session cannot be restored from this server: linking the phone again needs start_pairing and a scan or code
    entered on the phone. The instance itself, its chats and its configuration stay in Evolution. Evolution refuses
    a session that is already closed.
    """
    conn, client = await context.resolve()
    identity = conn.identity
    await calls.call(
        client,
        identity,
        "DELETE",
        "instance/logout",
        write=True,
        reread=_state_reread(client, identity),
    )
    return tool_result(
        {
            "logged_out": True,
            "note": "The WhatsApp session was removed from this instance; start_pairing links it again.",
        }
    )


@registry.tool(
    title="Set online presence",
    toolset="instance",
    kind="write",
    idempotent=True,
    integrations=BAI,
)
async def set_presence(
    presence: Annotated[
        Literal["available", "unavailable"],
        Field(description="available shows the number as online; unavailable shows it as offline."),
    ],
) -> str:
    """Show this WhatsApp number as online or offline to its contacts.

    Presence is an ephemeral indicator, not a message; nothing is sent to any chat. Use send_chat_presence (messaging
    toolset) to show typing or recording in one chat. Returns the presence that was set.
    """
    conn, client = await context.resolve()
    await calls.call(
        client,
        conn.identity,
        "POST",
        "instance/setPresence",
        json={"presence": presence},
        write=True,
    )
    return tool_result({"presence": presence})
