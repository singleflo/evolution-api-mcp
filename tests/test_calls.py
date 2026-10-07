from types import SimpleNamespace

import pytest

from evolution_api_mcp import calls, directory
from evolution_api_mcp.client import EvolutionClient, EvolutionHTTPError, EvolutionUncertain
from evolution_api_mcp.errors import ToolExecutionError
from tests.conftest import BASE, INSTANCE, TOKEN
from tests.fakes import program_directory


def _client(evo) -> EvolutionClient:
    return EvolutionClient(BASE, TOKEN, transport=evo)


@pytest.mark.anyio
async def test_call_builds_the_instance_path_and_returns_the_body(evo, make_identity):
    evo.on("GET", f"/typebot/fetch/bot%201/{INSTANCE}", json={"id": "x"})
    body = await calls.call(_client(evo), make_identity(), "GET", "typebot/fetch", "bot 1")
    assert body == {"id": "x"}


@pytest.mark.anyio
async def test_read_failure_says_nothing_was_changed(evo, make_identity):
    evo.on("GET", f"/chat/findChats/{INSTANCE}", status=400, json={"response": {"message": ["bad"]}})
    with pytest.raises(ToolExecutionError, match=r"refused the request \(400\): bad\. Nothing was changed"):
        await calls.call(_client(evo), make_identity(), "GET", "chat/findChats")


@pytest.mark.anyio
async def test_write_without_an_answer_is_uncertain_with_the_reread_state(evo, make_identity):
    evo.fail("POST", f"/group/create/{INSTANCE}", EvolutionUncertain("evo.test", "read timeout"))

    async def reread() -> object:
        return {"groups": []}

    with pytest.raises(ToolExecutionError, match=r"UNCERTAIN.*\{\"groups\": \[\]\}.*Do NOT repeat"):
        await calls.call(_client(evo), make_identity(), "POST", "group/create", json={}, write=True, reread=reread)


@pytest.mark.anyio
async def test_http_error_hook_can_replace_the_message(evo, make_identity):
    evo.on("GET", f"/typebot/find/{INSTANCE}", status=400, json={"message": "Typebot is disabled"})

    def hook(exc: EvolutionHTTPError) -> None:
        if exc.message.endswith("is disabled"):
            raise ToolExecutionError("The typebot integration is disabled on this Evolution server.")

    with pytest.raises(ToolExecutionError, match="typebot integration is disabled"):
        await calls.call(_client(evo), make_identity(), "GET", "typebot/find", on_http_error=hook)


def test_parse_phone_refuses_groups_and_accepts_formatted_numbers():
    assert calls.parse_phone("+39 333 123 4567") == "393331234567"
    with pytest.raises(ToolExecutionError, match="not a phone number"):
        calls.parse_phone("120363000000000000@g.us")
    with pytest.raises(ToolExecutionError, match="Unsupported chat id"):
        calls.parse_chat("abc")


def test_parse_group_accepts_ids_and_digits_only():
    assert calls.parse_group("120363000000000000") == "120363000000000000@g.us"
    with pytest.raises(ToolExecutionError, match="Unsupported group id"):
        calls.parse_group("Family")


# --- names -----------------------------------------------------------------------------------------------------

MARIO = "393331234567@s.whatsapp.net"
FAMILY = "120363000000000001@g.us"
ANA_LID = "98765432100@lid"
ANA_PHONE = "393335550000@s.whatsapp.net"


def _program(evo, *, contacts=(), groups=(), rows=()) -> None:
    program_directory(evo, INSTANCE, contacts=contacts, groups=groups, rows=rows)


def _contact(chat_id: str, name: str) -> dict:
    return {"remoteJid": chat_id, "pushName": name, "isSaved": True}


@pytest.mark.anyio
async def test_numbers_and_ids_are_parsed_without_calling_evolution(evo, make_connection):
    conn = make_connection()
    assert await calls.resolve_chat(_client(evo), conn, "+39 333 123 4567", purpose="send") == MARIO
    assert await calls.resolve_chat(_client(evo), conn, FAMILY, purpose="read") == FAMILY
    assert await calls.resolve_group(_client(evo), conn, "120363000000000001", purpose="write") == FAMILY
    assert await calls.resolve_group(_client(evo), conn, FAMILY, purpose="write") == FAMILY
    assert evo.requests == []


@pytest.mark.anyio
async def test_an_id_that_is_not_a_chat_keeps_the_literal_error(evo, make_connection):
    with pytest.raises(ToolExecutionError, match="Unsupported chat id 'abc@foo.com'"):
        await calls.resolve_chat(_client(evo), make_connection(), "abc@foo.com", purpose="send")
    assert evo.requests == []


@pytest.mark.anyio
async def test_a_unique_exact_name_resolves_whatever_the_case_and_accents(evo, make_connection):
    _program(evo, contacts=[_contact(MARIO, "Mario Rossi"), _contact(ANA_PHONE, "Ana")])
    conn = make_connection()

    assert await calls.resolve_chat(_client(evo), conn, "MÁRIO rossi", purpose="send") == MARIO
    assert await calls.resolve_chat(_client(evo), conn, "ana", purpose="write", kind="person") == ANA_PHONE


