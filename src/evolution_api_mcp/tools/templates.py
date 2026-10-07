"""Templates toolset: WhatsApp Business Platform message templates (WHATSAPP-BUSINESS instances)."""

from typing import Annotated, Any, Literal

from pydantic import Field

from evolution_api_mcp import calls, context, registry, sending
from evolution_api_mcp.client import EvolutionHTTPError
from evolution_api_mcp.errors import UNCERTAIN_4XX_MARKERS, ToolExecutionError, tool_result

BUS = frozenset({registry.BUSINESS})


def _meta_refusal(exc: EvolutionHTTPError) -> None:
    """Turn Evolution's 400 for a Meta rejection into a refusal that names Meta's reason and code.

    Template routes report every Meta rejection as HTTP 400 with `details.whatsapp_code`. A 400 that only wraps a
    lost connection is left to the standard handling, which reports it as UNCERTAIN for writes.
    """
    if exc.status != 400 or any(marker in exc.message for marker in UNCERTAIN_4XX_MARKERS):
        return
    details = exc.body.get("details") if isinstance(exc.body, dict) else None
    code = details.get("whatsapp_code") if isinstance(details, dict) else None
    suffix = f" (code {code})" if code and code != "UNKNOWN_ERROR" else ""
    raise ToolExecutionError(
        f"WhatsApp Business Platform refused the request: {exc.message.rstrip(' .')}{suffix}. Nothing was changed."
    )


def _template_view(item: dict) -> dict:
    return {
        "template_id": item.get("id"),
        "name": item.get("name"),
        "language": item.get("language"),
        "status": item.get("status"),
        "category": item.get("category"),
        "components": item.get("components"),
    }


async def _find_templates(client, identity) -> list[dict]:
    """Meta's template list as Evolution relays it (a bare array; a `data` wrapper is accepted too)."""
    body = await calls.call(client, identity, "GET", "template/find", on_http_error=_meta_refusal)
    if isinstance(body, dict):
        body = body.get("data")
    return [item for item in body if isinstance(item, dict)] if isinstance(body, list) else []


@registry.tool(
    title="List message templates",
    toolset="templates",
    kind="read",
    idempotent=True,
    integrations=BUS,
)
async def list_templates(
    name_contains: Annotated[
        str | None,
        Field(min_length=1, max_length=512, description="Keep only templates whose name contains this text."),
    ] = None,
    status: Annotated[
        Literal["APPROVED", "PENDING", "REJECTED", "PAUSED", "DISABLED"] | None,
        Field(description="Keep only templates with this approval status."),
    ] = None,
) -> str:
    """List the message templates of this WhatsApp Business Platform instance with their approval status.

    Only approved templates can be sent (send_template_message). Each entry has the template id, name, language,
    status, category and components. Evolution relays only the first page of templates that Meta returns, and the
    name and status filters are applied on that page. Meta's reference:
    https://developers.facebook.com/docs/whatsapp/business-management-api/message-templates
    """
    conn, client = await context.resolve()
    items = await _find_templates(client, conn.identity)
    needle = name_contains.casefold() if name_contains else None
    templates = [
        _template_view(item)
        for item in items
        if (needle is None or needle in str(item.get("name") or "").casefold())
        and (status is None or item.get("status") == status)
    ]
    return tool_result({"templates": templates})


@registry.tool(
    title="Send template message",
    toolset="templates",
    kind="destructive",
    idempotent=False,
    integrations=BUS,
)
async def send_template_message(
    chat: Annotated[
        str,
        Field(
            min_length=3,
            max_length=128,
            description="International phone number of the recipient (country code first, e.g. 393331234567).",
        ),
    ],
    template_name: Annotated[
        str, Field(min_length=1, max_length=512, description="Name of an approved template, from list_templates.")
    ],
    language: Annotated[
        str,
        Field(min_length=2, max_length=15, description="Language code of the template version, e.g. it or en_US."),
    ],
    body_parameters: Annotated[
        list[str] | None,
        Field(
            max_length=20,
            description="Texts for the {{1}}, {{2}}… placeholders of the template body, in order. Excludes components.",
        ),
    ] = None,
    components: Annotated[
        list[dict[str, Any]] | None,
        Field(
            description=(
                "Full Cloud API component list (header, body, button parameters) for templates that need more than "
                "body text. Excludes body_parameters."
            ),
        ),
    ] = None,
) -> str:
    """Send an approved message template to a phone number through the WhatsApp Business Platform.

    Templates are the only messages a person receives outside the 24-hour window that opens when they write to this
    number; use send_text_message inside the window. body_parameters fills the numbered placeholders of the body;
    components takes the raw Cloud API component list for headers and buttons, and the two exclude each other. The
    recipient receives a real message that this server cannot recall, and Meta charges per conversation. Returns the
    message id, chat id, status and timestamp. Meta's references:
    https://developers.facebook.com/docs/whatsapp/cloud-api/guides/send-message-templates and
    https://developers.facebook.com/docs/whatsapp/business-management-api/message-templates
    """
    if body_parameters is not None and components is not None:
        raise ToolExecutionError("Give body_parameters or components, not both. Nothing was sent.")
    conn, client = await context.resolve()
    chat_jid = await calls.resolve_chat(client, conn, chat, purpose="send")
    number = sending.recipient(conn.identity, chat_jid)
    if components is not None:
        parts: list[dict[str, Any]] = components
    elif body_parameters:
        parts = [{"type": "body", "parameters": [{"type": "text", "text": value} for value in body_parameters]}]
    else:
        parts = []
    body = {"number": number, "name": template_name, "language": language, "components": parts}
    return tool_result(await sending.send(client, conn.identity, conn, "message/sendTemplate", body, chat_jid))


