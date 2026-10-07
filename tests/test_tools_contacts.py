import json

import pytest

from evolution_api_mcp.errors import ToolExecutionError
from evolution_api_mcp.tools.contacts import (
    block_contact,
    check_whatsapp_numbers,
    find_chats,
    get_business_profile,
    get_contact_profile,
    unblock_contact,
)
from tests.conftest import INSTANCE
from tests.fakes import program_directory

FIND_CONTACTS = f"/chat/findContacts/{INSTANCE}"
FETCH_GROUPS = f"/group/fetchAllGroups/{INSTANCE}"
FIND_MESSAGES = f"/chat/findMessages/{INSTANCE}"
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


# --- find_chats ------------------------------------------------------------------------------------------------

TEAM = "120363012345678901@g.us"


def _directory(evo, *, contacts=(), groups=()):
    program_directory(evo, INSTANCE, contacts=contacts, groups=groups)


def _ranked_contacts():
    return [
        _contact("393330000001@s.whatsapp.net", "Ana"),
        _contact("393330000002@s.whatsapp.net", "Anastasia Verdi"),
        _contact("393330000003@s.whatsapp.net", "Maria Ana Neri"),
        _contact("393330000004@s.whatsapp.net", "Banana Split"),
        _contact("393330000005@s.whatsapp.net", "Bob Bianchi"),
    ]


@pytest.mark.anyio
async def test_find_chats_ranks_exact_then_prefix_then_word_then_substring_and_projects_each_chat(evo, bound):
    _directory(evo, contacts=_ranked_contacts(), groups=[{"id": TEAM, "subject": "Team Ana"}])
    with bound(evo):
        result = json.loads(await find_chats(query="ana"))

    assert [(r.method, r.path) for r in evo.requests] == [
        ("POST", FIND_CONTACTS),
        ("GET", FETCH_GROUPS),
        ("POST", FIND_MESSAGES),
        ("GET", "/instance/fetchInstances"),
    ]
    assert result == {
        "query": "ana",
        "chats": [
            {
                "chat_id": "393330000001@s.whatsapp.net",
                "name": "Ana",
                "kind": "person",
                "phone": "393330000001",
                "saved": True,
            },
            {
                "chat_id": "393330000002@s.whatsapp.net",
                "name": "Anastasia Verdi",
                "kind": "person",
                "phone": "393330000002",
                "saved": True,
            },
            {
                "chat_id": "393330000003@s.whatsapp.net",
                "name": "Maria Ana Neri",
                "kind": "person",
                "phone": "393330000003",
                "saved": True,
            },
            {"chat_id": TEAM, "name": "Team Ana", "kind": "group"},
            {
                "chat_id": "393330000004@s.whatsapp.net",
                "name": "Banana Split",
                "kind": "person",
                "phone": "393330000004",
                "saved": True,
            },
        ],
        "total": 5,
    }
    assert "internal-instance-id" not in json.dumps(result)


@pytest.mark.anyio
async def test_find_chats_kind_limits_the_search_to_people_or_groups_and_limit_cuts_the_list(evo, bound):
    _directory(evo, contacts=_ranked_contacts(), groups=[{"id": TEAM, "subject": "Team Ana"}])
    with bound(evo):
        people = json.loads(await find_chats(query="ana", kind="people", limit=2))
        groups = json.loads(await find_chats(query="ana", kind="groups"))

    assert [c["name"] for c in people["chats"]] == ["Ana", "Anastasia Verdi"]
    assert people["total"] == 4
    assert [c["chat_id"] for c in groups["chats"]] == [TEAM]
    assert groups["total"] == 1


@pytest.mark.anyio
async def test_find_chats_matches_names_without_regard_to_case_and_accents(evo, bound):
    _directory(evo, contacts=[_contact(ANA, "Mário Rossi")])
    with bound(evo):
        result = json.loads(await find_chats(query="MARIO ros"))

    assert [c["chat_id"] for c in result["chats"]] == [ANA]


@pytest.mark.anyio
async def test_find_chats_with_three_digits_matches_phone_numbers_not_names(evo, bound):
    _directory(evo, contacts=[_contact(ANA, "Ana Rossi"), _contact(BOB, "Room 333 Bob")])
    with bound(evo):
        by_digits = json.loads(await find_chats(query="+39 333 12"))
        by_name = json.loads(await find_chats(query="333 bob"))

    assert [c["chat_id"] for c in by_digits["chats"]] == [ANA]
    assert [c["chat_id"] for c in by_name["chats"]] == [BOB]


@pytest.mark.anyio
async def test_find_chats_names_a_number_only_contact_by_its_phone_and_marks_it_unsaved(evo, bound):
    _directory(evo, contacts=[_contact(BOB, None, saved=False)])
    with bound(evo):
        result = json.loads(await find_chats(query="393339876"))

    assert result["chats"] == [{"chat_id": BOB, "kind": "person", "phone": "393339876543", "saved": False}]


@pytest.mark.anyio
async def test_find_chats_skips_status_and_broadcast_chats(evo, bound):
    _directory(evo, contacts=[_contact("status@broadcast", "Status Ana"), _contact(ANA, "Ana")])
    with bound(evo):
        result = json.loads(await find_chats(query="ana"))

    assert [c["chat_id"] for c in result["chats"]] == [ANA]
    assert result["total"] == 1


@pytest.mark.anyio
async def test_find_chats_finds_nothing_on_an_empty_instance(evo, bound):
    _directory(evo)
    with bound(evo):
        result = json.loads(await find_chats(query="ana"))

    assert result == {"query": "ana", "chats": [], "total": 0}


@pytest.mark.anyio
async def test_find_chats_says_which_list_evolution_did_not_return(evo, bound):
    _directory(evo, contacts=[_contact(ANA, "Ana")])
    evo.on("GET", FETCH_GROUPS, status=400, json={"message": "no groups"})
    with bound(evo):
        result = json.loads(await find_chats(query="ana"))

    assert [c["chat_id"] for c in result["chats"]] == [ANA]
    assert result["note"] == "Evolution did not return the groups list, so names there were not checked."


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