@pytest.mark.anyio
async def test_reads_accept_a_unique_partial_name(evo, make_connection):
    _program(evo, contacts=[_contact(MARIO, "Mario Rossi"), _contact(ANA_PHONE, "Ana Verdi")])

    assert await calls.resolve_chat(_client(evo), make_connection(), "rossi", purpose="read") == MARIO


@pytest.mark.anyio
@pytest.mark.parametrize(("purpose", "suffix"), [("send", " Nothing was sent."), ("write", " Nothing was changed.")])
async def test_writes_and_sends_refuse_a_partial_name_and_list_the_close_matches(evo, make_connection, purpose, suffix):
    _program(evo, contacts=[_contact(MARIO, "Mario Rossi")])

    with pytest.raises(ToolExecutionError) as caught:
        await calls.resolve_chat(_client(evo), make_connection(), "Mario", purpose=purpose)

    assert str(caught.value) == (
        "No contact or group is named exactly 'Mario'. Close matches: Mario Rossi (" + MARIO + "). "
        "Pass the chat_id of the one you mean." + suffix
    )


@pytest.mark.anyio
async def test_an_ambiguous_name_lists_ten_candidates_and_counts_the_rest(evo, make_connection):
    people = [f"3933300000{n:02d}@s.whatsapp.net" for n in range(12)]
    _program(evo, contacts=[_contact(chat_id, "Pat Smith") for chat_id in people])

    with pytest.raises(ToolExecutionError) as caught:
        await calls.resolve_chat(_client(evo), make_connection(), "pat smith", purpose="send")

    shown = "; ".join(f"Pat Smith ({chat_id})" for chat_id in people[:10])
    assert str(caught.value) == (
        f"'pat smith' matches 12 chats: {shown} and 2 more. Pass the chat_id of the one you mean. Nothing was sent."
    )


@pytest.mark.anyio
async def test_reads_refuse_an_ambiguous_partial_name_without_a_suffix(evo, make_connection):
    _program(evo, contacts=[_contact(MARIO, "Mario Rossi"), _contact(ANA_PHONE, "Mario Verdi")])

    with pytest.raises(ToolExecutionError) as caught:
        await calls.resolve_chat(_client(evo), make_connection(), "mario", purpose="read")

    assert str(caught.value).startswith("'mario' matches 2 chats: Mario Rossi (")
    assert str(caught.value).endswith("Pass the chat_id of the one you mean.")


