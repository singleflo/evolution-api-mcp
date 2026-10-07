"""Contacts toolset: find chats by name or number, check numbers on WhatsApp, profiles, block and unblock."""

from collections.abc import Callable
from typing import Annotated, Literal

from pydantic import Field

from evolution_api_mcp import calls, context, directory, jid, messages, registry
from evolution_api_mcp.client import EvolutionHTTPError
from evolution_api_mcp.errors import ToolExecutionError, tool_result
from evolution_api_mcp.sending import _absent_number
from evolution_api_mcp.tools import Chat

_BUSINESS_KEYS = ("description", "category", "email", "website", "address", "business_hours")


def compact(values: dict) -> dict:
    """`values` without the entries whose value is None."""
    return {key: value for key, value in values.items() if value is not None}


def refuse_absent_number(digits: str) -> Callable[[EvolutionHTTPError], None]:
    """An `on_http_error` hook: Evolution's 400 `{exists: false}` becomes a clear refusal for `digits`."""

    def hook(exc: EvolutionHTTPError) -> None:
        if exc.status == 400 and _absent_number(exc.body) is not None:
            raise ToolExecutionError(f"{digits} is not on WhatsApp.")

    return hook


def _rows(body: object) -> list[dict]:
    return [row for row in body if isinstance(row, dict)] if isinstance(body, list) else []


@registry.tool(title="Find chats by name or number", toolset="contacts", kind="read", idempotent=True)
async def find_chats(
    query: Annotated[
        str,
        Field(min_length=2, max_length=100, description="Part of a name, or at least 3 digits of a phone number."),
    ],
    kind: Annotated[Literal["all", "people", "groups"], Field(description="People, groups, or both.")] = "all",
    limit: Annotated[int, Field(ge=1, le=50, description="Maximum number of chats.")] = 10,
) -> str:
    """Turn a name or phone number into chat_ids: people and groups whose name or number matches the query.

    Every chat parameter of the other tools also accepts an exact name, so this tool is for finding the right
    chat_id when a name is partial or shared. The directory it searches holds the stored contacts, the group
    subjects and the names seen on recent messages, so it needs no chat to exist yet. Names match without regard to
    case and accents; a query of at least 3 digits matches phone numbers instead. Best matches come first: exact,
    then prefix, then word prefix, then any part of the name. Returns chat_id, name, kind (person or group), phone
    and, for people, saved (a saved contact) for each chat, plus the total number of matches. For chats with
    recent activity, list_chats is the sibling tool.
    """
    conn, client = await context.resolve()
    names = await directory.get(client, conn)
    wanted = {"all": None, "people": "person", "groups": "group"}[kind]
    hits = [entry for entry in names.search(query, wanted) if not messages.is_noise_chat(entry.chat_id)]
    chats = []
    for entry in hits[:limit]:
        chats.append(
            compact(
                {
                    "chat_id": entry.chat_id,
                    "name": names.name_of(entry.chat_id),
                    "kind": entry.kind,
                    "phone": names.phone_of(entry.chat_id) if entry.kind == "person" else None,
                    "saved": entry.saved if entry.kind == "person" else None,
                }
            )
        )
    result: dict = {"query": query, "chats": chats, "total": len(hits)}
    if names.incomplete:
        result["note"] = (
            f"Evolution did not return the {', '.join(names.incomplete)} list, so names there were not checked."
        )
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
        chat_jid = calls.parse_chat(value)
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
    chat_jid, digits = await calls.resolve_person(client, conn, chat, purpose="read")
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
    chat_jid, digits = await calls.resolve_person(client, conn, chat, purpose="read")
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
    chat_jid, digits = await calls.resolve_person(client, conn, chat, purpose="write")
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
    chat_jid, digits = await calls.resolve_person(client, conn, chat, purpose="write")
    await calls.call(
        client,
        conn.identity,
        "POST",
        "chat/updateBlockStatus",
        json={"number": digits, "status": "unblock"},
        write=True,
    )
    return tool_result({"chat_id": chat_jid, "blocked": False})
