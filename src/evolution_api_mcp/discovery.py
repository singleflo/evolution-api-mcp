"""Find out which Evolution instance a token belongs to, and refuse the credentials this server must never use.

The operator supplies a server URL and one instance's own token; the instance name and integration are discovered
so nobody types them. Evolution's server-wide `AUTHENTICATION_API_KEY` controls every instance, so it is refused
in both modes: `POST /verify-creds` answers 200 only for that key and 401 for an instance token.
"""

from evolution_api_mcp.client import EvolutionClient, EvolutionHTTPError
from evolution_api_mcp.context import InstanceIdentity
from evolution_api_mcp.errors import ToolExecutionError
from evolution_api_mcp.registry import ALL, BAILEYS

GLOBAL_KEY_MESSAGE = (
    "This is the Evolution server's global AUTHENTICATION_API_KEY, which controls every instance on the server. "
    "Use the instance's own token instead."
)
NOT_EVOLUTION_MESSAGE = (
    "The URL does not answer like an Evolution API server (GET / returned no version). Check the Evolution server URL."
)
NOT_INSTANCE_TOKEN_MESSAGE = (
    "Evolution did not accept this token as an instance token. Check the token; Evolution resolves instance tokens "
    "only when DATABASE_SAVE_DATA_INSTANCE=true (its default)."
)


class DiscoveryRefused(Exception):
    """The server or the credentials cannot be used by this MCP server; `message` says why, for the user."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _major(version: str) -> int | None:
    head = version.strip().lstrip("vV").split(".", 1)[0]
    return int(head) if head.isdigit() else None


async def discover(client: EvolutionClient) -> InstanceIdentity:
    """Identify the one instance `client`'s token belongs to.

    Raises `DiscoveryRefused` when the server or the credentials are unusable, and lets transport failures and
    unexpected Evolution answers (`EvolutionUnreachable`, `EvolutionUncertain`, 5xx) propagate: those say nothing
    about the credentials and may pass on retry.
    """
    try:
        root = await client.request("GET", "/")
    except EvolutionHTTPError as exc:
        if exc.status >= 500:
            raise
        raise DiscoveryRefused(NOT_EVOLUTION_MESSAGE) from exc
    version = root.get("version") if isinstance(root, dict) else None
    if not isinstance(version, str) or _major(version) is None:
        raise DiscoveryRefused(NOT_EVOLUTION_MESSAGE)
    if _major(version) != 2:
        raise DiscoveryRefused(f"Evolution API {version} is not supported; this server needs Evolution API 2.x.")

    try:
        await client.request("POST", "/verify-creds", json={})
    except EvolutionHTTPError as exc:
        if exc.status == 404:
            raise DiscoveryRefused(
                "This Evolution server has no POST /verify-creds; Evolution API 2.3 or later is required."
            ) from exc
        if exc.status != 401:
            raise
    else:
        raise DiscoveryRefused(GLOBAL_KEY_MESSAGE)

    try:
        body = await client.request("GET", "/instance/fetchInstances")
    except EvolutionHTTPError as exc:
        if exc.status == 401:
            raise DiscoveryRefused(NOT_INSTANCE_TOKEN_MESSAGE) from exc
        raise
    rows = [row for row in body if isinstance(row, dict)] if isinstance(body, list) else []
    if not rows:
        raise DiscoveryRefused(NOT_INSTANCE_TOKEN_MESSAGE)
    if len(rows) > 1:
        names = ", ".join(str(row.get("name")) for row in rows)
        raise DiscoveryRefused(
            f"This token belongs to {len(rows)} instances ({names}). Give each instance its own token, then retry."
        )
    row = rows[0]
    if row.get("token") != client.token:
        raise DiscoveryRefused(GLOBAL_KEY_MESSAGE)
    integration = row.get("integration") or BAILEYS
    if integration not in ALL:
        raise DiscoveryRefused(f"Unknown Evolution integration '{integration}'.")
    return InstanceIdentity(name=str(row["name"]), integration=integration)


async def fetch_instance_row(client: EvolutionClient, identity: InstanceIdentity) -> dict:
    """A fresh Instance row for status and profile tools. The row carries secrets: callers project only the keys
    they need and never return it."""
    body = await client.request("GET", "/instance/fetchInstances", params={"instanceName": identity.name})
    if isinstance(body, list):
        for row in body:
            if isinstance(row, dict) and row.get("name") == identity.name:
                return row
    raise ToolExecutionError("Evolution no longer lists this instance; check that it still exists.")
