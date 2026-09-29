"""tools/list filtering: the listing shows exactly what a call would be allowed to do."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from mcp import Client
from mcp.server import MCPServer
from mcp_types import ListToolsResult, Tool

from evolution_api_mcp import context, registry
from evolution_api_mcp.middleware import VisibilityMiddleware, filter_tools_result
from evolution_api_mcp.registry import BAILEYS, BUSINESS


@pytest.fixture
def sample_registry(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Swap in a small registry (the real one is already imported, so nothing is lost) and return its names."""
    monkeypatch.setattr(registry, "_REGISTRY", {})

    @registry.tool(title="Read chats", toolset="chats", kind="read", idempotent=True)
    async def read_chats() -> str:
        """Read chats."""
        return "chats"

    @registry.tool(title="Get status", toolset="instance", kind="read", idempotent=True, universal=True)
    async def get_status() -> str:
        """Get status."""
        return "status"

    @registry.tool(title="Send thing", toolset="messaging", kind="destructive", idempotent=False)
    async def send_thing() -> str:
        """Send thing."""
        return "sent"

    @registry.tool(title="Wipe thing", toolset="messaging", kind="irreversible", idempotent=True)
    async def wipe_thing() -> str:
        """Wipe thing."""
        return "wiped"

    @registry.tool(
        title="Make group", toolset="groups", kind="destructive", idempotent=False, integrations=frozenset({BAILEYS})
    )
    async def make_group() -> str:
        """Make group."""
        return "group"

    @registry.tool(title="Set proxy", toolset="settings", kind="destructive", idempotent=True, local_only=True)
    async def set_proxy() -> str:
        """Set proxy."""
        return "proxy"

    return [spec.name for spec in registry.specs()]


def wire_result(names: list[str]) -> dict:
    return {"tools": [{"name": name, "title": name} for name in names], "nextCursor": None}


def names_of(result: dict) -> list[str]:
    return [tool["name"] for tool in result["tools"]]


def test_toolset_selection_hides_other_toolsets_but_not_universal_tools(sample_registry, make_connection):
    conn = make_connection(toolsets=frozenset({"chats"}))
    shown = names_of(filter_tools_result(wire_result(sample_registry), conn))
    assert shown == ["get_status", "read_chats"]


def test_integration_rule_hides_tools_the_instance_cannot_use(sample_registry, make_connection, make_identity):
    all_names = wire_result(sample_registry)
    assert "make_group" in names_of(filter_tools_result(all_names, make_connection(identity=make_identity(BAILEYS))))
    assert "make_group" not in names_of(
        filter_tools_result(all_names, make_connection(identity=make_identity(BUSINESS)))
    )
    assert "make_group" in names_of(filter_tools_result(all_names, make_connection(identity=None)))


def test_read_only_policy_leaves_only_reads(sample_registry, make_connection):
    shown = names_of(filter_tools_result(wire_result(sample_registry), make_connection(policy="read")))
    assert shown == ["get_status", "read_chats"]


def test_deny_and_allow_lists_filter_writes_but_never_reads(sample_registry, make_connection):
    all_names = wire_result(sample_registry)
    denied = make_connection(deny=frozenset({"send_thing", "read_chats"}))
    assert "send_thing" not in names_of(filter_tools_result(all_names, denied))
    assert "read_chats" in names_of(filter_tools_result(all_names, denied))

    only_wipe = make_connection(allow=frozenset({"wipe_thing"}))
    assert names_of(filter_tools_result(all_names, only_wipe)) == ["get_status", "wipe_thing", "read_chats"]


def test_irreversible_tools_need_the_grant(sample_registry, make_connection):
    all_names = wire_result(sample_registry)
    assert "wipe_thing" in names_of(filter_tools_result(all_names, make_connection(irreversible_granted=True)))
    assert "wipe_thing" not in names_of(filter_tools_result(all_names, make_connection(irreversible_granted=False)))


def test_hosted_connection_hides_local_only_and_irreversible_tools(sample_registry, make_connection):
    conn = make_connection(mode="hosted", irreversible_granted=False, subject="t_abc")
    shown = names_of(filter_tools_result(wire_result(sample_registry), conn))
    assert "set_proxy" not in shown
    assert "wipe_thing" not in shown
    assert {"read_chats", "send_thing", "make_group"} <= set(shown)


def test_order_and_other_result_keys_are_preserved(sample_registry, make_connection):
    shuffled = ["send_thing", "read_chats", "make_group", "get_status"]
    result = {**wire_result(shuffled), "_meta": {"k": 1}}
    filtered = filter_tools_result(result, make_connection(toolsets=frozenset({"chats", "messaging"})))
    assert names_of(filtered) == ["send_thing", "read_chats", "get_status"]
    assert filtered["_meta"] == {"k": 1}
    assert names_of(result) == shuffled


