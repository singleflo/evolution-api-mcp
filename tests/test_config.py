"""Local configuration: blank means unset, precedence, presets, and startup refusal of every bad value."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from evolution_api_mcp import policy, registry
from evolution_api_mcp.config import ConfigError, load_local_config
from evolution_api_mcp.toolsets import DEFAULT_TOOLSETS, TOOLSET_ORDER

SPECS = [
    registry.ToolSpec("list_chats", "List chats", "chats", "read", True),
    registry.ToolSpec("send_text_message", "Send text message", "messaging", "destructive", False),
    registry.ToolSpec("logout_instance", "Log out WhatsApp session", "instance", "irreversible", True),
    registry.ToolSpec("post_status", "Post status update", "status", "destructive", False),
    registry.ToolSpec("set_presence", "Set online presence", "instance", "write", True),
]


@pytest.fixture(autouse=True)
def _fixed_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A registry of five known tools and an empty home directory, so no test depends on the machine."""
    monkeypatch.setattr(registry, "specs", lambda: list(SPECS))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))


def test_blank_variables_behave_as_unset() -> None:
    blank = {
        name: "  "
        for name in (
            "EVOLUTION_API_URL",
            "EVOLUTION_INSTANCE_TOKEN",
            "EVOLUTION_MCP_TOOLSETS",
            "EVOLUTION_MCP_ALLOW",
            "EVOLUTION_MCP_DENY",
            "EVOLUTION_MCP_ALLOW_IRREVERSIBLE",
            "EVOLUTION_MCP_DEFAULT_DELAY_MS",
            "EVOLUTION_MCP_MAX_WRITES_PER_MINUTE",
            "EVOLUTION_MCP_FILE_ROOTS",
            "EVOLUTION_MCP_DOWNLOAD_DIR",
        )
    }

    config = load_local_config(blank)

    assert config.base_url is None
    assert config.token is None
    assert config.toolsets == DEFAULT_TOOLSETS
    assert config.read_only is False
    assert config.allow is None
    assert config.deny == policy.DEFAULT_DENY
    assert config.deny_is_default is True
    assert config.irreversible_granted is False
    assert config.default_delay_ms == 1200
    assert config.max_writes_per_minute == 30


def test_credentials_are_stripped() -> None:
    config = load_local_config({"EVOLUTION_API_URL": " https://evo.example.com ", "EVOLUTION_INSTANCE_TOKEN": " tok "})

    assert config.base_url == "https://evo.example.com"
    assert config.token == "tok"


def test_cli_toolsets_beat_the_environment() -> None:
    config = load_local_config({"EVOLUTION_MCP_TOOLSETS": "groups"}, cli_toolsets="labels,status")

    assert config.toolsets == frozenset({"labels", "status"})


def test_blank_cli_toolsets_fall_back_to_the_environment() -> None:
    config = load_local_config({"EVOLUTION_MCP_TOOLSETS": "groups"}, cli_toolsets="  ")

    assert config.toolsets == frozenset({"groups"})


def test_presets_expand_and_mix_with_names() -> None:
    assert load_local_config({"EVOLUTION_MCP_TOOLSETS": "core"}).toolsets == DEFAULT_TOOLSETS
    assert load_local_config({"EVOLUTION_MCP_TOOLSETS": "ALL"}).toolsets == frozenset(TOOLSET_ORDER)
    assert load_local_config({"EVOLUTION_MCP_TOOLSETS": "core, groups"}).toolsets == DEFAULT_TOOLSETS | {"groups"}


@pytest.mark.parametrize(
    ("environ", "kwargs", "variable"),
    [
        ({"EVOLUTION_MCP_TOOLSETS": "chats,nonsense"}, {}, "EVOLUTION_MCP_TOOLSETS"),
        ({}, {"cli_toolsets": "nonsense"}, "--toolsets"),
    ],
)
def test_unknown_toolset_names_its_source(environ: dict[str, str], kwargs: dict[str, str], variable: str) -> None:
    with pytest.raises(ConfigError) as info:
        load_local_config(environ, **kwargs)

    assert variable in str(info.value)
    assert "nonsense" in str(info.value)


def test_allow_star_is_unrestricted_and_none_is_read_only() -> None:
    assert load_local_config({"EVOLUTION_MCP_ALLOW": "*"}).allow is None
    assert load_local_config({"EVOLUTION_MCP_ALLOW": "*"}).read_only is False

    none = load_local_config({"EVOLUTION_MCP_ALLOW": "NONE"})

    assert none.read_only is True
    assert none.allow is None


def test_cli_read_only_flag() -> None:
    assert load_local_config({}, cli_read_only=True).read_only is True


def test_allow_list_names_write_tools() -> None:
    config = load_local_config({"EVOLUTION_MCP_ALLOW": "send_text_message, set_presence"})

    assert config.allow == frozenset({"send_text_message", "set_presence"})
    assert config.read_only is False


