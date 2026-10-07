"""The four MCP prompts and the live completion of their `chat` argument, over the wire."""

from __future__ import annotations

import pytest
from mcp import Client
from mcp_types import PromptReference

from evolution_api_mcp.server import build_server
from tests.fakes import program_directory

INSTANCE = "inst"
PROMPTS = ("inbox", "reply", "find_attachment", "export")


def _contact(jid: str, name: str | None) -> dict:
    return {"remoteJid": jid, "pushName": name, "isSaved": True}


def _complete(prompt: str, name: str, value: str):
    return PromptReference(name=prompt), {"name": name, "value": value}


async def _text(client: Client, name: str, arguments: dict[str, str] | None = None) -> str:
    result = await client.get_prompt(name, arguments)
    (message,) = result.messages
    assert message.role == "user"
    return message.content.text


@pytest.mark.anyio
async def test_prompts_list_shows_the_four_prompts_with_titles_and_arguments(evo, bound):
    with bound(evo):
        async with Client(build_server(mode="local")) as client:  # type: ignore[arg-type]
            listed = {prompt.name: prompt for prompt in (await client.list_prompts()).prompts}

    assert set(listed) == set(PROMPTS)
    assert {name: prompt.title for name, prompt in listed.items()} == {
        "inbox": "What's new",
        "reply": "Reply in a chat",
        "find_attachment": "Find an attachment",
        "export": "Export a chat",
    }
    required = {name: [a.name for a in prompt.arguments or [] if a.required] for name, prompt in listed.items()}
    assert required == {"inbox": [], "reply": ["chat"], "find_attachment": ["description"], "export": ["chat"]}
    assert [a.name for a in listed["export"].arguments] == ["chat", "period", "format"]
    assert all(prompt.description for prompt in listed.values())


@pytest.mark.anyio
async def test_prompts_render_with_and_without_their_optional_arguments(evo, bound):
    with bound(evo):
        async with Client(build_server(mode="local")) as client:  # type: ignore[arg-type]
            assert await _text(client, "inbox") == (
                "Show me what arrived on WhatsApp in the last 24 hours: group it by chat, "
                "summarise each chat in one line, and list the chats waiting for my reply."
            )
            assert (await _text(client, "inbox", {"since": "today"})).startswith(
                "Show me what arrived on WhatsApp today: group it by chat,"
            )
            assert await _text(client, "reply", {"chat": "Mario"}) == (
                "Show me the latest messages of Mario and draft a reply. "
                "Show me the draft and wait for my approval before sending it."
            )
            assert await _text(client, "reply", {"chat": "Mario", "instructions": "say yes"}) == (
                "Show me the latest messages of Mario and draft a reply: say yes. "
                "Show me the draft and wait for my approval before sending it."
            )
            assert await _text(client, "find_attachment", {"description": "the invoice"}) == (
                "Find the attachment that matches: the invoice. "
                "List the matching messages with date and sender, then ask me which one to download."
            )
            assert await _text(client, "find_attachment", {"description": "the invoice", "chat": "Mario"}) == (
                "Find the attachment that matches: the invoice in the chat Mario. "
                "List the matching messages with date and sender, then ask me which one to download."
            )
            assert await _text(client, "export", {"chat": "Mario"}) == (
                "Export my WhatsApp chat with Mario as Markdown with its attachments, and tell me where the export is."
            )
            assert await _text(client, "export", {"chat": "Mario", "period": "September", "format": "JSON"}) == (
                "Export my WhatsApp chat with Mario for September as JSON with its attachments, "
                "and tell me where the export is."
            )


