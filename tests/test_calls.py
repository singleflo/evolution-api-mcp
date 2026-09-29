import pytest

from evolution_api_mcp import calls
from evolution_api_mcp.client import EvolutionClient, EvolutionHTTPError, EvolutionUncertain
from evolution_api_mcp.errors import ToolExecutionError
from tests.conftest import BASE, INSTANCE, TOKEN


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


def test_phone_refuses_groups_and_accepts_formatted_numbers():
    assert calls.phone("+39 333 123 4567") == "393331234567"
    with pytest.raises(ToolExecutionError, match="not a phone number"):
        calls.phone("120363000000000000@g.us")
    with pytest.raises(ToolExecutionError, match="Unsupported chat id"):
        calls.chat("abc")
