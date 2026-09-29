import json

import pytest

from evolution_api_mcp.errors import ToolExecutionError
from evolution_api_mcp.tools.contacts import (
    block_contact,
    check_whatsapp_numbers,
    find_contacts,
    get_business_profile,
    get_contact_profile,
    unblock_contact,
)
from tests.conftest import INSTANCE

FIND_CONTACTS = f"/chat/findContacts/{INSTANCE}"
WHATSAPP_NUMBERS = f"/chat/whatsappNumbers/{INSTANCE}"
FETCH_PROFILE = f"/chat/fetchProfile/{INSTANCE}"
FETCH_BUSINESS = f"/chat/fetchBusinessProfile/{INSTANCE}"
UPDATE_BLOCK = f"/chat/updateBlockStatus/{INSTANCE}"

ANA = "393331234567@s.whatsapp.net"
BOB = "393339876543@s.whatsapp.net"
GROUP = "120363012345678901@g.us"


def _contact(remote, push_name=None, *, saved=True):
    is_group = remote.endswith("@g.us")
    return {
        "id": f"row-{remote}",
        "remoteJid": remote,
        "pushName": push_name,
        "profilePicUrl": None,
        "instanceId": "internal-instance-id",
        "createdAt": "2026-01-01T00:00:00.000Z",
        "updatedAt": "2026-01-02T00:00:00.000Z",
        "isGroup": is_group,
        "isSaved": saved and bool(push_name),
        "type": "group" if is_group else "contact",
    }


def _people(count, *, prefix="Filler", start=0):
    return [_contact(f"39{3000000000 + start + n}@s.whatsapp.net", f"{prefix} {start + n}") for n in range(count)]


def _paged(pages):
    """Answer findContacts by the requested page number, like Evolution's offset/page pagination."""

    def handler(request):
        assert request.json["offset"] == 500
        page = request.json["page"]
        return 200, pages[page - 1] if page <= len(pages) else []

    return handler


# --- find_contacts ---------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_find_contacts_by_phone_looks_up_that_jid_and_projects_the_row(evo, bound):
    evo.on("POST", FIND_CONTACTS, json=[_contact(ANA, "Ana Rossi")])
    with bound(evo):
        result = json.loads(await find_contacts(phone="+39 333 123 4567"))

    assert evo.last("POST", FIND_CONTACTS).json == {"where": {"remoteJid": ANA}}
    assert result == {
        "contacts": [{"chat_id": ANA, "name": "Ana Rossi", "phone": "393331234567", "is_group": False, "saved": True}],
        "offset": 0,
        "limit": 20,
        "has_more": False,
        "scanned": 1,
    }
    assert "internal-instance-id" not in json.dumps(result)


@pytest.mark.anyio
async def test_find_contacts_by_phone_accepts_a_group_id_only_with_include_groups(evo, bound):
    evo.on("POST", FIND_CONTACTS, json=[_contact(GROUP, "Family")])
    with bound(evo):
        hidden = json.loads(await find_contacts(phone=GROUP))
        shown = json.loads(await find_contacts(phone=GROUP, include_groups=True))

    assert hidden["contacts"] == []
    assert shown["contacts"] == [{"chat_id": GROUP, "name": "Family", "is_group": True, "saved": True}]


@pytest.mark.anyio
async def test_find_contacts_scans_pages_and_filters_names_without_regard_to_case(evo, bound):
    page1 = _people(498) + [_contact(GROUP, "Anastasia Family")]
    page1.append(_contact(BOB, "Bob Bianchi"))
    page2 = [_contact(ANA, "ana rossi"), _contact("393330000001@s.whatsapp.net", None, saved=False)]
    evo.on("POST", FIND_CONTACTS, handler=_paged([page1, page2]))
    with bound(evo):
        result = json.loads(await find_contacts(name="ANA"))

    assert [r.json for r in evo.requests] == [
        {"where": {}, "offset": 500, "page": 1},
        {"where": {}, "offset": 500, "page": 2},
    ]
    assert result["contacts"] == [
        {"chat_id": ANA, "name": "ana rossi", "phone": "393331234567", "is_group": False, "saved": True}
    ]
    assert result["scanned"] == 502
    assert result["has_more"] is False
    assert "note" not in result


@pytest.mark.anyio
async def test_find_contacts_includes_groups_when_asked(evo, bound):
    evo.on("POST", FIND_CONTACTS, handler=_paged([[_contact(GROUP, "Team Ana"), _contact(ANA, "Ana")]]))
    with bound(evo):
        people = json.loads(await find_contacts(name="ana"))
        both = json.loads(await find_contacts(name="ana", include_groups=True))

    assert [c["chat_id"] for c in people["contacts"]] == [ANA]
    assert [c["chat_id"] for c in both["contacts"]] == [GROUP, ANA]


