"""Settings toolset: instance behaviour settings and the proxy."""

from typing import Annotated, Literal

from pydantic import Field

from evolution_api_mcp import calls, context, redact, registry
from evolution_api_mcp.errors import ToolExecutionError, tool_result

# Evolution key -> tool key, in the order the tools return them.
_SETTING_KEYS = (
    ("rejectCall", "reject_calls"),
    ("msgCall", "call_rejection_message"),
    ("groupsIgnore", "ignore_groups"),
    ("alwaysOnline", "always_online"),
    ("readMessages", "auto_read_messages"),
    ("readStatus", "auto_read_status"),
    ("syncFullHistory", "sync_full_history"),
)


def _settings_view(row: dict) -> dict:
    """The seven settings from an Evolution settings row; `wavoipToken` is never read."""
    row = redact.redact(row)
    return {
        tool_key: (row.get(wire_key) or "") if wire_key == "msgCall" else bool(row.get(wire_key))
        for wire_key, tool_key in _SETTING_KEYS
    }


def _proxy_view(row: dict) -> dict:
    """The proxy row without its password."""
    row = redact.redact(row)
    return {
        "enabled": bool(row.get("enabled")),
        "host": row.get("host"),
        "port": row.get("port"),
        "protocol": row.get("protocol"),
        "username": row.get("username"),
        "password_set": bool(row.get("password")),
    }


async def _find_settings(client, identity) -> dict | None:
    row = await calls.call(client, identity, "GET", "settings/find")
    return row if isinstance(row, dict) else None


@registry.tool(
    title="Get instance settings",
    toolset="settings",
    kind="read",
    idempotent=True,
    integrations=registry.ALL,
)
async def get_instance_settings() -> str:
    """Read the behaviour settings of this Evolution instance.

    Returns whether incoming calls are rejected (and the message sent to the caller), whether group messages are
    ignored, whether the number stays online, whether messages and status updates are marked read automatically and
    whether full history is synced at pairing. Answers configured=false when Evolution has stored no settings yet.
    update_instance_settings changes them.
    """
    conn, client = await context.resolve()
    row = await _find_settings(client, conn.identity)
    if row is None:
        return tool_result({"configured": False})
    return tool_result(_settings_view(row))


@registry.tool(
    title="Update instance settings",
    toolset="settings",
    kind="destructive",
    idempotent=True,
    integrations=registry.ALL,
)
async def update_instance_settings(
    reject_calls: Annotated[bool | None, Field(description="Reject incoming WhatsApp calls automatically.")] = None,
    call_rejection_message: Annotated[
        str | None,
        Field(max_length=500, description="Text sent to a caller whose call was rejected. Empty text sends none."),
    ] = None,
    ignore_groups: Annotated[bool | None, Field(description="Ignore messages and events from group chats.")] = None,
    always_online: Annotated[
        bool | None, Field(description="Keep the number shown as online while the session is connected.")
    ] = None,
    auto_read_messages: Annotated[
        bool | None,
        Field(description="Mark incoming messages as read automatically; senders then see blue ticks."),
    ] = None,
    auto_read_status: Annotated[
        bool | None, Field(description="Mark contacts' status updates as viewed automatically.")
    ] = None,
    sync_full_history: Annotated[
        bool | None,
        Field(description="Ask WhatsApp for the full message history at the next pairing; no effect until then."),
    ] = None,
) -> str:
    """Change behaviour settings of this Evolution instance; settings not given keep their current value.

    Give at least one setting. The tool reads the current settings, applies the given values and writes all seven
    back, so it replaces the stored configuration with the merged result. Turning on auto_read_messages makes senders
    see their messages as read. sync_full_history applies only at the next pairing. Returns the seven settings after
    the change.
    """
    values = {
        "rejectCall": reject_calls,
        "msgCall": call_rejection_message,
        "groupsIgnore": ignore_groups,
        "alwaysOnline": always_online,
        "readMessages": auto_read_messages,
        "readStatus": auto_read_status,
        "syncFullHistory": sync_full_history,
    }
    changes = {key: value for key, value in values.items() if value is not None}
    if not changes:
        raise ToolExecutionError("Give at least one setting to change. Nothing was changed.")
    conn, client = await context.resolve()
    identity = conn.identity
    current = await _find_settings(client, identity) or {}
    body = {
        "rejectCall": bool(current.get("rejectCall")),
        "msgCall": current.get("msgCall") or "",
        "groupsIgnore": bool(current.get("groupsIgnore")),
        "alwaysOnline": bool(current.get("alwaysOnline")),
        "readMessages": bool(current.get("readMessages")),
        "readStatus": bool(current.get("readStatus")),
        "syncFullHistory": bool(current.get("syncFullHistory")),
    }
    body.update(changes)

    async def reread() -> object:
        row = await _find_settings(client, identity)
        return _settings_view(row) if row is not None else {"configured": False}

    await calls.call(client, identity, "POST", "settings/set", json=body, write=True, reread=reread)
    return tool_result(_settings_view(body))


