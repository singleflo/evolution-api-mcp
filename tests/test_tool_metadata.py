"""What a host sees over the wire: every tool carries a title, the same title in its annotations, and four hints."""

from __future__ import annotations

import pytest
from mcp import Client

from evolution_api_mcp import context, registry
from evolution_api_mcp.server import INSTRUCTIONS, build_server


async def _listed(mode: str, connection: context.Connection):
    with context.override_for_tests(connection, object()):
        async with Client(build_server(mode=mode)) as client:  # type: ignore[arg-type]
            return (await client.list_tools()).tools


@pytest.mark.anyio
@pytest.mark.parametrize(("mode", "expected_count"), [("local", 97), ("hosted", 89)])
async def test_every_tool_publishes_title_annotations_and_all_four_hints(make_connection, mode, expected_count):
    tools = await _listed(mode, make_connection(mode=mode, identity=None, subject="t_test"))

    assert len(tools) == expected_count
    expected_names = [spec.name for spec in registry.specs() if mode == "local" or not spec.local_only]
    assert [tool.name for tool in tools] == expected_names
    for tool in tools:
        assert tool.title, tool.name
        assert tool.annotations is not None, tool.name
        assert tool.annotations.title == tool.title, tool.name
        hints = (
            tool.annotations.read_only_hint,
            tool.annotations.destructive_hint,
            tool.annotations.idempotent_hint,
            tool.annotations.open_world_hint,
        )
        assert all(isinstance(hint, bool) for hint in hints), tool.name
        assert tool.annotations.open_world_hint is True, tool.name
        assert tool.output_schema is None, tool.name
        assert tool.input_schema["type"] == "object", tool.name


@pytest.mark.anyio
async def test_the_listing_follows_the_kind_of_each_tool(make_connection):
    tools = {tool.name: tool for tool in await _listed("local", make_connection(identity=None))}

    assert tools["list_chats"].annotations.read_only_hint is True
    assert tools["list_chats"].annotations.destructive_hint is False
    assert tools["send_text_message"].annotations.read_only_hint is False
    assert tools["send_text_message"].annotations.destructive_hint is True
    assert tools["send_text_message"].annotations.idempotent_hint is False
    assert tools["logout_instance"].annotations.destructive_hint is True
    assert tools["set_presence"].annotations.destructive_hint is False


@pytest.mark.anyio
async def test_the_listing_hides_what_the_connection_may_not_call(make_connection):
    tools = await _listed("local", make_connection(policy="read", identity=None))

    names = {tool.name for tool in tools}
    assert names
    assert all(registry.get(name).kind == "read" for name in names)


@pytest.mark.anyio
async def test_the_server_describes_itself_with_the_reviewed_instructions(make_connection):
    with context.override_for_tests(make_connection(identity=None), object()):
        async with Client(build_server(mode="local")) as client:
            info = client.server_info
            instructions = client.instructions
    assert info is not None
    assert info.name == "evolution-api-mcp"
    assert info.title == "Evolution API Assistant"
    assert instructions == INSTRUCTIONS