@pytest.mark.anyio
async def test_chat_completion_lists_names_from_find_contacts_and_groups(evo, bound):
    program_directory(
        evo,
        contacts=[
            _contact("393331111111@s.whatsapp.net", "Mario Rossi"),
            _contact("393332222222@s.whatsapp.net", "Luigi Mario"),
            _contact("393333333333@s.whatsapp.net", "Anna Verdi"),
            _contact("status@broadcast", "Mario Status"),
            _contact("393334444444@s.whatsapp.net", "393334444444"),
        ],
        groups=[{"id": "120363000000000000@g.us", "subject": "Famiglia Mario"}],
    )
    with bound(evo):
        async with Client(build_server(mode="local")) as client:  # type: ignore[arg-type]
            for prompt in ("reply", "find_attachment", "export"):
                result = await client.complete(*_complete(prompt, "chat", "mário"))
                assert result.completion.values == ["Mario Rossi", "Famiglia Mario", "Luigi Mario"]
                assert result.completion.total == 3
                assert result.completion.has_more is False
            everything = await client.complete(*_complete("reply", "chat", ""))

    assert everything.completion.values == ["Anna Verdi", "Famiglia Mario", "Luigi Mario", "Mario Rossi"]
    assert [r.path for r in evo.requests if r.method == "POST"].count(f"/chat/findContacts/{INSTANCE}") == 1


@pytest.mark.anyio
async def test_chat_completion_shows_the_chat_id_of_a_duplicated_name(evo, bound):
    program_directory(
        evo,
        contacts=[
            _contact("393331111111@s.whatsapp.net", "Marco"),
            _contact("393332222222@s.whatsapp.net", "Marco"),
            _contact("393333333333@s.whatsapp.net", "Marcella"),
        ],
    )
    with bound(evo):
        async with Client(build_server(mode="local")) as client:  # type: ignore[arg-type]
            result = await client.complete(*_complete("reply", "chat", "marc"))

    assert sorted(result.completion.values) == [
        "Marcella",
        "Marco (393331111111@s.whatsapp.net)",
        "Marco (393332222222@s.whatsapp.net)",
    ]


@pytest.mark.anyio
async def test_completion_is_limited_to_the_chat_argument_of_a_prompt(evo, bound):
    program_directory(evo, contacts=[_contact("393331111111@s.whatsapp.net", "Mario Rossi")])
    with bound(evo):
        async with Client(build_server(mode="local")) as client:  # type: ignore[arg-type]
            other = await client.complete(*_complete("find_attachment", "description", "mar"))

    assert other.completion.values == []
    assert evo.requests == []


@pytest.mark.anyio
async def test_completion_caps_at_one_hundred_values_and_reports_the_total(evo, bound):
    program_directory(
        evo,
        contacts=[_contact(f"39333{n:07d}@s.whatsapp.net", f"Contact {n:03d}") for n in range(120)],
    )
    with bound(evo):
        async with Client(build_server(mode="local")) as client:  # type: ignore[arg-type]
            result = await client.complete(*_complete("reply", "chat", "contact"))

    assert len(result.completion.values) == 100
    assert result.completion.total == 120
    assert result.completion.has_more is True


@pytest.mark.anyio
async def test_completion_swallows_errors_and_offers_nothing(evo, bound):
    def explode(_request):
        raise RuntimeError("boom")

    evo.on("POST", f"/chat/findContacts/{INSTANCE}", handler=explode)
    with bound(evo):
        async with Client(build_server(mode="local")) as client:  # type: ignore[arg-type]
            result = await client.complete(*_complete("reply", "chat", "mar"))

    assert result.completion.values == []


@pytest.mark.anyio
async def test_completion_with_evolution_down_offers_nothing(evo, bound):
    program_directory(evo)
    evo.on("POST", f"/chat/findContacts/{INSTANCE}", status=500, json={"message": "down"})
    evo.on("GET", f"/group/fetchAllGroups/{INSTANCE}", status=500, json={"message": "down"})
    evo.on("POST", f"/chat/findMessages/{INSTANCE}", status=500, json={"message": "down"})
    with bound(evo):
        async with Client(build_server(mode="local")) as client:  # type: ignore[arg-type]
            result = await client.complete(*_complete("reply", "chat", "mar"))

    assert result.completion.values == []
