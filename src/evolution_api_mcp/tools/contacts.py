"""Contacts toolset: find contacts, check numbers on WhatsApp, profiles, block and unblock."""

from collections.abc import Callable
from typing import Annotated

from pydantic import Field

from evolution_api_mcp import calls, context, jid, registry
from evolution_api_mcp.client import EvolutionHTTPError
from evolution_api_mcp.errors import ToolExecutionError, tool_result
from evolution_api_mcp.sending import _absent_number
from evolution_api_mcp.tools import Chat

SCAN_PAGE_SIZE = 500
SCAN_ROW_LIMIT = 5_000
_BUSINESS_KEYS = ("description", "category", "email", "website", "address", "business_hours")


def compact(values: dict) -> dict:
    """`values` without the entries whose value is None."""
    return {key: value for key, value in values.items() if value is not None}


def person(chat: str) -> tuple[str, str]:
    """(chat id, phone digits) of a person; groups and @lid ids are refused."""
    chat_jid = calls.chat(chat)
    digits = jid.phone_of(chat_jid)
    if digits is None:
        raise ToolExecutionError(f"{chat} is not a phone number; this tool takes international phone numbers.")
    return chat_jid, digits


def refuse_absent_number(digits: str) -> Callable[[EvolutionHTTPError], None]:
    """An `on_http_error` hook: Evolution's 400 `{exists: false}` becomes a clear refusal for `digits`."""

    def hook(exc: EvolutionHTTPError) -> None:
        if exc.status == 400 and _absent_number(exc.body) is not None:
            raise ToolExecutionError(f"{digits} is not on WhatsApp.")

    return hook


def _rows(body: object) -> list[dict]:
    return [row for row in body if isinstance(row, dict)] if isinstance(body, list) else []


def _contact(row: dict) -> dict:
    chat_id = row["remoteJid"]
    return compact(
        {
            "chat_id": chat_id,
            "name": row.get("pushName"),
            "phone": jid.phone_of(chat_id),
            "is_group": jid.is_group(chat_id),
            "saved": bool(row.get("isSaved")),
        }
    )


@registry.tool(title="Find contacts", toolset="contacts", kind="read", idempotent=True)
async def find_contacts(
    name: Annotated[
        str | None,
        Field(
            min_length=1,
            max_length=100,
            description="Part of the contact's WhatsApp display name; matched without regard to case.",
        ),
    ] = None,
    phone: Annotated[
        str | None,
        Field(
            min_length=3,
            max_length=128,
            description="International phone number or chat_id of one contact to look up exactly.",
        ),
    ] = None,
    include_groups: Annotated[bool, Field(description="Also return group chats. Default: people only.")] = False,
    limit: Annotated[int, Field(ge=1, le=100, description="Maximum contacts to return.")] = 20,
    offset: Annotated[int, Field(ge=0, le=10000, description="Matching contacts to skip, for paging.")] = 0,
) -> str:
    """Find stored contacts by display name or phone number.

    Evolution matches names exactly, so this tool filters names itself over the stored contacts (the first 5,000
    are scanned). A phone number looks up that one contact directly. Without either filter it lists contacts in
    stored order. Contacts are the people Evolution has seen on this instance: saved contacts and people met in
    chats or groups. Returns chat_id, name, phone, is_group and saved for each match, plus has_more and how many
    stored contacts were scanned. For chats with recent activity, list_chats is the sibling tool.
    """
    conn, client = await context.resolve()
    identity = conn.identity
    wanted = name.casefold() if name else None

    def keep(row: dict) -> bool:
        remote = row.get("remoteJid")
        if not isinstance(remote, str) or not remote:
            return False
        if not include_groups and jid.is_group(remote):
            return False
        return wanted is None or wanted in str(row.get("pushName") or "").casefold()

    end = offset + limit
    matches: list[dict] = []
    scanned = 0
    capped = False

    if phone is not None:
        chat_jid = calls.chat(phone)
        rows = _rows(
            await calls.call(client, identity, "POST", "chat/findContacts", json={"where": {"remoteJid": chat_jid}})
        )
        scanned = len(rows)
        matches = [_contact(row) for row in rows if keep(row)]
    else:
        page = 1
        exhausted = False
        while scanned < SCAN_ROW_LIMIT and len(matches) <= end:
            rows = _rows(
                await calls.call(
                    client,
                    identity,
                    "POST",
                    "chat/findContacts",
                    json={"where": {}, "offset": SCAN_PAGE_SIZE, "page": page},
                )
            )
            scanned += len(rows)
            matches.extend(_contact(row) for row in rows if keep(row))
            if len(rows) < SCAN_PAGE_SIZE:
                exhausted = True
                break
            page += 1
        capped = not exhausted and len(matches) <= end and scanned >= SCAN_ROW_LIMIT

    result: dict = {
        "contacts": matches[offset:end],
        "offset": offset,
        "limit": limit,
        "has_more": len(matches) > end,
        "scanned": scanned,
    }
    if capped:
        result["note"] = f"Only the first {SCAN_ROW_LIMIT} stored contacts were scanned; narrow with name or phone."
    return tool_result(result)