@pytest.mark.anyio
async def test_find_contacts_without_filters_lists_unsaved_people_too(evo, bound):
    unsaved = _contact(BOB, None, saved=False)
    evo.on("POST", FIND_CONTACTS, handler=_paged([[_contact(ANA, "Ana"), unsaved]]))
    with bound(evo):
        result = json.loads(await find_contacts())

    assert result["contacts"] == [
        {"chat_id": ANA, "name": "Ana", "phone": "393331234567", "is_group": False, "saved": True},
        {"chat_id": BOB, "phone": "393339876543", "is_group": False, "saved": False},
    ]


@pytest.mark.anyio
async def test_find_contacts_pages_with_offset_and_limit_and_stops_scanning_early(evo, bound):
    page1 = _people(500, prefix="Ana")
    evo.on("POST", FIND_CONTACTS, handler=_paged([page1, _people(500, prefix="Ana", start=500)]))
    with bound(evo):
        result = json.loads(await find_contacts(name="ana", limit=3, offset=2))

    assert len(evo.requests) == 1  # offset + limit + 1 matches were found on the first page
    assert [c["name"] for c in result["contacts"]] == ["Ana 2", "Ana 3", "Ana 4"]
    assert (result["offset"], result["limit"], result["has_more"]) == (2, 3, True)
    assert result["scanned"] == 500


@pytest.mark.anyio
async def test_find_contacts_reports_when_the_scan_limit_cut_the_search_short(evo, bound):
    evo.on("POST", FIND_CONTACTS, handler=lambda request: (200, _people(500, start=500 * request.json["page"])))
    with bound(evo):
        result = json.loads(await find_contacts(name="nobody"))

    assert len(evo.requests) == 10
    assert result["contacts"] == []
    assert result["scanned"] == 5000
    assert result["note"] == "Only the first 5000 stored contacts were scanned; narrow with name or phone."


@pytest.mark.anyio
async def test_find_contacts_empty_store(evo, bound):
    evo.on("POST", FIND_CONTACTS, json=[])
    with bound(evo):
        result = json.loads(await find_contacts(name="ana"))

    assert result == {"contacts": [], "offset": 0, "limit": 20, "has_more": False, "scanned": 0}


@pytest.mark.anyio
async def test_find_contacts_rejects_a_bad_phone(evo, bound):
    with bound(evo), pytest.raises(ToolExecutionError, match="Unsupported chat id"):
        await find_contacts(phone="x@foo.com")


# --- check_whatsapp_numbers ------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_check_numbers_sends_normalized_unique_digits_and_matches_answers_on_the_echoed_number(evo, bound):
    # Evolution answers in its own order; the echoed `number` is what ties an answer to an input.
    evo.on(
        "POST",
        WHATSAPP_NUMBERS,
        json=[
            {"exists": False, "jid": BOB, "number": "393339876543"},
            {"exists": True, "jid": ANA, "number": "393331234567", "name": "Ana Rossi"},
        ],
    )
    with bound(evo):
        result = json.loads(await check_whatsapp_numbers(["+39 333 123 4567", "393339876543", "393331234567"]))

    assert evo.last("POST", WHATSAPP_NUMBERS).json == {"numbers": ["393331234567", "393339876543"]}
    assert result == {
        "results": [
            {"input": "+39 333 123 4567", "on_whatsapp": True, "chat_id": ANA},
            {"input": "393339876543", "on_whatsapp": False, "chat_id": BOB},
            {"input": "393331234567", "on_whatsapp": True, "chat_id": ANA},
        ]
    }


@pytest.mark.anyio
async def test_check_numbers_keeps_a_lid_id_and_drops_the_literal_lid_marker(evo, bound):
    evo.on(
        "POST",
        WHATSAPP_NUMBERS,
        json=[
            {"exists": True, "jid": ANA, "number": "393331234567", "lid": "98765432100@lid"},
            {"exists": True, "jid": BOB, "number": "393339876543", "lid": "lid"},
        ],
    )
    with bound(evo):
        result = json.loads(await check_whatsapp_numbers(["393331234567", "393339876543"]))

    assert result["results"][0]["lid"] == "98765432100@lid"
    assert "lid" not in result["results"][1]


@pytest.mark.anyio
async def test_check_numbers_leaves_an_unanswered_input_undecided(evo, bound):
    evo.on("POST", WHATSAPP_NUMBERS, json=[{"exists": True, "jid": ANA, "number": "393331234567"}])
    with bound(evo):
        result = json.loads(await check_whatsapp_numbers(["393331234567", "393339876543"]))

    assert result["results"][1] == {"input": "393339876543", "on_whatsapp": None}


@pytest.mark.anyio
@pytest.mark.parametrize("value", [GROUP, "9876543210@lid"])
async def test_check_numbers_refuses_groups_and_lid_ids_without_calling_evolution(evo, bound, value):
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await check_whatsapp_numbers(["393331234567", value])

    expected = f"{value} is not a phone number; check_whatsapp_numbers takes international phone numbers."
    assert str(caught.value) == expected
    assert evo.requests == []


