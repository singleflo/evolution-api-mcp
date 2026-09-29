"""Profile toolset: the instance's own WhatsApp profile and privacy settings."""

from typing import Annotated, Literal

from pydantic import Field

from evolution_api_mcp import calls, context, jid, registry
from evolution_api_mcp.client import EvolutionClient, EvolutionError
from evolution_api_mcp.context import InstanceIdentity
from evolution_api_mcp.discovery import fetch_instance_row
from evolution_api_mcp.errors import ToolExecutionError, raise_evolution_failure, tool_result
from evolution_api_mcp.tools import MediaUrl
from evolution_api_mcp.tools.contacts import compact, refuse_absent_number

BAILEYS_ONLY = frozenset({registry.BAILEYS})

# Tool parameter -> the key Evolution uses in its privacy request and answer.
_PRIVACY_KEYS = {
    "read_receipts": "readreceipts",
    "profile_photo": "profile",
    "about": "status",
    "online": "online",
    "last_seen": "last",
    "groups_add": "groupadd",
}

Audience = Literal["all", "contacts", "contact_blacklist", "none"]


@registry.tool(title="Get own profile", toolset="profile", kind="read", idempotent=True, integrations=BAILEYS_ONLY)
async def get_my_profile() -> str:
    """Get the WhatsApp profile of the number this instance is linked to.

    Returns phone_number, name, about, picture_url and is_business, plus email, description and website when the
    account is a business account. A newly created instance that is not paired yet has no phone number; the
    pairing tool of the instance toolset links one. get_privacy_settings shows who can see these fields.
    """
    conn, client = await context.resolve()
    try:
        row = await fetch_instance_row(client, conn.identity)
    except EvolutionError as exc:
        await raise_evolution_failure(exc, phase="before_mutation")
    owner = row.get("ownerJid")
    phone = jid.phone_of(owner) if isinstance(owner, str) else None
    if phone is None:
        raise ToolExecutionError("The instance has no linked phone number yet; start_pairing links one.")
    body = await calls.call(
        client,
        conn.identity,
        "POST",
        "chat/fetchProfile",
        json={"number": phone},
        on_http_error=refuse_absent_number(phone),
    )
    data = body if isinstance(body, dict) else {}
    return tool_result(
        compact(
            {
                "phone_number": phone,
                "name": data.get("name"),
                "about": data.get("status"),
                "picture_url": data.get("picture"),
                "is_business": data.get("isBusiness"),
                "email": data.get("email"),
                "description": data.get("description"),
                "website": data.get("website"),
            }
        )
    )


async def _read_privacy(client: EvolutionClient, identity: InstanceIdentity) -> dict:
    body = await calls.call(client, identity, "GET", "chat/fetchPrivacySettings")
    data = body if isinstance(body, dict) else {}
    return {name: data.get(key) for name, key in _PRIVACY_KEYS.items()}


@registry.tool(title="Get privacy settings", toolset="profile", kind="read", idempotent=True, integrations=BAILEYS_ONLY)
async def get_privacy_settings() -> str:
    """Get who can see this account's read receipts, profile photo, about text, online status and last seen.

    Returns read_receipts, profile_photo, about, online, last_seen and groups_add (who may add the account to
    groups). Audience values are all, contacts, contact_blacklist (contacts except some) and none; online is all
    or match_last_seen. update_privacy_settings changes them.
    """
    conn, client = await context.resolve()
    return tool_result(await _read_privacy(client, conn.identity))