@registry.tool(
    title="Get proxy settings",
    toolset="settings",
    kind="read",
    idempotent=True,
    integrations=registry.ALL,
)
async def get_proxy() -> str:
    """Read the proxy this Evolution instance uses to reach WhatsApp.

    Returns whether the proxy is enabled, its host, port, protocol and username, and password_set telling whether a
    password is stored; the password itself is never returned. Answers enabled=false when no proxy is configured.
    """
    conn, client = await context.resolve()
    row = await calls.call(client, conn.identity, "GET", "proxy/find")
    if not isinstance(row, dict):
        return tool_result({"enabled": False})
    return tool_result(_proxy_view(row))


@registry.tool(
    title="Set proxy",
    toolset="settings",
    kind="destructive",
    idempotent=True,
    integrations=registry.ALL,
    local_only=True,
)
async def set_proxy(
    enabled: Annotated[
        bool, Field(description="true routes the instance's traffic through the proxy; false disables it.")
    ],
    host: Annotated[
        str | None, Field(min_length=1, max_length=255, description="Proxy host name or IP address.")
    ] = None,
    port: Annotated[int | None, Field(ge=1, le=65535, description="Proxy port.")] = None,
    protocol: Annotated[
        Literal["http", "https", "socks4", "socks5"] | None, Field(description="Proxy protocol.")
    ] = None,
    username: Annotated[str | None, Field(max_length=255, description="Proxy user name, when it needs one.")] = None,
    password: Annotated[str | None, Field(max_length=255, description="Proxy password, when it needs one.")] = None,
) -> str:
    """Enable, replace or disable the proxy this Evolution instance uses to reach WhatsApp.

    Enabling needs host, port and protocol. Evolution tests the proxy when it is saved and refuses one that does not
    change its outgoing address, so a wrong proxy is rejected with Invalid proxy and the old setting stays. Disabling
    clears the stored proxy fields. Available on the local server only because it takes a password. Returns the
    stored proxy without its password.
    """
    if enabled:
        missing = [
            name for name, value in (("host", host), ("port", port), ("protocol", protocol)) if value in (None, "")
        ]
        if missing:
            raise ToolExecutionError(f"Enabling the proxy needs {', '.join(missing)}. Nothing was changed.")
        body: dict[str, object] = {"enabled": True, "host": host, "port": str(port), "protocol": protocol}
        if username is not None:
            body["username"] = username
        if password is not None:
            body["password"] = password
    else:
        body = {"enabled": False, "host": "disabled", "port": "0", "protocol": "http"}
    conn, client = await context.resolve()
    identity = conn.identity

    async def reread() -> object:
        row = await calls.call(client, identity, "GET", "proxy/find")
        return _proxy_view(row) if isinstance(row, dict) else {"enabled": False}

    answer = await calls.call(client, identity, "POST", "proxy/set", json=body, write=True, reread=reread)
    stored = answer.get("proxy") if isinstance(answer, dict) else None
    stored = stored.get("proxy") if isinstance(stored, dict) else None
    if not isinstance(stored, dict):
        return tool_result(await reread())
    return tool_result(_proxy_view(stored))
