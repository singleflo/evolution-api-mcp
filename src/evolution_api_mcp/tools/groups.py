"""Group tools: details, participants, invite links, creation, settings and membership (WhatsApp Web / Baileys).

Evolution reads `groupJid` from the JSON body on POST routes and from the query string on GET and DELETE routes.
"""

from typing import Annotated, Literal

from pydantic import Field

from evolution_api_mcp import calls, context, jid, messages, registry
from evolution_api_mcp.client import EvolutionClient
from evolution_api_mcp.context import InstanceIdentity
from evolution_api_mcp.errors import ToolExecutionError, tool_result
from evolution_api_mcp.tools import Group, MediaUrl

_BAILEYS = frozenset({registry.BAILEYS})
_INVITE_BASE = "https://chat.whatsapp.com/"
_MAX_PARTICIPANTS_SHOWN = 500

_PARTICIPANT_STATUS = {
    "200": "done",
    "403": "needs an invite (privacy settings)",
    "408": "left recently",
    "409": "already in group",
}
_EPHEMERAL_SECONDS = {"off": 0, "24h": 86_400, "7d": 604_800, "90d": 7_776_000}

Phones = Annotated[
    list[str],
    Field(
        min_length=1,
        max_length=50,
        description="International phone numbers (country code first, e.g. 393331234567), up to 50.",
    ),
]


# --- helpers ---------------------------------------------------------------------------------------------------