# --- get_contact_profile ---------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_contact_profile_projects_the_fetch_profile_answer(evo, bound):
    evo.on(
        "POST",
        FETCH_PROFILE,
        json={
            "wuid": ANA,
            "name": "Ana Rossi",
            "numberExists": True,
            "picture": "https://pps.whatsapp.net/ana.jpg",
            "status": "At the office",
            "isBusiness": True,
            "email": "ana@example.com",
            "description": "Bakery",
            "website": "https://ana.example.com",
        },
    )
    with bound(evo):
        result = json.loads(await get_contact_profile("+39 333 123 4567"))

    assert evo.last("POST", FETCH_PROFILE).json == {"number": "393331234567"}
    assert result == {
        "chat_id": ANA,
        "name": "Ana Rossi",
        "on_whatsapp": True,
        "about": "At the office",
        "is_business": True,
        "picture_url": "https://pps.whatsapp.net/ana.jpg",
        "email": "ana@example.com",
        "description": "Bakery",
        "website": "https://ana.example.com",
    }


@pytest.mark.anyio
async def test_get_contact_profile_omits_what_evolution_could_not_read(evo, bound):
    unreadable = {"wuid": ANA, "name": None, "picture": None, "status": None, "isBusiness": False}
    evo.on("POST", FETCH_PROFILE, json=unreadable)
    with bound(evo):
        result = json.loads(await get_contact_profile(ANA))

    assert result == {"chat_id": ANA, "is_business": False}


@pytest.mark.anyio
async def test_get_contact_profile_reports_a_number_that_is_not_on_whatsapp(evo, bound):
    evo.on(
        "POST",
        FETCH_PROFILE,
        status=400,
        json={
            "status": 400,
            "error": "Bad Request",
            "response": {"message": [{"jid": ANA, "exists": False, "number": "393331234567"}]},
        },
    )
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await get_contact_profile("393331234567")

    assert str(caught.value) == "393331234567 is not on WhatsApp."


@pytest.mark.anyio
async def test_get_contact_profile_refuses_groups(evo, bound):
    with bound(evo), pytest.raises(ToolExecutionError, match="is not a phone number"):
        await get_contact_profile(GROUP)

    assert evo.requests == []


# --- get_business_profile --------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_business_profile_returns_the_published_business_fields(evo, bound):
    evo.on(
        "POST",
        FETCH_BUSINESS,
        json={
            "isBusiness": True,
            "wid": ANA,
            "description": "Fresh bread",
            "category": "Bakery",
            "email": "shop@example.com",
            "website": ["https://shop.example.com"],
            "address": "Via Roma 1, Milano",
            "business_hours": {"timezone": "Europe/Rome", "config": {"mon": {"mode": "open_24h"}}},
            "unrelated": "dropped",
        },
    )
    with bound(evo):
        result = json.loads(await get_business_profile("393331234567"))

    assert evo.last("POST", FETCH_BUSINESS).json == {"number": "393331234567"}
    assert result == {
        "chat_id": ANA,
        "is_business": True,
        "description": "Fresh bread",
        "category": "Bakery",
        "email": "shop@example.com",
        "website": ["https://shop.example.com"],
        "address": "Via Roma 1, Milano",
        "business_hours": {"timezone": "Europe/Rome", "config": {"mon": {"mode": "open_24h"}}},
    }


@pytest.mark.anyio
async def test_get_business_profile_of_a_personal_account(evo, bound):
    evo.on(
        "POST",
        FETCH_BUSINESS,
        json={"isBusiness": False, "message": "Not is business profile", "jid": ANA, "exists": True, "number": "39"},
    )
    with bound(evo):
        result = json.loads(await get_business_profile(ANA))

    assert result == {"chat_id": ANA, "is_business": False}


# --- block_contact / unblock_contact ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_block_contact_posts_block_for_the_digits(evo, bound):
    evo.on("POST", UPDATE_BLOCK, json={"block": "success"})
    with bound(evo):
        result = json.loads(await block_contact("+39 333 123 4567"))

    assert evo.last("POST", UPDATE_BLOCK).json == {"number": "393331234567", "status": "block"}
    assert result == {"chat_id": ANA, "blocked": True}


@pytest.mark.anyio
async def test_unblock_contact_posts_unblock_for_the_digits(evo, bound):
    evo.on("POST", UPDATE_BLOCK, json={"block": "success"})
    with bound(evo):
        result = json.loads(await unblock_contact(ANA))

    assert evo.last("POST", UPDATE_BLOCK).json == {"number": "393331234567", "status": "unblock"}
    assert result == {"chat_id": ANA, "blocked": False}


@pytest.mark.anyio
async def test_block_contact_refuses_groups(evo, bound):
    with bound(evo), pytest.raises(ToolExecutionError, match="is not a phone number"):
        await block_contact(GROUP)

    assert evo.requests == []


@pytest.mark.anyio
async def test_block_contact_server_failure_is_uncertain_not_repeatable(evo, bound):
    evo.on(
        "POST",
        UPDATE_BLOCK,
        status=500,
        json={"status": 500, "error": "Internal Server Error", "response": {"message": "Error blocking user"}},
    )
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await block_contact(ANA)

    assert str(caught.value).startswith("UNCERTAIN: Evolution did not confirm the result")
    assert "Do NOT repeat the call" in str(caught.value)