@registry.tool(
    title="Update privacy settings",
    toolset="profile",
    kind="destructive",
    idempotent=True,
    integrations=BAILEYS_ONLY,
)
async def update_privacy_settings(
    read_receipts: Annotated[
        Literal["all", "none"] | None,
        Field(description="Send read receipts (blue ticks): all or none."),
    ] = None,
    profile_photo: Annotated[Audience | None, Field(description="Who sees the profile photo.")] = None,
    about: Annotated[Audience | None, Field(description="Who sees the about text.")] = None,
    last_seen: Annotated[Audience | None, Field(description="Who sees last seen.")] = None,
    groups_add: Annotated[Audience | None, Field(description="Who may add this account to groups.")] = None,
    online: Annotated[
        Literal["all", "match_last_seen"] | None,
        Field(description="Who sees when the account is online: all, or the same people as last seen."),
    ] = None,
) -> str:
    """Change who can see this account's read receipts, profile photo, about text, last seen and online status.

    At least one setting is required; settings that are not given keep their current value. The change applies to
    the whole WhatsApp account at once and replaces the earlier settings, so people see or stop seeing these
    details immediately. The earlier values are not kept by this server; reading them with get_privacy_settings
    beforehand allows restoring them. Returns the six settings as they are after the change.
    """
    given = {
        "read_receipts": read_receipts,
        "profile_photo": profile_photo,
        "about": about,
        "online": online,
        "last_seen": last_seen,
        "groups_add": groups_add,
    }
    changes = {name: value for name, value in given.items() if value is not None}
    if not changes:
        raise ToolExecutionError("Give at least one privacy setting to change. Nothing was changed.")
    conn, client = await context.resolve()
    merged = {**await _read_privacy(client, conn.identity), **changes}
    missing = [name for name, value in merged.items() if not value]
    if missing:
        raise ToolExecutionError(
            f"Evolution did not report the current value of {', '.join(missing)}, and Evolution needs all six "
            "privacy settings to save any of them. Give those settings too. Nothing was changed."
        )
    await calls.call(
        client,
        conn.identity,
        "POST",
        "chat/updatePrivacySettings",
        json={_PRIVACY_KEYS[name]: value for name, value in merged.items()},
        write=True,
    )
    return tool_result(merged)


@registry.tool(
    title="Update profile name", toolset="profile", kind="destructive", idempotent=True, integrations=BAILEYS_ONLY
)
async def update_profile_name(
    name: Annotated[str, Field(min_length=1, max_length=25, description="New WhatsApp display name.")],
) -> str:
    """Change the WhatsApp display name (push name) of this account.

    People who message or are messaged by this account see the new name. The earlier name is replaced and is not
    kept; set it again to restore it. Returns the updated field.
    """
    conn, client = await context.resolve()
    await calls.call(client, conn.identity, "POST", "chat/updateProfileName", json={"name": name}, write=True)
    return tool_result({"updated": "name"})


@registry.tool(
    title="Update profile about text",
    toolset="profile",
    kind="destructive",
    idempotent=True,
    integrations=BAILEYS_ONLY,
)
async def update_profile_about(
    about: Annotated[str, Field(min_length=1, max_length=139, description="New about text.")],
) -> str:
    """Change the about text (the short status line) of this account's WhatsApp profile.

    Contacts see the new text on the profile, depending on the about privacy setting. The earlier text is replaced
    and is not kept. Evolution rejects an empty text, so the about text cannot be cleared through this tool.
    Returns the updated field.
    """
    conn, client = await context.resolve()
    await calls.call(client, conn.identity, "POST", "chat/updateProfileStatus", json={"status": about}, write=True)
    return tool_result({"updated": "about"})


@registry.tool(
    title="Update profile picture",
    toolset="profile",
    kind="destructive",
    idempotent=True,
    integrations=BAILEYS_ONLY,
)
async def update_profile_picture(
    image_url: Annotated[
        MediaUrl,
        Field(description="Public http(s) URL of the new profile picture (JPEG or PNG); Evolution downloads it."),
    ],
) -> str:
    """Change the profile picture of this WhatsApp account to the image at a URL.

    Evolution downloads the image from the URL and sets it; people who can see the profile photo (see
    get_privacy_settings) see the new picture. The earlier picture is replaced and is not kept.
    remove_profile_picture clears it. Returns the updated field.
    """
    conn, client = await context.resolve()
    await calls.call(
        client, conn.identity, "POST", "chat/updateProfilePicture", json={"picture": image_url}, write=True
    )
    return tool_result({"updated": "picture"})


@registry.tool(
    title="Remove profile picture",
    toolset="profile",
    kind="destructive",
    idempotent=True,
    integrations=BAILEYS_ONLY,
)
async def remove_profile_picture() -> str:
    """Remove the profile picture of this WhatsApp account.

    People who could see the picture see the default avatar afterwards. The removed picture is not kept;
    update_profile_picture sets a new one. Returns the updated field.
    """
    conn, client = await context.resolve()
    await calls.call(client, conn.identity, "DELETE", "chat/removeProfilePicture", json={}, write=True)
    return tool_result({"updated": "picture"})