class TestMiddleware:
    @pytest.fixture
    def middleware(self) -> VisibilityMiddleware:
        return VisibilityMiddleware()

    @staticmethod
    def request(method: str) -> SimpleNamespace:
        return SimpleNamespace(method=method)

    @pytest.mark.anyio
    async def test_only_tools_list_is_touched(self, middleware, sample_registry, make_connection):
        sentinel = {"content": [{"type": "text", "text": "hi"}]}

        async def call_next(_ctx):
            return sentinel

        with context.override_for_tests(make_connection(policy="read"), object()):
            assert await middleware(self.request("tools/call"), call_next) is sentinel

    @pytest.mark.anyio
    async def test_tools_list_is_filtered_for_the_connection(self, middleware, sample_registry, make_connection):
        async def call_next(_ctx):
            return wire_result(sample_registry)

        with context.override_for_tests(make_connection(policy="read"), object()):
            result = await middleware(self.request("tools/list"), call_next)
        assert names_of(result) == ["get_status", "read_chats"]

    @pytest.mark.anyio
    async def test_a_model_result_is_converted_to_the_wire_dict_first(
        self, middleware, sample_registry, make_connection
    ):
        model = ListToolsResult(
            tools=[Tool(name=name, input_schema={"type": "object"}) for name in sample_registry],
        )

        async def call_next(_ctx):
            return model

        with context.override_for_tests(make_connection(policy="read"), object()):
            result = await middleware(self.request("tools/list"), call_next)
        assert isinstance(result, dict)
        assert names_of(result) == ["get_status", "read_chats"]
        assert "inputSchema" in result["tools"][0]

    @pytest.mark.anyio
    async def test_local_listing_stays_stable_once_identity_has_settled(
        self, middleware, sample_registry, make_connection, make_identity
    ):
        async def call_next(_ctx):
            return wire_result(sample_registry)

        baileys = make_connection(identity=make_identity(BAILEYS))
        business = make_connection(identity=make_identity(BUSINESS))
        with context.override_for_tests(baileys, object()):
            first = names_of(await middleware(self.request("tools/list"), call_next))
        with context.override_for_tests(business, object()):
            second = names_of(await middleware(self.request("tools/list"), call_next))
        assert "make_group" in first
        assert second == first

    @pytest.mark.anyio
    async def test_local_listing_is_recomputed_until_identity_has_settled(
        self, middleware, sample_registry, make_connection, make_identity, monkeypatch
    ):
        async def call_next(_ctx):
            return wire_result(sample_registry)

        monkeypatch.setattr(context, "listing_settled", lambda: False)
        unknown = make_connection(identity=None)
        business = make_connection(identity=make_identity(BUSINESS))
        with context.override_for_tests(unknown, object()):
            before = names_of(await middleware(self.request("tools/list"), call_next))
        with context.override_for_tests(business, object()):
            after = names_of(await middleware(self.request("tools/list"), call_next))
        assert "make_group" in before
        assert "make_group" not in after

    @pytest.mark.anyio
    async def test_hosted_listing_follows_the_authorization_of_each_request(
        self, middleware, sample_registry, make_connection
    ):
        async def call_next(_ctx):
            return wire_result(sample_registry)

        reader = make_connection(mode="hosted", policy="read", irreversible_granted=False, subject="t_a")
        actor = make_connection(mode="hosted", policy="standard", irreversible_granted=False, subject="t_b")
        with context.override_for_tests(reader, object()):
            read_view = names_of(await middleware(self.request("tools/list"), call_next))
        with context.override_for_tests(actor, object()):
            act_view = names_of(await middleware(self.request("tools/list"), call_next))
        assert "send_thing" not in read_view
        assert "send_thing" in act_view


@pytest.mark.anyio
async def test_a_real_server_lists_and_calls_only_visible_tools(sample_registry, make_connection):
    server = MCPServer(name="test", middleware=[VisibilityMiddleware()])
    registry.register_all(server, mode="local")
    conn = make_connection(policy="read")
    with context.override_for_tests(conn, object()):
        async with Client(server) as client:
            listed = await client.list_tools()
            assert [tool.name for tool in listed.tools] == ["get_status", "read_chats"]

            refused = await client.call_tool("send_thing", {})
            assert refused.is_error
            assert "send_thing changes data, and this server is read-only" in refused.content[0].text

            allowed = await client.call_tool("read_chats", {})
            assert not allowed.is_error
            assert allowed.content[0].text == "chats"
