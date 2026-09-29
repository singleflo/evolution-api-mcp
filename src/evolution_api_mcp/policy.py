"""The safety gate: one function decides whether a tool may run and whether tools/list shows it (D11)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from evolution_api_mcp.context import Connection
    from evolution_api_mcp.registry import ToolSpec

DEFAULT_DENY = frozenset(
    {
        "post_status",
        "remove_group_participants",
        "set_webhook",
        "set_event_channel",
        "set_proxy",
        "set_chatwoot_config",
    }
)


def refusal(spec: ToolSpec, conn: Connection) -> str | None:
    """Why `spec` may not run on `conn`, or None when it may. The first failing rule wins."""
    n = spec.name
    hosted = conn.mode == "hosted"

    if spec.local_only and hosted:
        return f"{n} is available only on the local server (uvx evolution-api-mcp)."

    if not spec.universal and spec.toolset not in conn.toolsets:
        t = spec.toolset
        if hosted:
            return (
                f"{n} belongs to the '{t}' toolset, which this connection did not enable. "
                f"Disconnect and reconnect, then tick '{t}' on the consent page."
            )
        return (
            f"{n} belongs to the '{t}' toolset, which is not enabled. "
            f"Add '{t}' to EVOLUTION_MCP_TOOLSETS (or --toolsets) and restart the server."
        )

    if conn.identity is not None and conn.identity.integration not in spec.integrations:
        return (
            f"{n} is not available for this instance: it needs {' or '.join(sorted(spec.integrations))}, "
            f"and this instance uses {conn.identity.integration}."
        )

    if spec.kind == "read":
        return None

    if conn.policy == "read":
        if hosted:
            return (
                f"{n} changes data, and this connection was authorised as read-only. "
                "Reconnect and choose the standard policy to allow it."
            )
        return f"{n} changes data, and this server is read-only (EVOLUTION_MCP_ALLOW=none or --read-only)."

    if spec.kind == "irreversible" and not conn.irreversible_granted:
        if hosted:
            return f"{n} cannot be undone and is never available on the hosted server."
        return (
            f"{n} cannot be undone, so it runs only when EVOLUTION_MCP_ALLOW_IRREVERSIBLE=yes is set for this server."
        )

    if n in conn.deny:
        if hosted:
            return f"{n} is not available on the hosted server."
        suffix = " (default list)" if conn.deny_is_default else ""
        return f"{n} is in EVOLUTION_MCP_DENY{suffix}. Remove it from that list to allow it."

    if conn.allow is not None and n not in conn.allow:
        return f"{n} is not in EVOLUTION_MCP_ALLOW."

    return None


def visible(spec: ToolSpec, conn: Connection) -> bool:
    """Whether tools/list shows `spec` to `conn`: exactly when a call would not be refused."""
    return refusal(spec, conn) is None
