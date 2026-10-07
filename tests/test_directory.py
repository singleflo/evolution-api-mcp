from types import SimpleNamespace

import pytest

from evolution_api_mcp import directory, registry
from evolution_api_mcp.client import EvolutionClient
from evolution_api_mcp.directory import Entry, normalize
from tests.conftest import BASE, INSTANCE, TOKEN
from tests.fakes import program_directory

MARIO = "393331234567@s.whatsapp.net"
BOB = "393339876543@s.whatsapp.net"
FAMILY = "120363000000000001@g.us"
ANA_LID = "98765432100@lid"
ANA_PHONE = "393335550000@s.whatsapp.net"
OWNER = "393930000000@s.whatsapp.net"

CONTACTS_PATH = f"/chat/findContacts/{INSTANCE}"
GROUPS_PATH = f"/group/fetchAllGroups/{INSTANCE}"
MESSAGES_PATH = f"/chat/findMessages/{INSTANCE}"
INSTANCES_PATH = "/instance/fetchInstances"


def _client(evo) -> EvolutionClient:
    return EvolutionClient(BASE, TOKEN, transport=evo)


def _program(evo, *, contacts=(), groups=(), rows=(), instance=None) -> None:
    program_directory(evo, INSTANCE, contacts=contacts, groups=groups, rows=rows, instance_row=instance)


def test_normalize_drops_accents_case_and_punctuation():
    assert normalize("Mário  Rossi!") == "mario rossi"
    assert normalize("  ÅNGSTRÖM-Co. ") == "angstrom co"
    assert normalize("***") == ""


@pytest.mark.anyio
async def test_build_sends_the_four_requests_exactly(evo, make_connection):
    _program(evo)

    await directory.get(_client(evo), make_connection())

    sent = [(r.method, r.path, r.params, r.json) for r in evo.requests]
    assert sent == [
        ("POST", CONTACTS_PATH, {}, {"where": {}}),
        ("GET", GROUPS_PATH, {"getParticipants": "false"}, None),
        ("POST", MESSAGES_PATH, {}, {"where": {}, "offset": 100, "page": 1}),
        ("GET", INSTANCES_PATH, {"instanceName": INSTANCE}, None),
    ]


@pytest.mark.anyio
async def test_business_instances_skip_the_groups_call(evo, make_connection, make_identity):
    _program(evo)

    await directory.get(_client(evo), make_connection(identity=make_identity(registry.BUSINESS)))

    assert [r.path for r in evo.requests] == [CONTACTS_PATH, MESSAGES_PATH, INSTANCES_PATH]


@pytest.mark.anyio
async def test_contacts_and_groups_become_entries(evo, make_connection):
    _program(
        evo,
        contacts=[
            {"remoteJid": MARIO, "pushName": "Mario Rossi", "isSaved": True},
            {"remoteJid": BOB, "pushName": "393339876543", "isSaved": False},  # Evolution's fallback is the digits
            {"remoteJid": FAMILY, "pushName": "old subject", "isSaved": False},
            {"remoteJid": ANA_LID, "pushName": "Ana", "isSaved": False},
        ],
        groups=[{"id": "120363000000000001", "subject": "Family"}],
    )

    names = await directory.get(_client(evo), make_connection())

    assert names.entries[MARIO] == Entry(MARIO, "Mario Rossi", "person", "393331234567", True)
    assert names.entries[BOB] == Entry(BOB, None, "person", "393339876543", False)
    assert names.entries[FAMILY] == Entry(FAMILY, "Family", "group", None, True)
    assert names.entries[ANA_LID] == Entry(ANA_LID, "Ana", "person", None, False)
    assert names.name_of(MARIO) == "Mario Rossi"
    assert names.name_of(BOB) is None
    assert names.phone_of(MARIO) == "393331234567"
    assert names.incomplete == ()


@pytest.mark.anyio
async def test_a_pushname_equal_to_the_digits_is_not_learned(evo, make_connection):
    row = {"key": {"id": "A1", "remoteJid": BOB, "fromMe": False}, "pushName": "393339876543"}
    _program(evo, contacts=[{"remoteJid": BOB, "pushName": "393339876543"}], rows=[row])

    names = await directory.get(_client(evo), make_connection())

    assert names.name_of(BOB) is None