@registry.tool(
    title="Check numbers on WhatsApp",
    toolset="contacts",
    kind="read",
    idempotent=True,
    integrations=frozenset({registry.BAILEYS}),
)
async def check_whatsapp_numbers(
    numbers: Annotated[
        list[str],
        Field(
            min_length=1,
            max_length=50,
            description="International phone numbers (country code first, e.g. 393331234567 or +39 333 123 4567).",
        ),
    ],
) -> str:
    """Check which phone numbers have a WhatsApp account.

    Takes phone numbers only; group and privacy (@lid) ids are refused. Returns one entry per input with
    on_whatsapp true or false and the chat_id Evolution resolved (Evolution corrects some Brazilian, Mexican and
    Argentine numbers), plus lid when WhatsApp reports a privacy id for the person. Nothing is sent to the numbers
    and they are not notified.
    """
    conn, client = await context.resolve()
    inputs: list[tuple[str, str]] = []
    for value in numbers:
        chat_jid = calls.chat(value)
        digits = jid.phone_of(chat_jid)
        if digits is None:
            raise ToolExecutionError(
                f"{value} is not a phone number; check_whatsapp_numbers takes international phone numbers."
            )
        inputs.append((value, digits))
    unique = list(dict.fromkeys(digits for _, digits in inputs))  # Evolution rejects repeated numbers

    body = await calls.call(client, conn.identity, "POST", "chat/whatsappNumbers", json={"numbers": unique})
    answers = {str(row.get("number")): row for row in _rows(body)}

    results = []
    for value, digits in inputs:
        answer = answers.get(digits)
        if answer is None:
            results.append({"input": value, "on_whatsapp": None})
            continue
        lid = answer.get("lid")
        results.append(
            compact(
                {
                    "input": value,
                    "on_whatsapp": bool(answer.get("exists")),
                    "chat_id": answer.get("jid"),
                    "lid": lid if isinstance(lid, str) and "@lid" in lid else None,
                }
            )
        )
    return tool_result({"results": results})


@registry.tool(
    title="Get contact profile",
    toolset="contacts",
    kind="read",
    idempotent=True,
    integrations=frozenset({registry.BAILEYS}),
)
async def get_contact_profile(chat: Chat) -> str:
    """Get the public WhatsApp profile of one person: name, about text, picture and business details.

    Takes a phone number or a person's chat_id; groups are not accepted. Returns chat_id, name, on_whatsapp,
    about, is_business, picture_url and, for business accounts, email, description and website. Fields the person
    hides with their privacy settings come back empty. A number without a WhatsApp account is reported as an
    error. get_business_profile returns the fuller business record.
    """
    conn, client = await context.resolve()
    chat_jid, digits = person(chat)
    body = await calls.call(
        client,
        conn.identity,
        "POST",
        "chat/fetchProfile",
        json={"number": digits},
        on_http_error=refuse_absent_number(digits),
    )
    return tool_result(profile_result(body, chat_jid))


def profile_result(body: object, chat_jid: str) -> dict:
    """The projection shared by get_contact_profile and get_my_profile."""
    data = body if isinstance(body, dict) else {}
    return compact(
        {
            "chat_id": data.get("wuid") or chat_jid,
            "name": data.get("name"),
            "on_whatsapp": data.get("numberExists"),
            "about": data.get("status"),
            "is_business": data.get("isBusiness"),
            "picture_url": data.get("picture"),
            "email": data.get("email"),
            "description": data.get("description"),
            "website": data.get("website"),
        }
    )


@registry.tool(
    title="Get business profile",
    toolset="contacts",
    kind="read",
    idempotent=True,
    integrations=frozenset({registry.BAILEYS}),
)
async def get_business_profile(chat: Chat) -> str:
    """Get the WhatsApp Business profile of a person or company.

    Takes a phone number or a person's chat_id. Returns is_business false when the account is a personal one;
    otherwise description, category, email, website, address and business_hours as far as the business publishes
    them. For a general look at any contact, get_contact_profile is the sibling tool.
    """
    conn, client = await context.resolve()
    chat_jid, digits = person(chat)
    body = await calls.call(
        client,
        conn.identity,
        "POST",
        "chat/fetchBusinessProfile",
        json={"number": digits},
        on_http_error=refuse_absent_number(digits),
    )
    data = body if isinstance(body, dict) else {}
    chat_id = data.get("jid") or chat_jid
    if not data.get("isBusiness"):
        return tool_result({"chat_id": chat_id, "is_business": False})
    result: dict = {"chat_id": chat_id, "is_business": True}
    result.update({key: data[key] for key in _BUSINESS_KEYS if data.get(key) is not None})
    return tool_result(result)


@registry.tool(
    title="Block contact",
    toolset="contacts",
    kind="destructive",
    idempotent=True,
    integrations=frozenset({registry.BAILEYS}),
)
async def block_contact(chat: Chat) -> str:
    """Block a person on WhatsApp.

    Takes a phone number or a person's chat_id. A blocked person can no longer message or call this number, and
    messages sent to them are not delivered. The block can be lifted with unblock_contact. Blocking a person who
    is already blocked changes nothing.
    """
    conn, client = await context.resolve()
    chat_jid, digits = person(chat)
    await calls.call(
        client,
        conn.identity,
        "POST",
        "chat/updateBlockStatus",
        json={"number": digits, "status": "block"},
        write=True,
    )
    return tool_result({"chat_id": chat_jid, "blocked": True})


@registry.tool(
    title="Unblock contact",
    toolset="contacts",
    kind="write",
    idempotent=True,
    integrations=frozenset({registry.BAILEYS}),
)
async def unblock_contact(chat: Chat) -> str:
    """Unblock a person on WhatsApp.

    Takes a phone number or a person's chat_id. The person can message and call this number again. Nothing is
    sent to them and they are not told. Unblocking a person who is not blocked changes nothing.
    """
    conn, client = await context.resolve()
    chat_jid, digits = person(chat)
    await calls.call(
        client,
        conn.identity,
        "POST",
        "chat/updateBlockStatus",
        json={"number": digits, "status": "unblock"},
        write=True,
    )
    return tool_result({"chat_id": chat_jid, "blocked": False})