@pytest.mark.anyio
async def test_one_rebuild_before_not_found(evo, make_connection, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(directory, "time", SimpleNamespace(monotonic=lambda: now[0]))
    _program(evo)
    client, conn = _client(evo), make_connection()
    await directory.get(client, conn)
    assert len(evo.requests) == 4

    now[0] += 100
    _program(evo, contacts=[_contact(MARIO, "Mario Rossi")])
    assert await calls.resolve_chat(client, conn, "Mario Rossi", purpose="send") == MARIO
    assert len(evo.requests) == 8  # the directory was rebuilt once

    with pytest.raises(ToolExecutionError) as caught:
        await calls.resolve_chat(client, conn, "Nobody", purpose="send")
    assert str(caught.value) == (
        "No contact or group named 'Nobody' was found. find_chats searches names and numbers. Nothing was sent."
    )
    assert len(evo.requests) == 8  # younger than the rebuild throttle: no second rebuild


@pytest.mark.anyio
async def test_not_found_names_the_sources_evolution_did_not_answer(evo, make_connection):
    _program(evo, contacts=[_contact(MARIO, "Mario Rossi")])
    evo.on("GET", f"/group/fetchAllGroups/{INSTANCE}", status=400, json={"message": "no groups"})

    with pytest.raises(ToolExecutionError) as caught:
        await calls.resolve_chat(_client(evo), make_connection(), "Family", purpose="write")

    assert str(caught.value) == (
        "No contact or group named 'Family' was found. find_chats searches names and numbers. "
        "Evolution did not return the groups list, so names there were not checked. Nothing was changed."
    )


@pytest.mark.anyio
async def test_resolve_group_matches_group_names_only(evo, make_connection):
    _program(
        evo,
        contacts=[_contact(MARIO, "Family")],
        groups=[{"id": FAMILY, "subject": "Family"}],
    )

    assert await calls.resolve_group(_client(evo), make_connection(), "family", purpose="write") == FAMILY


@pytest.mark.anyio
async def test_resolve_person_returns_the_jid_and_the_digits(evo, make_connection):
    _program(evo, contacts=[_contact(MARIO, "Mario Rossi"), _contact(FAMILY, "Mario Rossi")])
    conn = make_connection()

    assert await calls.resolve_person(_client(evo), conn, "Mario Rossi", purpose="send") == (MARIO, "393331234567")
    assert await calls.resolve_person(_client(evo), conn, "+39 333 123 4567", purpose="send") == (
        MARIO,
        "393331234567",
    )


@pytest.mark.anyio
async def test_resolve_person_refuses_a_group_id(evo, make_connection):
    with pytest.raises(ToolExecutionError) as caught:
        await calls.resolve_person(_client(evo), make_connection(), FAMILY, purpose="write")

    assert str(caught.value) == (
        f"{FAMILY} is not a phone number; this tool takes international phone numbers. Nothing was changed."
    )
    assert evo.requests == []


@pytest.mark.anyio
async def test_resolve_person_turns_a_known_lid_into_its_phone(evo, make_connection):
    row = {"key": {"id": "A1", "remoteJid": ANA_LID, "remoteJidAlt": ANA_PHONE, "fromMe": False}}
    _program(evo, rows=[row])

    result = await calls.resolve_person(_client(evo), make_connection(), ANA_LID, purpose="send")

    assert result == (ANA_PHONE, "393335550000")


@pytest.mark.anyio
async def test_resolve_person_refuses_an_lid_without_a_known_phone(evo, make_connection):
    _program(evo, contacts=[_contact(ANA_LID, "Ana")])

    with pytest.raises(ToolExecutionError) as caught:
        await calls.resolve_person(_client(evo), make_connection(), "Ana", purpose="send")

    assert str(caught.value) == (
        f"Ana has no known phone number: WhatsApp shows this person only as {ANA_LID}. Nothing was sent."
    )


# --- stored_message --------------------------------------------------------------------------------------------

STORED_FIND = f"/chat/findMessages/{INSTANCE}"
ID_HINT = "read_messages, search_messages and list_recent_messages show message ids."


def _stored_row(remote: str = "393331234567@s.whatsapp.net") -> dict:
    return {"key": {"id": "3EB0AAAA01", "remoteJid": remote, "fromMe": False}, "messageType": "conversation"}


def _stored_page(*records: dict, total: int | None = None) -> dict:
    count = len(records) if total is None else total
    return {"messages": {"total": count, "pages": 1, "currentPage": 1, "records": list(records)}}


@pytest.mark.anyio
async def test_stored_message_returns_the_row(evo, make_connection):
    row = _stored_row()
    evo.on("POST", STORED_FIND, json=_stored_page(row))
    found = await calls.stored_message(_client(evo), make_connection(), "3EB0AAAA01", None, purpose="read")
    assert found == row


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("purpose", "suffix"), [("read", ""), ("write", " Nothing was changed."), ("send", " Nothing was sent.")]
)
async def test_stored_message_not_found_names_the_scope_and_takes_the_purpose_suffix(
    evo, make_connection, purpose, suffix
):
    evo.on("POST", STORED_FIND, json=_stored_page())
    conn = make_connection()
    with pytest.raises(ToolExecutionError) as anywhere:
        await calls.stored_message(_client(evo), conn, "3EB0MISSING", None, purpose=purpose)
    with pytest.raises(ToolExecutionError) as in_chat:
        await calls.stored_message(_client(evo), conn, "3EB0MISSING", "393331234567@s.whatsapp.net", purpose=purpose)
    assert str(anywhere.value) == f"Message 3EB0MISSING was not found. {ID_HINT}{suffix}"
    assert str(in_chat.value) == f"Message 3EB0MISSING was not found in this chat. {ID_HINT}{suffix}"


@pytest.mark.anyio
async def test_stored_message_puts_the_note_before_the_purpose_suffix(evo, make_connection):
    evo.on("POST", STORED_FIND, json=_stored_page())
    with pytest.raises(ToolExecutionError) as caught:
        await calls.stored_message(
            _client(evo), make_connection(), "3EB0MISSING", None, purpose="write", not_found_note=" A note."
        )
    assert str(caught.value) == f"Message 3EB0MISSING was not found. {ID_HINT} A note. Nothing was changed."


@pytest.mark.anyio
async def test_stored_message_names_the_chats_of_an_ambiguous_id(evo, make_connection):
    group = "120363012345678901@g.us"
    evo.on("POST", STORED_FIND, json=_stored_page(_stored_row(), _stored_row(group)))
    with pytest.raises(ToolExecutionError) as caught:
        await calls.stored_message(_client(evo), make_connection(), "3EB0AAAA01", None, purpose="send")
    assert str(caught.value) == (
        "Message 3EB0AAAA01 exists in 2 chats (393331234567@s.whatsapp.net, 120363012345678901@g.us). "
        "Pass chat to pick one. Nothing was sent."
    )


@pytest.mark.anyio
async def test_find_stored_returns_none_when_there_is_no_row_and_reports_evolution_failures(evo, make_connection):
    conn = make_connection()
    evo.on("POST", STORED_FIND, json=_stored_page())
    assert await calls.find_stored(_client(evo), conn, "3EB0MISSING", None, purpose="read") is None
    evo.on("POST", STORED_FIND, status=500, json={"message": "boom"})
    with pytest.raises(ToolExecutionError, match=r"Nothing was changed"):
        await calls.find_stored(_client(evo), conn, "3EB0MISSING", None, purpose="write")
