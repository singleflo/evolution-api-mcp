"""The tool set is the reviewed contract of the plan's Master table: names, toolsets, kinds, integrations, flags."""

from __future__ import annotations

import re

import pytest
from mcp.server import MCPServer

from evolution_api_mcp import context, policy, registry
from evolution_api_mcp.registry import BAILEYS, BUSINESS, ToolSpec, annotations_for
from evolution_api_mcp.toolsets import DEFAULT_TOOLSETS, TOOLSET_ORDER

ALL = registry.ALL
BAI = frozenset({BAILEYS})
BAI_BUS = frozenset({BAILEYS, BUSINESS})
BUS = frozenset({BUSINESS})

# (name, toolset, kind, idempotent, integrations, local_only, universal): the Master table, row by row.
R, W, D, I = "read", "write", "destructive", "irreversible"  # noqa: E741
MASTER_TABLE: list[tuple[str, str, str, bool, frozenset[str], bool, bool]] = [
    ("get_instance_status", "instance", R, True, ALL, False, True),
    ("start_pairing", "instance", W, False, BAI, False, False),
    ("restart_instance", "instance", D, False, BAI, False, False),
    ("logout_instance", "instance", I, True, BAI, False, False),
    ("set_presence", "instance", W, True, BAI, False, False),
    ("send_text_message", "messaging", D, False, ALL, False, False),
    ("send_media_message", "messaging", D, False, ALL, False, False),
    ("send_local_file", "messaging", D, False, ALL, True, False),
    ("send_voice_note", "messaging", D, False, ALL, False, False),
    ("send_video_note", "messaging", D, False, BAI, False, False),
    ("send_sticker", "messaging", D, False, BAI, False, False),
    ("send_location", "messaging", D, False, BAI_BUS, False, False),
    ("send_contact_card", "messaging", D, False, BAI_BUS, False, False),
    ("send_poll", "messaging", D, False, BAI, False, False),
    ("send_list_message", "messaging", D, False, BAI_BUS, False, False),
    ("send_button_message", "messaging", D, False, ALL, False, False),
    ("send_chat_presence", "messaging", W, False, BAI, False, False),
    ("react_to_message", "messaging", D, True, BAI_BUS, False, False),
    ("edit_message", "messaging", D, True, BAI, False, False),
    ("delete_message_for_everyone", "messaging", I, True, BAI, False, False),
    ("list_chats", "chats", R, True, ALL, False, False),
    ("get_chat", "chats", R, True, ALL, False, False),
    ("read_messages", "chats", R, True, ALL, False, False),
    ("search_messages", "chats", R, True, ALL, False, False),
    ("get_message", "chats", R, True, ALL, False, False),
    ("get_message_status", "chats", R, True, ALL, False, False),
    ("view_message_image", "chats", R, True, BAI_BUS, False, False),
    ("download_message_media", "chats", W, True, BAI_BUS, False, False),
    ("mark_chat_read", "chats", D, True, BAI, False, False),
    ("mark_chat_unread", "chats", W, True, BAI, False, False),
    ("set_chat_archived", "chats", W, True, BAI, False, False),
    ("find_contacts", "contacts", R, True, ALL, False, False),
    ("check_whatsapp_numbers", "contacts", R, True, BAI, False, False),
    ("get_contact_profile", "contacts", R, True, BAI, False, False),
    ("get_business_profile", "contacts", R, True, BAI, False, False),
    ("block_contact", "contacts", D, True, BAI, False, False),
    ("unblock_contact", "contacts", W, True, BAI, False, False),
    ("list_groups", "groups", R, True, BAI, False, False),
    ("get_group", "groups", R, True, BAI, False, False),
    ("get_group_invite_link", "groups", R, True, BAI, False, False),
    ("get_invite_info", "groups", R, True, BAI, False, False),
    ("create_group", "groups", D, False, BAI, False, False),
    ("update_group_subject", "groups", D, True, BAI, False, False),
    ("update_group_description", "groups", D, True, BAI, False, False),
    ("update_group_picture", "groups", D, True, BAI, False, False),
    ("update_group_settings", "groups", D, True, BAI, False, False),
    ("set_disappearing_messages", "groups", D, True, BAI, False, False),
    ("add_group_participants", "groups", D, True, BAI, False, False),
    ("remove_group_participants", "groups", D, True, BAI, False, False),
    ("update_group_admins", "groups", D, True, BAI, False, False),
    ("send_group_invite", "groups", D, False, BAI, False, False),
    ("revoke_group_invite_link", "groups", D, False, BAI, False, False),
    ("join_group_by_invite", "groups", D, True, BAI, False, False),
    ("leave_group", "groups", I, True, BAI, False, False),
    ("list_labels", "labels", R, True, BAI, False, False),
    ("add_chat_label", "labels", W, True, BAI, False, False),
    ("remove_chat_label", "labels", W, True, BAI, False, False),
    ("get_my_profile", "profile", R, True, BAI, False, False),
    ("get_privacy_settings", "profile", R, True, BAI, False, False),
    ("update_privacy_settings", "profile", D, True, BAI, False, False),
    ("update_profile_name", "profile", D, True, BAI, False, False),
    ("update_profile_about", "profile", D, True, BAI, False, False),
    ("update_profile_picture", "profile", D, True, BAI, False, False),
    ("remove_profile_picture", "profile", D, True, BAI, False, False),
    ("post_status", "status", D, False, BAI, False, False),
    ("list_catalog_products", "catalog", R, True, BAI, False, False),
    ("list_catalog_collections", "catalog", R, True, BAI, False, False),
    ("list_templates", "templates", R, True, BUS, False, False),
    ("send_template_message", "templates", D, False, BUS, False, False),
    ("create_template", "templates", W, False, BUS, False, False),
    ("edit_template", "templates", D, True, BUS, False, False),
    ("delete_template", "templates", I, True, BUS, False, False),
    ("get_instance_settings", "settings", R, True, ALL, False, False),
    ("update_instance_settings", "settings", D, True, ALL, False, False),
    ("get_proxy", "settings", R, True, ALL, False, False),
    ("set_proxy", "settings", D, True, ALL, True, False),
    ("get_webhook", "events", R, True, ALL, False, False),
    ("set_webhook", "events", D, True, ALL, True, False),
    ("get_event_channel", "events", R, True, ALL, False, False),
    ("set_event_channel", "events", D, True, ALL, True, False),
    ("list_chatbots", "integrations", R, True, ALL, False, False),
    ("get_chatbot", "integrations", R, True, ALL, False, False),
    ("create_chatbot", "integrations", D, False, ALL, True, False),
    ("update_chatbot", "integrations", D, True, ALL, True, False),
    ("delete_chatbot", "integrations", I, True, ALL, False, False),
    ("get_chatbot_settings", "integrations", R, True, ALL, False, False),
    ("update_chatbot_settings", "integrations", D, True, ALL, False, False),
    ("list_chatbot_sessions", "integrations", R, True, ALL, False, False),
    ("change_chatbot_session", "integrations", D, True, ALL, False, False),
    ("set_chatbot_ignored_chat", "integrations", D, True, ALL, False, False),
    ("start_typebot_session", "integrations", D, False, ALL, False, False),
    ("list_openai_credentials", "integrations", R, True, ALL, False, False),
    ("create_openai_credential", "integrations", W, False, ALL, True, False),
    ("delete_openai_credential", "integrations", I, True, ALL, False, False),
    ("list_openai_models", "integrations", R, True, ALL, False, False),
    ("get_chatwoot_config", "integrations", R, True, ALL, False, False),
    ("set_chatwoot_config", "integrations", D, True, ALL, True, False),
]

