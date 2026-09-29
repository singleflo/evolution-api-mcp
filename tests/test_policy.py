"""The safety gate: every refusal rule, in order, with the exact local and hosted wording."""

from __future__ import annotations

from evolution_api_mcp.policy import refusal, visible
from evolution_api_mcp.registry import BAILEYS, BUSINESS, ToolSpec


def spec(name: str = "do_thing", **overrides: object) -> ToolSpec:
    fields: dict[str, object] = {
        "name": name,
        "title": "Do thing",
        "toolset": "messaging",
        "kind": "destructive",
        "idempotent": False,
    }
    return ToolSpec(**{**fields, **overrides})  # type: ignore[arg-type]


def test_a_permitted_call_has_no_refusal(make_connection):
    conn = make_connection()
    assert refusal(spec(), conn) is None
    assert visible(spec(), conn)


def test_local_only_tool_is_refused_when_hosted(make_connection):
    conn = make_connection(mode="hosted")
    assert refusal(spec(local_only=True), conn) == (
        "do_thing is available only on the local server (uvx evolution-api-mcp)."
    )
    assert refusal(spec(local_only=True), make_connection()) is None


def test_disabled_toolset_message_names_the_toolset_locally(make_connection):
    conn = make_connection(toolsets=frozenset({"chats"}))
    assert refusal(spec(), conn) == (
        "do_thing belongs to the 'messaging' toolset, which is not enabled. "
        "Add 'messaging' to EVOLUTION_MCP_TOOLSETS (or --toolsets) and restart the server."
    )


def test_disabled_toolset_message_points_to_the_consent_page_when_hosted(make_connection):
    conn = make_connection(mode="hosted", toolsets=frozenset({"chats"}))
    assert refusal(spec(), conn) == (
        "do_thing belongs to the 'messaging' toolset, which this connection did not enable. "
        "Disconnect and reconnect, then tick 'messaging' on the consent page."
    )


def test_universal_tool_ignores_the_toolset_selection(make_connection):
    conn = make_connection(toolsets=frozenset({"chats"}))
    assert refusal(spec("get_status", toolset="instance", kind="read", universal=True), conn) is None


def test_integration_mismatch_lists_the_needed_integrations_sorted(make_connection, make_identity):
    conn = make_connection(identity=make_identity(BUSINESS))
    assert refusal(spec(integrations=frozenset({BAILEYS})), conn) == (
        "do_thing is not available for this instance: it needs WHATSAPP-BAILEYS, "
        "and this instance uses WHATSAPP-BUSINESS."
    )
    two = spec(integrations=frozenset({BUSINESS, BAILEYS}))
    assert refusal(two, make_connection(identity=make_identity("EVOLUTION"))) == (
        "do_thing is not available for this instance: it needs WHATSAPP-BAILEYS or WHATSAPP-BUSINESS, "
        "and this instance uses EVOLUTION."
    )
    assert refusal(two, conn) is None


def test_unknown_identity_skips_the_integration_rule(make_connection):
    conn = make_connection(identity=None)
    assert refusal(spec(integrations=frozenset({BAILEYS})), conn) is None


def test_reads_are_never_gated_by_policy_lists_or_grants(make_connection):
    read = spec("list_things", toolset="chats", kind="read", idempotent=True)
    conn = make_connection(
        policy="read", allow=frozenset(), deny=frozenset({"list_things"}), irreversible_granted=False
    )
    assert refusal(read, conn) is None


def test_reads_still_need_their_toolset_and_integration(make_connection, make_identity):
    read = spec("list_things", toolset="chats", kind="read", idempotent=True, integrations=frozenset({BAILEYS}))
    assert refusal(read, make_connection(toolsets=frozenset({"groups"}))) is not None
    assert refusal(read, make_connection(identity=make_identity(BUSINESS))) is not None


def test_read_only_policy_message_local_and_hosted(make_connection):
    assert refusal(spec(), make_connection(policy="read")) == (
        "do_thing changes data, and this server is read-only (EVOLUTION_MCP_ALLOW=none or --read-only)."
    )
    assert refusal(spec(), make_connection(mode="hosted", policy="read")) == (
        "do_thing changes data, and this connection was authorised as read-only. "
        "Reconnect and choose the standard policy to allow it."
    )


def test_irreversible_needs_the_grant_local_and_hosted(make_connection):
    irreversible = spec("wipe_thing", kind="irreversible")
    assert refusal(irreversible, make_connection(irreversible_granted=False)) == (
        "wipe_thing cannot be undone, so it runs only when EVOLUTION_MCP_ALLOW_IRREVERSIBLE=yes is set for this server."
    )
    assert refusal(irreversible, make_connection(mode="hosted", irreversible_granted=False)) == (
        "wipe_thing cannot be undone and is never available on the hosted server."
    )
    assert refusal(irreversible, make_connection(irreversible_granted=True)) is None


def test_deny_list_message_marks_the_default_list(make_connection):
    assert refusal(spec(), make_connection(deny=frozenset({"do_thing"}), deny_is_default=True)) == (
        "do_thing is in EVOLUTION_MCP_DENY (default list). Remove it from that list to allow it."
    )
    assert refusal(spec(), make_connection(deny=frozenset({"do_thing"}), deny_is_default=False)) == (
        "do_thing is in EVOLUTION_MCP_DENY. Remove it from that list to allow it."
    )
    assert refusal(spec(), make_connection(mode="hosted", deny=frozenset({"do_thing"}))) == (
        "do_thing is not available on the hosted server."
    )


def test_allow_list_admits_only_named_tools(make_connection):
    conn = make_connection(allow=frozenset({"do_thing"}))
    assert refusal(spec(), conn) is None
    assert refusal(spec("other_thing"), conn) == "other_thing is not in EVOLUTION_MCP_ALLOW."
    assert refusal(spec(), make_connection(allow=frozenset())) == "do_thing is not in EVOLUTION_MCP_ALLOW."


def test_rules_apply_in_the_documented_order(make_connection, make_identity):
    tool = spec("wipe_thing", kind="irreversible", integrations=frozenset({BAILEYS}), local_only=True)
    state: dict[str, object] = {
        "mode": "hosted",
        "policy": "read",
        "toolsets": frozenset({"chats"}),
        "identity": make_identity(BUSINESS),
        "irreversible_granted": False,
        "deny": frozenset({"wipe_thing"}),
        "allow": frozenset(),
    }
    steps = [
        (None, "wipe_thing is available only on the local server (uvx evolution-api-mcp)."),
        ({"mode": "local"}, "wipe_thing belongs to the 'messaging' toolset, which is not enabled."),
        ({"toolsets": frozenset({"messaging"})}, "wipe_thing is not available for this instance:"),
        ({"identity": make_identity(BAILEYS)}, "wipe_thing changes data, and this server is read-only"),
        ({"policy": "standard"}, "wipe_thing cannot be undone, so it runs only when"),
        ({"irreversible_granted": True}, "wipe_thing is in EVOLUTION_MCP_DENY."),
        ({"deny": frozenset()}, "wipe_thing is not in EVOLUTION_MCP_ALLOW."),
    ]
    for cure, expected_start in steps:
        state.update(cure or {})
        message = refusal(tool, make_connection(**state))
        assert message is not None
        assert message.startswith(expected_start), message

    state["allow"] = None
    assert refusal(tool, make_connection(**state)) is None


def test_visible_is_false_for_a_refused_tool_and_true_for_a_permitted_one(make_connection):
    denied = make_connection(deny=frozenset({"do_thing"}))
    assert not visible(spec(), denied)
    assert visible(spec("other_thing"), denied)