@pytest.mark.parametrize("value", ["send_txt_message", "list_chats", "send_text_message,*"])
def test_allow_rejects_unknown_and_read_tools(value: str) -> None:
    with pytest.raises(ConfigError) as info:
        load_local_config({"EVOLUTION_MCP_ALLOW": value})

    assert "EVOLUTION_MCP_ALLOW" in str(info.value)


def test_deny_replaces_the_default_list() -> None:
    config = load_local_config({"EVOLUTION_MCP_DENY": "logout_instance"})

    assert config.deny == frozenset({"logout_instance"})
    assert config.deny_is_default is False
    assert "post_status" not in config.deny


def test_deny_accepts_read_tools_and_rejects_unknown_names() -> None:
    assert load_local_config({"EVOLUTION_MCP_DENY": "list_chats"}).deny == frozenset({"list_chats"})

    with pytest.raises(ConfigError) as info:
        load_local_config({"EVOLUTION_MCP_DENY": "post_statuss"})

    assert "EVOLUTION_MCP_DENY" in str(info.value)
    assert "post_statuss" in str(info.value)


@pytest.mark.parametrize(
    ("value", "granted"),
    [("yes", True), ("TRUE", True), ("1", True), ("no", False), ("false", False), ("0", False)],
)
def test_irreversible_grant_values(value: str, granted: bool) -> None:
    assert load_local_config({"EVOLUTION_MCP_ALLOW_IRREVERSIBLE": value}).irreversible_granted is granted


def test_irreversible_grant_rejects_other_values() -> None:
    with pytest.raises(ConfigError) as info:
        load_local_config({"EVOLUTION_MCP_ALLOW_IRREVERSIBLE": "maybe"})

    assert "EVOLUTION_MCP_ALLOW_IRREVERSIBLE" in str(info.value)


def test_delay_bounds() -> None:
    assert load_local_config({"EVOLUTION_MCP_DEFAULT_DELAY_MS": "0"}).default_delay_ms == 0
    assert load_local_config({"EVOLUTION_MCP_DEFAULT_DELAY_MS": "20000"}).default_delay_ms == 20000

    for bad in ("20001", "-1", "fast"):
        with pytest.raises(ConfigError) as info:
            load_local_config({"EVOLUTION_MCP_DEFAULT_DELAY_MS": bad})
        assert "EVOLUTION_MCP_DEFAULT_DELAY_MS" in str(info.value)


def test_write_limit_accepts_zero_and_rejects_negative() -> None:
    assert load_local_config({"EVOLUTION_MCP_MAX_WRITES_PER_MINUTE": "0"}).max_writes_per_minute == 0

    with pytest.raises(ConfigError) as info:
        load_local_config({"EVOLUTION_MCP_MAX_WRITES_PER_MINUTE": "-5"})

    assert "EVOLUTION_MCP_MAX_WRITES_PER_MINUTE" in str(info.value)


def test_file_roots_must_be_existing_absolute_directories(tmp_path: Path) -> None:
    first = tmp_path / "a"
    second = tmp_path / "b"
    first.mkdir()
    second.mkdir()

    config = load_local_config({"EVOLUTION_MCP_FILE_ROOTS": os.pathsep.join([str(first), str(second)])})

    assert config.file_roots == (first, second)

    missing = tmp_path / "missing"
    with pytest.raises(ConfigError) as info:
        load_local_config({"EVOLUTION_MCP_FILE_ROOTS": str(missing)})
    assert "EVOLUTION_MCP_FILE_ROOTS" in str(info.value)

    with pytest.raises(ConfigError) as relative:
        load_local_config({"EVOLUTION_MCP_FILE_ROOTS": "relative/dir"})
    assert "EVOLUTION_MCP_FILE_ROOTS" in str(relative.value)


def test_file_roots_reject_a_file(tmp_path: Path) -> None:
    file = tmp_path / "note.txt"
    file.write_text("x")

    with pytest.raises(ConfigError, match="EVOLUTION_MCP_FILE_ROOTS"):
        load_local_config({"EVOLUTION_MCP_FILE_ROOTS": str(file)})


def test_default_file_roots_are_the_existing_home_folders_plus_the_download_dir(tmp_path: Path) -> None:
    (tmp_path / "Documents").mkdir()
    (tmp_path / "Pictures").mkdir()

    config = load_local_config({})

    assert config.download_dir == tmp_path / "Downloads" / "evolution-api-mcp"
    assert config.file_roots == (tmp_path / "Documents", tmp_path / "Pictures", config.download_dir)


def test_download_dir_override_joins_the_default_roots(tmp_path: Path) -> None:
    target = tmp_path / "saved"

    config = load_local_config({"EVOLUTION_MCP_DOWNLOAD_DIR": str(target)})

    assert config.download_dir == target
    assert config.file_roots == (target,)


def test_download_dir_must_be_absolute() -> None:
    with pytest.raises(ConfigError, match="EVOLUTION_MCP_DOWNLOAD_DIR"):
        load_local_config({"EVOLUTION_MCP_DOWNLOAD_DIR": "downloads"})