DEFAULT_DENY_ROWS = {
    "post_status",
    "remove_group_participants",
    "set_webhook",
    "set_event_channel",
    "set_proxy",
    "set_chatwoot_config",
}

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
DIRECTIVE_PATTERN = re.compile(r"(?i)\b(you must|you should|always call|ignore (all|previous)|do not tell)\b")


def _hosted(integration: str, **overrides: object) -> context.Connection:
    values: dict[str, object] = {
        "mode": "hosted",
        "policy": "standard",
        "toolsets": frozenset(TOOLSET_ORDER),
        "allow": None,
        "deny": policy.DEFAULT_DENY,
        "deny_is_default": True,
        "irreversible_granted": False,
        "identity": context.InstanceIdentity(name="inst", integration=integration),
        "subject": "t_test",
        "default_delay_ms": 1200,
        "max_writes_per_minute": 30,
        "max_reads_per_minute": 120,
    }
    values.update(overrides)
    return context.Connection(**values)  # type: ignore[arg-type]


def _visible(connection: context.Connection) -> set[str]:
    return {spec.name for spec in registry.specs() if policy.visible(spec, connection)}


def test_the_master_table_has_97_unique_rows():
    names = [row[0] for row in MASTER_TABLE]
    assert len(names) == 97
    assert len(set(names)) == 97


