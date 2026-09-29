"""Status toolset: post status updates."""

from typing import Annotated, Literal

from pydantic import Field

from evolution_api_mcp import calls, context, jid, registry
from evolution_api_mcp.client import EvolutionHTTPError
from evolution_api_mcp.errors import ToolExecutionError, tool_result
from evolution_api_mcp.tools.contacts import compact

_NO_CONTACTS_MARKERS = ("StatusJidList is required", "Contacts not found")


def _refuse_no_contacts(exc: EvolutionHTTPError) -> None:
    """Evolution's 400 for an all-contacts status with nobody to show it to."""
    if exc.status == 400 and any(marker in exc.message for marker in _NO_CONTACTS_MARKERS):
        raise ToolExecutionError(
            "Evolution found no saved contacts to show this status to; pass audience with phone numbers. "
            "Nothing was posted."
        )


@registry.tool(
    title="Post status update",
    toolset="status",
    kind="destructive",
    idempotent=False,
    integrations=frozenset({registry.BAILEYS}),
)
async def post_status(
    type: Annotated[
        Literal["text", "image", "video", "audio"],
        Field(description="Kind of status: text, image, video or audio."),
    ],
    content: Annotated[
        str,
        Field(
            min_length=1,
            max_length=2048,
            description=(
                "For text: the status text. For image, video and audio: the public http(s) URL Evolution downloads "
                "the media from."
            ),
        ),
    ],
    caption: Annotated[
        str | None,
        Field(max_length=1024, description="Caption shown under an image or video status."),
    ] = None,
    background_color: Annotated[
        str,
        Field(pattern=r"^#[0-9A-Fa-f]{6}$", description="Background color of a text status as #RRGGBB."),
    ] = "#128C7E",
    font: Annotated[int, Field(ge=1, le=5, description="Font style of a text status, 1 to 5.")] = 1,
    audience: Annotated[
        list[str] | None,
        Field(
            min_length=1,
            max_length=256,
            description=(
                "Phone numbers that get to see the status. Omit to show it to all saved contacts (contacts with "
                "a known name)."
            ),
        ),
    ] = None,
) -> str:
    """Post a status update (a story that disappears after 24 hours) from this WhatsApp number.

    The status is shown to the chosen audience, or to every saved contact when no audience is given, and the
    people who see it can reply to it. A posted status cannot be recalled with this server. For image, video and
    audio the content is a media URL that Evolution downloads; for text it is the status text, with background_color
    and font applying to text statuses only. Returns posted, message_id and the audience size (or all_contacts).
    Evolution needs at least one saved contact, or an explicit audience, to post.
    """
    conn, client = await context.resolve()
    if type != "text" and not content.lower().startswith(("http://", "https://")):
        raise ToolExecutionError("For image, video and audio status updates, content is the media URL (http or https).")

    body: dict = {
        "type": type,
        "content": content,
        "backgroundColor": background_color,
        "font": font,
        "allContacts": audience is None,
    }
    if caption is not None:
        body["caption"] = caption
    recipients: list[str] = []
    if audience is not None:
        for value in audience:
            chat_jid = calls.chat(value)
            if jid.phone_of(chat_jid) is None:
                raise ToolExecutionError(
                    f"{value} is not a phone number; the status audience takes international phone numbers."
                )
            if chat_jid not in recipients:
                recipients.append(chat_jid)
        # Evolution hands this list to WhatsApp unchanged, and WhatsApp addresses people by full JID.
        body["statusJidList"] = recipients

    response = await calls.call(
        client,
        conn.identity,
        "POST",
        "message/sendStatus",
        json=body,
        write=True,
        on_http_error=_refuse_no_contacts if audience is None else None,
    )
    key = response.get("key") if isinstance(response, dict) else None
    message_id = key.get("id") if isinstance(key, dict) else None
    return tool_result(
        compact(
            {
                "posted": True,
                "message_id": message_id,
                "audience": "all_contacts" if audience is None else len(recipients),
            }
        )
    )
