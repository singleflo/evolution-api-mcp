"""Labels toolset: WhatsApp Business app labels on chats."""

from typing import Annotated, Literal

from pydantic import Field

from evolution_api_mcp import calls, context, registry
from evolution_api_mcp.errors import tool_result
from evolution_api_mcp.tools import Chat
from evolution_api_mcp.tools.contacts import compact, person

LabelId = Annotated[
    str,
    Field(min_length=1, max_length=64, description="Label id from list_labels."),
]


@registry.tool(
    title="List labels",
    toolset="labels",
    kind="read",
    idempotent=True,
    integrations=frozenset({registry.BAILEYS}),
)
async def list_labels() -> str:
    """List the labels defined on this WhatsApp account.

    Labels are the colored tags of the WhatsApp Business app; personal accounts have none. Returns label_id, name
    and color for each label. The label_id is what add_chat_label and remove_chat_label take. An empty list means
    no label has been synced to Evolution yet.
    """
    conn, client = await context.resolve()
    body = await calls.call(client, conn.identity, "GET", "label/findLabels")
    rows = [row for row in body if isinstance(row, dict)] if isinstance(body, list) else []
    labels = [compact({"label_id": row.get("id"), "name": row.get("name"), "color": row.get("color")}) for row in rows]
    return tool_result({"labels": labels})


async def _handle(chat: str, label_id: str, action: Literal["add", "remove"]) -> str:
    conn, client = await context.resolve()
    chat_jid, digits = person(chat)
    body = await calls.call(
        client,
        conn.identity,
        "POST",
        "label/handleLabel",
        json={"number": digits, "labelId": label_id, "action": action},
        write=True,
    )
    data = body if isinstance(body, dict) else {}
    return tool_result(
        {
            "chat_id": data.get("numberJid") or chat_jid,
            "label_id": label_id,
            "applied": bool(data.get(action)),
        }
    )


@registry.tool(
    title="Add label to chat",
    toolset="labels",
    kind="write",
    idempotent=True,
    integrations=frozenset({registry.BAILEYS}),
)
async def add_chat_label(chat: Chat, label_id: LabelId) -> str:
    """Attach a label to the chat with one person.

    Takes a phone number or a person's chat_id and a label_id from list_labels. Labels are private to this account
    and are not visible to the person. Attaching a label that is already attached changes nothing. Returns chat_id,
    label_id and applied (true when Evolution confirmed the change). remove_chat_label undoes it.
    """
    return await _handle(chat, label_id, "add")


@registry.tool(
    title="Remove label from chat",
    toolset="labels",
    kind="write",
    idempotent=True,
    integrations=frozenset({registry.BAILEYS}),
)
async def remove_chat_label(chat: Chat, label_id: LabelId) -> str:
    """Detach a label from the chat with one person.

    Takes a phone number or a person's chat_id and a label_id from list_labels. Labels are private to this account
    and are not visible to the person. Detaching a label that is not attached changes nothing. Returns chat_id,
    label_id and applied (true when Evolution confirmed the change).
    """
    return await _handle(chat, label_id, "remove")