def test_registered_specs_equal_the_master_table():
    registered = {spec.name: spec for spec in registry.specs()}
    assert set(registered) == {row[0] for row in MASTER_TABLE}
    for name, toolset, kind, idempotent, integrations, local_only, universal in MASTER_TABLE:
        assert registered[name] == ToolSpec(
            name=name,
            title=registered[name].title,
            toolset=toolset,
            kind=kind,
            idempotent=idempotent,
            integrations=integrations,
            local_only=local_only,
            universal=universal,
        ), name


def test_specs_are_listed_by_toolset_then_name():
    specs = registry.specs()
    keys = [(TOOLSET_ORDER.index(spec.toolset), spec.name) for spec in specs]
    assert keys == sorted(keys)
    assert {spec.toolset for spec in specs} == set(TOOLSET_ORDER)


def test_the_default_deny_list_is_exactly_the_flagged_rows():
    assert policy.DEFAULT_DENY == DEFAULT_DENY_ROWS
    assert DEFAULT_DENY_ROWS <= {row[0] for row in MASTER_TABLE}


def test_every_tool_has_a_valid_name_and_a_title():
    for spec in registry.specs():
        assert NAME_PATTERN.match(spec.name), spec.name
        assert spec.title.strip(), spec.name


def test_annotations_follow_the_kind_of_every_tool():
    for spec in registry.specs():
        hints = annotations_for(spec)
        assert hints.title == spec.title
        assert hints.read_only_hint is (spec.kind == "read"), spec.name
        assert hints.destructive_hint is (spec.kind in ("destructive", "irreversible")), spec.name
        assert hints.idempotent_hint is spec.idempotent, spec.name
        assert hints.open_world_hint is True, spec.name


@pytest.mark.anyio
async def test_descriptions_are_short_first_lines_without_directives():
    server = MCPServer(name="descriptions")
    registry.register_all(server, mode="local")
    listed = await server.list_tools()
    assert len(listed) == 97
    for tool in listed:
        description = tool.description or ""
        first_line = description.splitlines()[0] if description else ""
        assert first_line, tool.name
        assert len(first_line) <= 120, f"{tool.name}: first line is {len(first_line)} characters"
        assert not DIRECTIVE_PATTERN.search(description), tool.name
        assert not re.search(r"(?m)^#", description), f"{tool.name}: description contains a Markdown heading"


def test_registration_publishes_97_tools_locally_and_89_hosted():
    local = registry.register_all(MCPServer(name="local"), mode="local")
    hosted = registry.register_all(MCPServer(name="hosted"), mode="hosted")
    assert len(local) == 97
    assert len(hosted) == 89
    assert set(local) - set(hosted) == {row[0] for row in MASTER_TABLE if row[5]}
    assert local == [spec.name for spec in registry.specs()]


def test_a_hosted_business_tenant_sees_exactly_38_tools():
    visible = _visible(_hosted(BUSINESS))
    expected = {
        row[0]
        for row in MASTER_TABLE
        if BUSINESS in row[4] and not row[5] and row[2] != "irreversible" and row[0] not in DEFAULT_DENY_ROWS
    }
    assert visible == expected
    assert len(visible) == 38


def test_a_read_only_hosted_tenant_sees_only_reads():
    visible = _visible(_hosted(BUSINESS, policy="read"))
    assert visible
    assert all(registry.get(name).kind == "read" for name in visible)


def test_a_local_baileys_instance_with_default_toolsets_sees_33_tools_once_irreversible_is_granted(make_connection):
    conn = make_connection(toolsets=DEFAULT_TOOLSETS, deny=policy.DEFAULT_DENY, deny_is_default=True)
    visible = _visible(conn)
    assert len(visible) == 33
    expected = {
        row[0]
        for row in MASTER_TABLE
        if BAILEYS in row[4] and (row[1] in DEFAULT_TOOLSETS or row[6]) and row[0] not in DEFAULT_DENY_ROWS
    }
    assert visible == expected


def test_the_irreversible_grant_is_the_only_thing_hiding_delete_message_for_everyone_by_default(make_connection):
    conn = make_connection(
        toolsets=DEFAULT_TOOLSETS, deny=policy.DEFAULT_DENY, deny_is_default=True, irreversible_granted=False
    )
    hidden = _visible(make_connection(toolsets=DEFAULT_TOOLSETS, deny=policy.DEFAULT_DENY, deny_is_default=True)) - (
        _visible(conn)
    )
    assert hidden == {"delete_message_for_everyone"}