@registry.tool(
    title="Create message template",
    toolset="templates",
    kind="write",
    idempotent=False,
    integrations=BUS,
)
async def create_template(
    name: Annotated[
        str,
        Field(
            min_length=1,
            max_length=512,
            pattern=r"^[a-z0-9_]{1,512}$",
            description="Template name: lowercase letters, digits and underscores.",
        ),
    ],
    category: Annotated[
        Literal["MARKETING", "UTILITY", "AUTHENTICATION"],
        Field(description="Meta template category; it decides pricing and review rules."),
    ],
    language: Annotated[
        str, Field(min_length=2, max_length=15, description="Language code of this template version, e.g. it or en_US.")
    ],
    components: Annotated[
        list[dict[str, Any]],
        Field(
            min_length=1,
            max_length=10,
            description="Cloud API component objects (HEADER, BODY, FOOTER, BUTTONS) in Meta's template format.",
        ),
    ],
    allow_category_change: Annotated[
        bool, Field(description="Let Meta pick another category when it disagrees with the one given.")
    ] = False,
) -> str:
    """Submit a new message template to Meta for review on this WhatsApp Business Platform instance.

    The template is a private draft until Meta approves it; nothing is sent to any person. Approval status shows in
    list_templates and may take minutes to hours. Meta rejects malformed components with a reason that this tool
    passes on. Returns the template id, name and initial status. Component format:
    https://developers.facebook.com/docs/whatsapp/business-management-api/message-templates
    """
    conn, client = await context.resolve()
    identity = conn.identity

    async def reread() -> object:
        items = await _find_templates(client, identity)
        return [_template_view(item) for item in items if item.get("name") == name]

    answer = await calls.call(
        client,
        identity,
        "POST",
        "template/create",
        json={
            "name": name,
            "category": category,
            "language": language,
            "components": components,
            "allowCategoryChange": allow_category_change,
        },
        write=True,
        reread=reread,
        on_http_error=_meta_refusal,
    )
    row = answer if isinstance(answer, dict) else {}
    meta = row.get("template") if isinstance(row.get("template"), dict) else {}
    return tool_result(
        {"template_id": row.get("templateId"), "name": row.get("name") or name, "status": meta.get("status")}
    )


@registry.tool(
    title="Edit message template",
    toolset="templates",
    kind="destructive",
    idempotent=True,
    integrations=BUS,
)
async def edit_template(
    template_id: Annotated[
        str, Field(min_length=1, max_length=64, description="Template id from list_templates (template_id).")
    ],
    components: Annotated[
        list[dict[str, Any]] | None,
        Field(min_length=1, max_length=10, description="Replacement Cloud API component objects."),
    ] = None,
    category: Annotated[
        Literal["MARKETING", "UTILITY", "AUTHENTICATION"] | None,
        Field(description="New Meta template category."),
    ] = None,
    allow_category_change: Annotated[
        bool | None, Field(description="Let Meta pick another category when it disagrees with the one given.")
    ] = None,
) -> str:
    """Change the components or category of an existing message template on this WhatsApp Business Platform instance.

    Give at least one of components, category or allow_category_change. Edited templates go through Meta's review
    again and Meta limits how often and how many times a template can be edited; Meta's refusal reason is passed on.
    Returns the template id. Format:
    https://developers.facebook.com/docs/whatsapp/business-management-api/message-templates
    """
    if components is None and category is None and allow_category_change is None:
        raise ToolExecutionError("Give components, category or allow_category_change to change. Nothing was changed.")
    body: dict[str, object] = {"templateId": template_id}
    if components is not None:
        body["components"] = components
    if category is not None:
        body["category"] = category
    if allow_category_change is not None:
        body["allowCategoryChange"] = allow_category_change
    conn, client = await context.resolve()
    identity = conn.identity

    async def reread() -> object:
        items = await _find_templates(client, identity)
        return [_template_view(item) for item in items if item.get("id") == template_id]

    await calls.call(
        client, identity, "POST", "template/edit", json=body, write=True, reread=reread, on_http_error=_meta_refusal
    )
    return tool_result({"template_id": template_id, "updated": True})


@registry.tool(
    title="Delete message template",
    toolset="templates",
    kind="irreversible",
    idempotent=True,
    integrations=BUS,
)
async def delete_template(
    name: Annotated[str, Field(min_length=1, max_length=512, description="Name of the template to delete.")],
    template_id: Annotated[
        str | None,
        Field(
            min_length=1,
            max_length=64,
            description=(
                "Delete only this language version (template_id from list_templates); omit to delete every language."
            ),
        ),
    ] = None,
) -> str:
    """Delete a message template from this WhatsApp Business Platform instance.

    Without template_id every language version of the name is deleted; with it only that version. Deleted templates
    cannot be sent any more and cannot be restored from this server; Meta blocks reusing the name for a while.
    Returns the deleted name. Meta's reference:
    https://developers.facebook.com/docs/whatsapp/business-management-api/message-templates
    """
    conn, client = await context.resolve()
    identity = conn.identity
    body: dict[str, object] = {"name": name}
    if template_id is not None:
        body["hsmId"] = template_id

    async def reread() -> object:
        items = await _find_templates(client, identity)
        return [_template_view(item) for item in items if item.get("name") == name]

    await calls.call(
        client,
        identity,
        "DELETE",
        "template/delete",
        json=body,
        write=True,
        reread=reread,
        on_http_error=_meta_refusal,
    )
    return tool_result({"deleted": True, "name": name})
