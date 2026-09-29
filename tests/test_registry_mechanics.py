"""Registry mechanics: hints derived from kinds, ordering, duplicates, the call gate and registration per mode."""

from __future__ import annotations

from typing import Annotated

import pytest
from mcp import Client
from mcp.server import MCPServer
from pydantic import Field

from evolution_api_mcp import context, registry
from evolution_api_mcp.errors import ToolExecutionError
from evolution_api_mcp.registry import BAILEYS, ToolSpec, annotations_for


@pytest.fixture
def isolated_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the registry with an empty one (the real tool modules are already imported, so nothing is lost)."""
    monkeypatch.setattr(registry, "_REGISTRY", {})


@pytest.mark.parametrize(
    ("kind", "read_only", "destructive"),
    [("read", True, False), ("write", False, False), ("destructive", False, True), ("irreversible", False, True)],
)
def test_annotations_are_derived_from_the_kind(kind, read_only, destructive):
    spec = ToolSpec(name="a_tool", title="A tool", toolset="chats", kind=kind, idempotent=True)
    hints = annotations_for(spec)
    assert hints.title == "A tool"
    assert hints.read_only_hint is read_only
    assert hints.destructive_hint is destructive
    assert hints.idempotent_hint is True
    assert hints.open_world_hint is True


def test_idempotent_hint_follows_the_spec():
    spec = ToolSpec(name="a_tool", title="A tool", toolset="messaging", kind="destructive", idempotent=False)
    assert annotations_for(spec).idempotent_hint is False


def test_specs_are_ordered_by_toolset_then_name(isolated_registry):
    for toolset, name in [("groups", "b_tool"), ("instance", "z_tool"), ("groups", "a_tool"), ("chats", "m_tool")]:

        async def fn() -> str:
            return "x"

        fn.__name__ = name
        registry.tool(title=name, toolset=toolset, kind="read", idempotent=True)(fn)
    assert [spec.name for spec in registry.specs()] == ["z_tool", "m_tool", "a_tool", "b_tool"]


def test_duplicate_tool_names_are_rejected(isolated_registry):
    @registry.tool(title="One", toolset="chats", kind="read", idempotent=True)
    async def same_name() -> str:
        return "one"

    with pytest.raises(ValueError, match="same_name"):

        @registry.tool(title="Two", toolset="chats", kind="read", idempotent=True)
        async def same_name() -> str:  # noqa: F811
            return "two"


def test_an_unknown_toolset_is_rejected(isolated_registry):
    with pytest.raises(ValueError, match="toolset"):

        @registry.tool(title="Odd", toolset="nonsense", kind="read", idempotent=True)
        async def odd_tool() -> str:
            return "x"


def test_get_returns_the_spec_and_raises_for_unknown_names(isolated_registry):
    @registry.tool(title="Known", toolset="chats", kind="read", idempotent=True)
    async def known_tool() -> str:
        return "x"

    assert registry.get("known_tool").title == "Known"
    with pytest.raises(KeyError):
        registry.get("unknown_tool")


@pytest.mark.anyio
async def test_a_call_runs_the_function_when_the_gate_allows_it(isolated_registry, make_connection):
    @registry.tool(title="Echo", toolset="chats", kind="read", idempotent=True)
    async def echo_tool(text: str) -> str:
        return f"echo {text}"

    with context.override_for_tests(make_connection(), object()):
        assert await echo_tool("hi") == "echo hi"


@pytest.mark.anyio
async def test_a_refused_call_raises_the_policy_reason_and_never_runs(isolated_registry, make_connection):
    calls: list[str] = []

    @registry.tool(title="Send", toolset="messaging", kind="destructive", idempotent=False)
    async def send_tool() -> str:
        calls.append("ran")
        return "sent"

    with context.override_for_tests(make_connection(policy="read"), object()):
        with pytest.raises(ToolExecutionError, match="send_tool changes data, and this server is read-only"):
            await send_tool()
    assert calls == []


@pytest.mark.anyio
async def test_a_call_over_the_rate_limit_is_refused_before_running(isolated_registry, make_connection):
    calls: list[str] = []

    @registry.tool(title="Send", toolset="messaging", kind="destructive", idempotent=False)
    async def send_tool() -> str:
        calls.append("ran")
        return "sent"

    with context.override_for_tests(make_connection(max_writes_per_minute=1), object()):
        await send_tool()
        with pytest.raises(ToolExecutionError, match="Rate limit reached"):
            await send_tool()
    assert calls == ["ran"]


@pytest.mark.anyio
async def test_register_all_publishes_hints_description_and_input_schema(isolated_registry, make_connection):
    @registry.tool(title="Find thing", toolset="chats", kind="read", idempotent=True)
    async def find_thing(
        query: Annotated[str, Field(min_length=2, description="Words to look for.")],
        limit: Annotated[int, Field(ge=1, le=50, description="Maximum results.")] = 20,
    ) -> str:
        """Find things by keyword.

        Returns matching things, newest first.
        """
        return "found"

    server = MCPServer(name="test")
    assert registry.register_all(server, mode="local") == ["find_thing"]
    with context.override_for_tests(make_connection(), object()):
        async with Client(server) as client:
            (tool,) = (await client.list_tools()).tools
            assert tool.title == "Find thing"
            assert tool.annotations.title == "Find thing"
            assert tool.annotations.read_only_hint is True
            assert tool.annotations.open_world_hint is True
            assert tool.description == "Find things by keyword.\n\nReturns matching things, newest first."
            assert tool.output_schema is None
            assert tool.input_schema["required"] == ["query"]
            assert tool.input_schema["properties"]["query"]["description"] == "Words to look for."
            assert tool.input_schema["properties"]["limit"]["maximum"] == 50

            result = await client.call_tool("find_thing", {"query": "x1"})
            assert result.content[0].text == "found"


@pytest.mark.anyio
async def test_register_all_skips_local_only_tools_when_hosted(isolated_registry):
    @registry.tool(title="Open thing", toolset="chats", kind="read", idempotent=True)
    async def open_thing() -> str:
        return "x"

    @registry.tool(
        title="Local thing",
        toolset="settings",
        kind="destructive",
        idempotent=True,
        local_only=True,
        integrations=frozenset({BAILEYS}),
    )
    async def local_thing() -> str:
        return "x"

    assert registry.register_all(MCPServer(name="hosted"), mode="hosted") == ["open_thing"]
    assert registry.register_all(MCPServer(name="local"), mode="local") == ["open_thing", "local_thing"]