@pytest.mark.anyio
async def test_learn_links_lids_to_phones_from_remote_jid_alt(evo, make_connection):
    row = {
        "key": {"id": "A1", "remoteJid": ANA_LID, "remoteJidAlt": ANA_PHONE, "fromMe": False},
        "pushName": "Ana",
    }
    _program(evo, contacts=[{"remoteJid": ANA_LID, "pushName": None}], rows=[row])

    names = await directory.get(_client(evo), make_connection())

    assert names.lid_phone == {ANA_LID: "393335550000"}
    assert names.phone_of(ANA_LID) == "393335550000"
    assert names.entries[ANA_LID].phone == "393335550000"
    assert names.entries[ANA_LID].name == "Ana"


@pytest.mark.anyio
async def test_learn_links_lids_to_phones_from_participant_alt(evo, make_connection):
    row = {
        "key": {
            "id": "G1",
            "remoteJid": FAMILY,
            "participant": ANA_LID,
            "participantAlt": ANA_PHONE,
            "fromMe": False,
        },
        "pushName": "Ana",
    }
    _program(evo, rows=[row])

    names = await directory.get(_client(evo), make_connection())

    assert names.lid_phone == {ANA_LID: "393335550000"}
    assert names.name_of(ANA_LID) == "Ana"
    assert FAMILY not in names.entries or names.entries[FAMILY].name is None


@pytest.mark.anyio
async def test_a_message_names_only_an_unnamed_sender_and_skips_the_own_placeholder(evo, make_connection):
    rows = [
        {"key": {"id": "A1", "remoteJid": MARIO, "fromMe": False}, "pushName": "Mario Messaggi"},
        {"key": {"id": "A2", "remoteJid": BOB, "fromMe": False}, "pushName": "Você"},
    ]
    _program(evo, contacts=[{"remoteJid": MARIO, "pushName": "Mario Rossi"}], rows=rows)

    names = await directory.get(_client(evo), make_connection())

    assert names.name_of(MARIO) == "Mario Rossi"
    assert names.name_of(BOB) is None


@pytest.mark.anyio
async def test_me_comes_from_the_owner_jid_and_own_lid_messages(evo, make_connection):
    own_row = {
        "key": {"id": "O1", "remoteJid": FAMILY, "participant": "55555:12@lid", "fromMe": True},
        "pushName": "Você",
    }
    _program(
        evo,
        rows=[own_row],
        instance={"name": INSTANCE, "ownerJid": "393930000000:7@s.whatsapp.net", "profileName": "Giulia"},
    )

    names = await directory.get(_client(evo), make_connection())

    assert names.me == frozenset({OWNER, "55555@lid"})
    assert names.me_name == "Giulia"


@pytest.mark.anyio
async def test_a_failing_source_is_listed_as_incomplete_and_the_build_goes_on(evo, make_connection):
    _program(evo, contacts=[{"remoteJid": MARIO, "pushName": "Mario Rossi"}])
    evo.on("GET", GROUPS_PATH, status=400, json={"message": "no groups"})

    names = await directory.get(_client(evo), make_connection())

    assert names.incomplete == ("groups",)
    assert names.name_of(MARIO) == "Mario Rossi"
    assert names.me == frozenset({OWNER})