def _dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _group_id(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return value if "@" in value else f"{value}@g.us"


def _person(*values: object) -> str | None:
    """A phone number when the id is one, else the id itself; the first non-empty value wins."""
    for value in values:
        if isinstance(value, str) and value:
            return jid.phone_of(value) or value
    return None


def _size(row: dict) -> int | None:
    size = row.get("size")
    if isinstance(size, int) and not isinstance(size, bool):
        return size
    participants = row.get("participants")
    return len(participants) if isinstance(participants, list) else None


def _summary(row: dict) -> dict:
    return {
        "group_id": _group_id(row.get("id")),
        "subject": row.get("subject"),
        "size": _size(row),
        "owner": _person(row.get("ownerPn"), row.get("owner")),
        "created_at": messages.iso(row.get("creation")),
        "announce_only": bool(row.get("announce")),
        "locked": bool(row.get("restrict")),
        "is_community": bool(row.get("isCommunity")),
    }


def _participant(row: object) -> dict:
    item = _dict(row)
    chat_id = item.get("id")
    admin = item.get("admin")
    phone = jid.phone_of(chat_id) if isinstance(chat_id, str) else None
    if phone is None and isinstance(item.get("phoneNumber"), str):
        # Baileys 7 lists a participant by @lid id and carries the phone number separately.
        phone = jid.phone_of(item["phoneNumber"])
    return {
        "chat_id": chat_id,
        "phone": phone,
        "admin": admin if admin in ("admin", "superadmin") else None,
    }


async def _phones(
    client: EvolutionClient, conn: context.Connection, values: list[str], *, purpose: calls.Purpose
) -> list[str]:
    """Digits of each person (numbers, chat ids or exact names) in the given order, without duplicates (Evolution
    rejects repeated numbers)."""
    digits = [(await calls.resolve_person(client, conn, value, purpose=purpose))[1] for value in values]
    return list(dict.fromkeys(digits))


def _invite_url(code: object) -> str | None:
    return f"{_INVITE_BASE}{code}" if isinstance(code, str) and code else None


async def _all_groups(client: EvolutionClient, identity: InstanceIdentity, *, write: bool = False) -> list[dict]:
    body = await calls.call(
        client, identity, "GET", "group/fetchAllGroups", params={"getParticipants": "false"}, write=write
    )
    return [row for row in body if isinstance(row, dict)] if isinstance(body, list) else []


async def _group_row(client: EvolutionClient, identity: InstanceIdentity, group_jid: str) -> dict:
    body = await calls.call(client, identity, "GET", "group/findGroupInfos", params={"groupJid": group_jid})
    if not isinstance(body, dict):
        raise ToolExecutionError(f"Group {group_jid} was not found. Use list_groups to find its id.")
    return body


def _participant_results(body: object) -> list[dict]:
    results = []
    for item in _dict(body).get("updateParticipants") or []:
        entry = _dict(item)
        code = str(entry.get("status", "")).strip()
        who = entry.get("jid")
        results.append(
            {
                "phone": _person(who),
                "status_code": int(code) if code.isdigit() else code,
                "status": _PARTICIPANT_STATUS.get(code, "failed"),
            }
        )
    return results


async def _update_participants(group: str, action: str, participants: list[str]) -> str:
    conn, client = await context.resolve()
    group_jid = await calls.resolve_group(client, conn, group, purpose="write")
    numbers = await _phones(client, conn, participants, purpose="write")
    body = await calls.call(
        client,
        conn.identity,
        "POST",
        "group/updateParticipant",
        json={"groupJid": group_jid, "action": action, "participants": numbers},
        write=True,
    )
    return tool_result({"group_id": group_jid, "action": action, "results": _participant_results(body)})


async def _update_field(group: str, endpoint: str, field: str, body: dict) -> str:
    conn, client = await context.resolve()
    group_jid = await calls.resolve_group(client, conn, group, purpose="write")
    await calls.call(client, conn.identity, "POST", endpoint, json={"groupJid": group_jid, **body}, write=True)
    return tool_result({"group_id": group_jid, "updated": field})


# --- reads -----------------------------------------------------------------------------------------------------


@registry.tool(
    title="List groups",
    toolset="groups",
    kind="read",
    idempotent=True,
    integrations=_BAILEYS,
)
async def list_groups(
    name_contains: Annotated[
        str | None,
        Field(min_length=1, max_length=100, description="Keep only groups whose subject contains this text."),
    ] = None,
    limit: Annotated[int, Field(ge=1, le=200, description="Groups to return per call.")] = 50,
    offset: Annotated[int, Field(ge=0, le=100000, description="Groups to skip, for paging.")] = 0,
) -> str:
    """List the groups this number belongs to, sorted by subject.

    Each group has its group_id (…@g.us), subject, size, owner, creation time, whether only admins can send
    (announce_only), whether only admins can edit group info (locked) and whether it is a community. Evolution has no
    group search, so name_contains is applied here to the full list; total counts the groups after that filter and
    has_more tells whether another page follows. get_group returns one group with its participants. Available on
    WhatsApp Web (Baileys) instances only.
    """
    conn, client = await context.resolve()
    rows = await _all_groups(client, conn.identity)
    groups = [_summary(row) for row in rows]
    if name_contains:
        needle = name_contains.casefold()
        groups = [g for g in groups if needle in (g["subject"] or "").casefold()]
    groups.sort(key=lambda g: (g["subject"] or "").casefold())
    return tool_result(
        {
            "groups": groups[offset : offset + limit],
            "total": len(groups),
            "offset": offset,
            "limit": limit,
            "has_more": offset + limit < len(groups),
        }
    )


@registry.tool(
    title="Get group details",
    toolset="groups",
    kind="read",
    idempotent=True,
    integrations=_BAILEYS,
)
async def get_group(group: Group) -> str:
    """Get one group's subject, description, settings and participants.

    Returns the group_id, subject, description, owner, creation time, size, announce_only (only admins can send),
    locked (only admins can edit group info), picture_url, is_community and the participants with their chat_id, phone
    number (when WhatsApp exposes it) and admin role ('admin', 'superadmin' or null). Groups with more than 500
    participants list the first 500 and set participants_truncated. list_groups finds group ids. Available on WhatsApp
    Web (Baileys) instances only.
    """
    conn, client = await context.resolve()
    group_jid = await calls.resolve_group(client, conn, group, purpose="read")
    row = await _group_row(client, conn.identity, group_jid)
    participants = row.get("participants") if isinstance(row.get("participants"), list) else []
    summary = _summary(row)
    result = {
        "group_id": summary["group_id"] or group_jid,
        "subject": summary["subject"],
        "description": row.get("desc"),
        "owner": summary["owner"],
        "created_at": summary["created_at"],
        "size": summary["size"],
        "announce_only": summary["announce_only"],
        "locked": summary["locked"],
    }
    if row.get("ephemeralDuration") is not None:
        result["ephemeral_seconds"] = row["ephemeralDuration"]
    result["picture_url"] = row.get("pictureUrl")
    result["is_community"] = summary["is_community"]
    result["participants"] = [_participant(p) for p in participants[:_MAX_PARTICIPANTS_SHOWN]]
    if len(participants) > _MAX_PARTICIPANTS_SHOWN:
        result["participants_truncated"] = True
    return tool_result(result)


@registry.tool(
    title="Get group invite link",
    toolset="groups",
    kind="read",
    idempotent=True,
    integrations=_BAILEYS,
)
async def get_group_invite_link(group: Group) -> str:
    """Get the current invite link of a group.

    Returns the group_id, the invite_url (https://chat.whatsapp.com/…) and the bare invite_code. Anyone who has the
    link can join, so sharing it is the user's decision. Reading the link changes nothing; revoke_group_invite_link
    replaces it with a new one. Evolution answers with an error when this number is not allowed to read the link.
    Available on WhatsApp Web (Baileys) instances only.
    """
    conn, client = await context.resolve()
    group_jid = await calls.resolve_group(client, conn, group, purpose="read")
    body = _dict(await calls.call(client, conn.identity, "GET", "group/inviteCode", params={"groupJid": group_jid}))
    code = body.get("inviteCode")
    return tool_result(
        {"group_id": group_jid, "invite_url": body.get("inviteUrl") or _invite_url(code), "invite_code": code}
    )


@registry.tool(
    title="Preview group invite",
    toolset="groups",
    kind="read",
    idempotent=True,
    integrations=_BAILEYS,
)
async def get_invite_info(
    invite: Annotated[
        str,
        Field(
            min_length=22,
            max_length=200,
            description="Invite link (https://chat.whatsapp.com/<code>) or the bare 22-character invite code.",
        ),
    ],
) -> str:
    """Preview a group from its invite link or code without joining it.

    Returns the group_id, subject, description, size, owner and creation time of the group the invite leads to.
    join_group_by_invite joins it. Available on WhatsApp Web (Baileys) instances only.
    """
    conn, client = await context.resolve()
    code = calls.invite_code(invite)
    row = _dict(await calls.call(client, conn.identity, "GET", "group/inviteInfo", params={"inviteCode": code}))
    summary = _summary(row)
    return tool_result(
        {
            "group_id": summary["group_id"],
            "subject": summary["subject"],
            "description": row.get("desc"),
            "size": summary["size"],
            "owner": summary["owner"],
            "created_at": summary["created_at"],
        }
    )


# --- create and edit -------------------------------------------------------------------------------------------


@registry.tool(
    title="Create group",
    toolset="groups",
    kind="destructive",
    idempotent=False,
    integrations=_BAILEYS,
)
async def create_group(
    subject: Annotated[str, Field(min_length=1, max_length=100, description="Group name.")],
    participants: Annotated[
        list[str],
        Field(
            min_length=1,
            max_length=256,
            description="International phone numbers of the people to add (country code first).",
        ),
    ],
    description: Annotated[
        str | None, Field(min_length=1, max_length=2048, description="Optional group description.")
    ] = None,
) -> str:
    """Create a new group with the given participants.

    The named people are added to the group and see it in their chats, so this reaches other people. Evolution skips
    numbers that are not on WhatsApp without reporting them; compare the returned size with the number of people
    given. Returns the group_id, subject and size. Calling it twice creates two groups. Available on WhatsApp Web
    (Baileys) instances only.
    """
    conn, client = await context.resolve()
    numbers = await _phones(client, conn, participants, purpose="send")
    body: dict = {"subject": subject, "participants": numbers}
    if description is not None:
        body["description"] = description

    async def reread() -> object:
        return [
            {"group_id": g["group_id"], "subject": g["subject"], "size": g["size"]}
            for g in map(_summary, await _all_groups(client, conn.identity))
            if g["subject"] == subject
        ]

    row = _dict(await calls.call(client, conn.identity, "POST", "group/create", json=body, write=True, reread=reread))
    summary = _summary(row)
    return tool_result(
        {"group_id": summary["group_id"], "subject": summary["subject"] or subject, "size": summary["size"]}
    )


@registry.tool(
    title="Rename group",
    toolset="groups",
    kind="destructive",
    idempotent=True,
    integrations=_BAILEYS,
)
async def update_group_subject(
    group: Group,
    subject: Annotated[str, Field(min_length=1, max_length=100, description="New group name.")],
) -> str:
    """Rename a group.

    The previous name is replaced and every participant sees the change. Returns the group_id and updated: subject.
    Requires that this number is allowed to edit group info (an admin when the group is locked). Available on
    WhatsApp Web (Baileys) instances only.
    """
    return await _update_field(group, "group/updateGroupSubject", "subject", {"subject": subject})


@registry.tool(
    title="Update group description",
    toolset="groups",
    kind="destructive",
    idempotent=True,
    integrations=_BAILEYS,
)
async def update_group_description(
    group: Group,
    description: Annotated[str, Field(min_length=1, max_length=2048, description="New group description.")],
) -> str:
    """Replace a group's description.

    The previous description is replaced and every participant sees the change. Evolution rejects an empty
    description, so a description cannot be cleared through this tool. Returns the group_id and updated: description.
    Requires that this number is allowed to edit group info. Available on WhatsApp Web (Baileys) instances only.
    """
    return await _update_field(group, "group/updateGroupDescription", "description", {"description": description})


@registry.tool(
    title="Update group picture",
    toolset="groups",
    kind="destructive",
    idempotent=True,
    integrations=_BAILEYS,
)
async def update_group_picture(
    group: Group,
    image_url: MediaUrl,
) -> str:
    """Replace a group's picture with an image downloaded from a URL.

    Evolution downloads the image and sets it as the group picture; the previous picture is replaced and every
    participant sees the change. Returns the group_id and updated: picture. Requires that this number is allowed to
    edit group info. Available on WhatsApp Web (Baileys) instances only.
    """
    return await _update_field(group, "group/updateGroupPicture", "picture", {"image": image_url})


@registry.tool(
    title="Update group settings",
    toolset="groups",
    kind="destructive",
    idempotent=True,
    integrations=_BAILEYS,
)
async def update_group_settings(
    group: Group,
    only_admins_send: Annotated[
        bool | None,
        Field(description="True: only admins can send messages. False: every participant can. Omit to keep."),
    ] = None,
    only_admins_edit_info: Annotated[
        bool | None,
        Field(
            description=(
                "True: only admins can edit the subject, description and picture. False: every participant can. "
                "Omit to keep."
            )
        ),
    ] = None,
) -> str:
    """Change who may send messages in a group and who may edit its info.

    At least one of only_admins_send and only_admins_edit_info is required; the one left out stays as it is. The
    change replaces the current setting and every participant sees it. Returns the values that were applied. This
    number needs to be a group admin. set_disappearing_messages handles message expiry, and update_group_admins
    changes who is an admin. Available on WhatsApp Web (Baileys) instances only.
    """
    if only_admins_send is None and only_admins_edit_info is None:
        raise ToolExecutionError(
            "Give at least one of only_admins_send and only_admins_edit_info. Nothing was changed."
        )
    conn, client = await context.resolve()
    group_jid = await calls.resolve_group(client, conn, group, purpose="write")
    steps: list[tuple[str, bool, str]] = []
    if only_admins_send is not None:
        steps.append(("only_admins_send", only_admins_send, "announcement" if only_admins_send else "not_announcement"))
    if only_admins_edit_info is not None:
        action = "locked" if only_admins_edit_info else "unlocked"
        steps.append(("only_admins_edit_info", only_admins_edit_info, action))

    async def reread() -> object:
        row = await _group_row(client, conn.identity, group_jid)
        return {"only_admins_send": bool(row.get("announce")), "only_admins_edit_info": bool(row.get("restrict"))}

    applied: dict[str, bool] = {}
    for name, value, action in steps:
        try:
            await calls.call(
                client,
                conn.identity,
                "POST",
                "group/updateSetting",
                json={"groupJid": group_jid, "action": action},
                write=True,
                reread=reread,
            )
        except ToolExecutionError as exc:
            if not applied:
                raise
            done = ", ".join(f"{key}={val}" for key, val in applied.items())
            raise ToolExecutionError(f"{exc} Already applied before this failure: {done}.") from exc
        applied[name] = value
    return tool_result({"group_id": group_jid, **applied})


@registry.tool(
    title="Set disappearing messages",
    toolset="groups",
    kind="destructive",
    idempotent=True,
    integrations=_BAILEYS,
)
async def set_disappearing_messages(
    group: Group,
    duration: Annotated[
        Literal["off", "24h", "7d", "90d"],
        Field(description="How long new messages stay before they disappear: 'off', '24h', '7d' or '90d'."),
    ],
) -> str:
    """Turn disappearing messages on or off for a group.

    New messages in the group disappear after the chosen time; messages already sent are not affected. The setting
    replaces the current one and every participant sees the change. Returns the group_id and the chosen duration.
    This number needs to be allowed to change group settings. Available on WhatsApp Web (Baileys) instances only.
    """
    conn, client = await context.resolve()
    group_jid = await calls.resolve_group(client, conn, group, purpose="write")
    await calls.call(
        client,
        conn.identity,
        "POST",
        "group/toggleEphemeral",
        json={"groupJid": group_jid, "expiration": _EPHEMERAL_SECONDS[duration]},
        write=True,
    )
    return tool_result({"group_id": group_jid, "duration": duration})


# --- membership ------------------------------------------------------------------------------------------------


@registry.tool(
    title="Add group participants",
    toolset="groups",
    kind="destructive",
    idempotent=True,
    integrations=_BAILEYS,
)
async def add_group_participants(group: Group, participants: Phones) -> str:
    """Add people to a group.

    The added people are told they were added, and the group sees them join. Returns one result per number with a
    status_code and status: 'done', 'needs an invite (privacy settings)' when the person only accepts an invite,
    'left recently', 'already in group' or 'failed'. send_group_invite sends the invite link to people who need one.
    This number needs to be a group admin, or the group must let members add people. Available on WhatsApp Web
    (Baileys) instances only.
    """
    return await _update_participants(group, "add", participants)


@registry.tool(
    title="Remove group participants",
    toolset="groups",
    kind="destructive",
    idempotent=True,
    integrations=_BAILEYS,
)
async def remove_group_participants(group: Group, participants: Phones) -> str:
    """Remove people from a group.

    The removed people lose access to the group and the group sees that they were removed. They can join again only
    if they are added or invited. Returns one result per number with a status_code and status ('done' or 'failed').
    This number needs to be a group admin. Available on WhatsApp Web (Baileys) instances only. This tool is in the
    server's default deny list, so an operator has to allow it explicitly.
    """
    return await _update_participants(group, "remove", participants)


@registry.tool(
    title="Promote or demote group admins",
    toolset="groups",
    kind="destructive",
    idempotent=True,
    integrations=_BAILEYS,
)
async def update_group_admins(
    group: Group,
    participants: Phones,
    action: Annotated[
        Literal["promote", "demote"],
        Field(description="'promote' makes the people group admins; 'demote' removes their admin role."),
    ],
) -> str:
    """Promote participants to group admin or demote admins to regular participants.

    The people concerned and the group see the change. Returns one result per number with a status_code and status
    ('done' or 'failed'). The numbers have to be participants of the group already; add_group_participants adds
    people. This number needs to be a group admin. Available on WhatsApp Web (Baileys) instances only.
    """
    return await _update_participants(group, action, participants)


@registry.tool(
    title="Send group invite",
    toolset="groups",
    kind="destructive",
    idempotent=False,
    integrations=_BAILEYS,
)
async def send_group_invite(
    group: Group,
    numbers: Annotated[
        list[str],
        Field(
            min_length=1,
            max_length=50,
            description="International phone numbers to send the invite to (country code first), up to 50.",
        ),
    ],
    message: Annotated[str, Field(min_length=1, max_length=1024, description="Text sent before the invite link.")],
) -> str:
    """Send a group's invite link to people in private chats.

    Each number receives one message made of the given text followed by the group's invite link. The messages are
    delivered to real people and cannot be recalled, and sending twice delivers twice. Returns sent and the invite_url
    that was shared. add_group_participants adds people directly instead. This number needs to be able to read the
    group's invite link. Available on WhatsApp Web (Baileys) instances only.
    """
    conn, client = await context.resolve()
    group_jid = await calls.resolve_group(client, conn, group, purpose="write")
    recipients = await _phones(client, conn, numbers, purpose="send")
    body = _dict(
        await calls.call(
            client,
            conn.identity,
            "POST",
            "group/sendInvite",
            json={"groupJid": group_jid, "description": message, "numbers": recipients},
            write=True,
        )
    )
    return tool_result({"sent": True, "invite_url": body.get("inviteUrl")})


@registry.tool(
    title="Reset group invite link",
    toolset="groups",
    kind="destructive",
    idempotent=False,
    integrations=_BAILEYS,
)
async def revoke_group_invite_link(group: Group) -> str:
    """Reset a group's invite link so the old link stops working.

    Anyone who only has the old link can no longer join; people already in the group stay. A new link is created and
    returned as invite_url with its invite_code. Each call creates another new link and invalidates the previous one.
    This number needs to be a group admin. Available on WhatsApp Web (Baileys) instances only.
    """
    conn, client = await context.resolve()
    group_jid = await calls.resolve_group(client, conn, group, purpose="write")
    body = _dict(
        await calls.call(
            client, conn.identity, "POST", "group/revokeInviteCode", json={"groupJid": group_jid}, write=True
        )
    )
    code = body.get("inviteCode")
    return tool_result({"group_id": group_jid, "invite_code": code, "invite_url": _invite_url(code)})


@registry.tool(
    title="Join group by invite",
    toolset="groups",
    kind="destructive",
    idempotent=True,
    integrations=_BAILEYS,
)
async def join_group_by_invite(
    invite: Annotated[
        str,
        Field(
            min_length=22,
            max_length=200,
            description="Invite link (https://chat.whatsapp.com/<code>) or the bare 22-character invite code.",
        ),
    ],
) -> str:
    """Join a group through its invite link or code.

    This number becomes a participant and the group sees it join. get_invite_info previews the group first. Leaving
    again is possible with leave_group. Returns joined and the group_id. Available on WhatsApp Web (Baileys)
    instances only.
    """
    conn, client = await context.resolve()
    code = calls.invite_code(invite)
    body = _dict(
        await calls.call(
            client, conn.identity, "GET", "group/acceptInviteCode", params={"inviteCode": code}, write=True
        )
    )
    return tool_result({"joined": True, "group_id": _group_id(body.get("groupJid"))})


@registry.tool(
    title="Leave group",
    toolset="groups",
    kind="irreversible",
    idempotent=True,
    integrations=_BAILEYS,
)
async def leave_group(group: Group) -> str:
    """Leave a group.

    This number is removed from the group and the group sees it leave. Rejoining needs an invite or an admin adding
    it back, so the step cannot be undone from this server. Returns left and the group_id. Available on WhatsApp Web
    (Baileys) instances only.
    """
    conn, client = await context.resolve()
    group_jid = await calls.resolve_group(client, conn, group, purpose="write")
    await calls.call(client, conn.identity, "DELETE", "group/leaveGroup", params={"groupJid": group_jid}, write=True)
    return tool_result({"left": True, "group_id": group_jid})