@pytest.mark.anyio
async def test_ttl_and_rebuild_throttle(evo, make_connection, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(directory, "time", SimpleNamespace(monotonic=lambda: now[0]))
    _program(evo, contacts=[{"remoteJid": MARIO, "pushName": "Mario Rossi"}])
    client, conn = _client(evo), make_connection()

    first = await directory.get(client, conn)
    assert len(evo.requests) == 4

    now[0] += 10
    assert await directory.get(client, conn) is first
    assert await directory.get(client, conn, rebuild=True) is first  # younger than 60 s: no rebuild
    assert len(evo.requests) == 4

    now[0] += directory.REBUILD_AFTER_SECONDS
    rebuilt = await directory.get(client, conn, rebuild=True)
    assert rebuilt is not first
    assert len(evo.requests) == 8

    now[0] += directory.TTL_SECONDS - 1
    assert await directory.get(client, conn) is rebuilt  # inside the TTL

    now[0] += 2
    expired = await directory.get(client, conn)
    assert expired is not rebuilt
    assert len(evo.requests) == 12


@pytest.mark.anyio
async def test_each_subject_has_its_own_directory_and_forget_drops_one(evo, make_connection):
    _program(evo, contacts=[{"remoteJid": MARIO, "pushName": "Mario Rossi"}])
    client = _client(evo)
    alice = make_connection(mode="hosted", subject="alice")
    bob = make_connection(mode="hosted", subject="bob")

    first_alice = await directory.get(client, alice)
    first_bob = await directory.get(client, bob)
    assert first_alice is not first_bob
    assert await directory.get(client, alice) is first_alice

    directory.forget("alice")
    assert await directory.get(client, bob) is first_bob
    assert await directory.get(client, alice) is not first_alice


@pytest.mark.anyio
async def test_tenant_forget_drops_the_subjects_directory(evo, make_connection):
    from evolution_api_mcp import tenant

    _program(evo)
    conn = make_connection(mode="hosted", subject="carol")
    first = await directory.get(_client(evo), conn)

    tenant.forget("carol")

    assert await directory.get(_client(evo), conn) is not first


def _names() -> directory.Directory:
    names = directory.Directory()
    for chat_id, name, kind in [
        (MARIO, "Mario Rossi", "person"),
        (BOB, "Roberto Bianchi", "person"),
        ("393330001111@s.whatsapp.net", "Maria Verdi", "person"),
        (FAMILY, "Famiglia Rossi", "group"),
        ("120363000000000002@g.us", "Mario's fans", "group"),
    ]:
        names.entries[chat_id] = Entry(chat_id, name, kind, chat_id.split("@")[0] if kind == "person" else None, True)
    return names


def test_exact_and_partial_ignore_case_and_accents():
    names = _names()

    assert [e.chat_id for e in names.exact("MARIO ROSSI")] == [MARIO]
    assert [e.chat_id for e in names.exact("mário rossi", "person")] == [MARIO]
    assert names.exact("mario") == []
    assert [e.name for e in names.partial("rossi")] == ["Famiglia Rossi", "Mario Rossi"]
    assert [e.name for e in names.partial("rossi", "group")] == ["Famiglia Rossi"]


def test_search_ranks_exact_prefix_word_prefix_then_substring():
    names = _names()

    assert [e.name for e in names.search("mari")] == ["Maria Verdi", "Mario Rossi", "Mario's fans"]
    assert [e.name for e in names.search("ross")] == ["Famiglia Rossi", "Mario Rossi"]
    assert [e.name for e in names.search("oss")] == ["Famiglia Rossi", "Mario Rossi"]
    assert [e.name for e in names.search("maria verdi")] == ["Maria Verdi"]


def test_search_matches_phone_numbers_of_three_or_more_digits():
    names = _names()

    assert [e.chat_id for e in names.search("+39 333 123")] == [MARIO]
    assert [e.chat_id for e in names.search("393339876543")] == [BOB]
    assert names.search("39") == []  # fewer than 3 digits is a name query, and no name contains it


def test_an_lid_and_its_phone_entry_count_as_one_person():
    names = directory.Directory()
    names.entries[MARIO] = Entry(MARIO, None, "person", "393331234567", True)
    names.entries[ANA_LID] = Entry(ANA_LID, "Ana", "person", None, False)
    names.entries[ANA_PHONE] = Entry(ANA_PHONE, "Ana", "person", "393335550000", True)
    names.learn([{"key": {"id": "x", "remoteJid": ANA_LID, "remoteJidAlt": ANA_PHONE, "fromMe": False}}])

    assert [e.chat_id for e in names.exact("ana")] == [ANA_PHONE]
    assert names.name_of(ANA_LID) == "Ana"


def test_identities_pair_a_phone_jid_with_its_lid_once_the_link_is_known():
    names = directory.Directory()
    assert names.identities(ANA_PHONE) == (ANA_PHONE,)

    names.learn([{"key": {"id": "x", "remoteJid": ANA_LID, "remoteJidAlt": ANA_PHONE, "fromMe": False}}])

    assert names.identities(ANA_PHONE) == (ANA_PHONE, ANA_LID)
    assert names.identities(ANA_LID) == (ANA_LID, ANA_PHONE)
