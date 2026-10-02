"""The registry manifest, the plugin manifests and the marketplaces must stay installable, consistent and secret-free.

The version strings in pyproject.toml, server.json, plugins/evolution-api-mcp/plugin.json,
plugins/evolution-api-mcp/.claude-plugin/plugin.json and the Claude marketplace entry move together, and every
environment variable server.json advertises must be one the server actually reads, with the documented default.
"""

from __future__ import annotations

import json
import re
import struct
import subprocess
import sys
from pathlib import Path

import tomllib

from evolution_api_mcp import config, policy, registry
from evolution_api_mcp.toolsets import DEFAULT_TOOLSETS, TOOLSET_ORDER

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/evolution-api-mcp"
PUBLIC_URL = "https://evolution-mcp.singleflo.com"
MCP_NAME = "io.github.singleflo/evolution-api-mcp"

MANIFESTS = [
    ROOT / "server.json",
    PLUGIN / "plugin.json",
    PLUGIN / "mcp.json",
    PLUGIN / ".claude-plugin/plugin.json",
    PLUGIN / ".mcp.json",
    ROOT / ".claude-plugin/marketplace.json",
    ROOT / ".agents/plugins/marketplace.json",
]

ENV_VARS = ["EVOLUTION_API_URL", "EVOLUTION_INSTANCE_TOKEN"]
MAX_REGISTRY_DESCRIPTION = 100
MAX_OPENAI_SHORT_DESCRIPTION = 30


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


def _pyproject_version() -> str:
    with open(ROOT / "pyproject.toml", "rb") as fh:
        return tomllib.load(fh)["project"]["version"]


def _server_env() -> dict[str, dict]:
    (package,) = _load(ROOT / "server.json")["packages"]
    return {entry["name"]: entry for entry in package["environmentVariables"]}


def _config_env_names() -> set[str]:
    return {value for name, value in vars(config).items() if name.startswith("ENV_") and isinstance(value, str)}


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG"
    return struct.unpack(">II", data[16:24])


def test_every_manifest_json_parses():
    for path in MANIFESTS:
        assert _load(path), f"{path} is not valid JSON"


def test_versions_match_pyproject_everywhere():
    expected = _pyproject_version()
    server = _load(ROOT / "server.json")
    assert server["version"] == expected
    assert server["packages"][0]["version"] == expected
    assert _load(PLUGIN / "plugin.json")["version"] == expected
    assert _load(PLUGIN / ".claude-plugin/plugin.json")["version"] == expected
    market = _load(ROOT / ".claude-plugin/marketplace.json")
    entry = next(p for p in market["plugins"] if p["name"] == "evolution-api-mcp")
    assert entry["version"] == expected


def test_release_consistency_script_passes():
    done = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check-release-consistency.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr


def test_readme_carries_the_registry_name_comment_first():
    first_line = (ROOT / "README.md").read_text().splitlines()[0]
    assert first_line == f"<!-- mcp-name: {MCP_NAME} -->"


def test_server_json_identity_and_remote():
    server = _load(ROOT / "server.json")
    assert server["name"] == MCP_NAME
    assert server["repository"] == {"url": "https://github.com/singleflo/evolution-api-mcp", "source": "github"}
    assert len(server["description"]) <= MAX_REGISTRY_DESCRIPTION
    (package,) = server["packages"]
    assert package["registryType"] == "pypi"
    assert package["identifier"] == "evolution-api-mcp"
    assert package["transport"] == {"type": "stdio"}
    assert server["remotes"] == [{"type": "streamable-http", "url": f"{PUBLIC_URL}/mcp"}]


def test_server_json_advertises_exactly_the_variables_the_server_reads():
    assert set(_server_env()) == _config_env_names()


def test_server_json_marks_only_url_and_token_required_and_only_the_token_secret():
    env = _server_env()
    assert {name for name, entry in env.items() if entry["isRequired"]} == set(ENV_VARS)
    assert {name for name, entry in env.items() if entry["isSecret"]} == {config.ENV_TOKEN}
    for entry in env.values():
        assert entry["description"].strip()
        assert entry["format"] in {"string", "number", "boolean", "filepath"}


def test_server_json_documents_the_defaults_the_code_uses():
    env = _server_env()
    assert str(config.DEFAULT_DELAY_MS) in env[config.ENV_DELAY]["description"]
    assert f"0 to {config.MAX_DELAY_MS}" in env[config.ENV_DELAY]["description"]
    assert f"Default: {config.DEFAULT_MAX_WRITES_PER_MINUTE}." in env[config.ENV_WRITES]["description"]
    assert env[config.ENV_DELAY]["format"] == env[config.ENV_WRITES]["format"] == "number"


def test_server_json_deny_default_lists_exactly_the_policy_default_deny():
    description = _server_env()[config.ENV_DENY]["description"]
    listed = description.split("which is ", 1)[1].rstrip(".")
    assert set(listed.split(", ")) == policy.DEFAULT_DENY


def test_server_json_irreversible_tools_are_exactly_the_registry_irreversible_set():
    description = _server_env()[config.ENV_ALLOW_IRREVERSIBLE]["description"]
    listed = description.split("(", 1)[1].split(")", 1)[0]
    irreversible = {spec.name for spec in registry.specs() if spec.kind == "irreversible"}
    assert set(listed.split(", ")) == irreversible


def test_server_json_toolsets_description_names_every_toolset_and_the_default():
    description = _server_env()[config.ENV_TOOLSETS]["description"]
    for name in TOOLSET_ORDER:
        assert name in description
    assert "`core`" in description and "`all`" in description
    assert ", ".join(sorted(DEFAULT_TOOLSETS, key=TOOLSET_ORDER.index)) in description


def test_claude_mcp_json_camelcase_key_and_uvx_command():
    # openai/codex#22105: snake_case mcp_servers is silently ignored, so the
    # camelCase spelling is the contract: assert it literally.
    body = (PLUGIN / ".mcp.json").read_text()
    assert '"mcpServers"' in body
    assert "mcp_servers" not in body
    server = _load(PLUGIN / ".mcp.json")["mcpServers"]["evolution-api-mcp"]
    assert "type" not in server
    assert server["command"] == "uvx"
    assert server["args"] == ["evolution-api-mcp"]
    assert set(server["env"]) == set(ENV_VARS)
    for var in ENV_VARS:
        assert server["env"][var] == "${" + var + "}"


def test_portable_mcp_json_camelcase_key_and_uvx_command():
    body = (PLUGIN / "mcp.json").read_text()
    assert '"mcpServers"' in body
    assert "mcp_servers" not in body
    server = _load(PLUGIN / "mcp.json")["mcpServers"]["evolution-api-mcp"]
    assert server["type"] == "stdio"
    assert server["command"] == "uvx"
    assert server["args"] == ["evolution-api-mcp"]
    assert set(server["env"]) == set(ENV_VARS)
    for var in ENV_VARS:
        assert server["env"][var] == "${" + var + "}"


def test_plugin_env_variables_are_ones_the_server_reads():
    assert set(ENV_VARS) <= _config_env_names()


def test_no_credential_literals_in_shipped_manifests():
    # Evolution instance tokens are UUIDs; the global key and API keys are long hex or free text.
    uuid = re.compile(r"\b[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\b")
    hex40 = re.compile(r"\b[0-9a-f]{40}\b")
    scanned = [ROOT / "server.json"]
    for tree in ["plugins", ".claude-plugin", ".agents/plugins"]:
        scanned += [path for path in (ROOT / tree).rglob("*") if path.is_file() and path.suffix != ".png"]
    assert len(scanned) > 8
    for path in scanned:
        text = path.read_text(errors="ignore")
        assert not uuid.search(text), f"UUID literal in {path}"
        assert not hex40.search(text), f"40-hex literal in {path}"


def test_marketplace_entries_reference_existing_plugin_dir():
    claude = _load(ROOT / ".claude-plugin/marketplace.json")
    entry = next(p for p in claude["plugins"] if p["name"] == "evolution-api-mcp")
    assert (ROOT / entry["source"]).is_dir()
    assert entry["category"] == "productivity"

    agents = _load(ROOT / ".agents/plugins/marketplace.json")
    entry = next(p for p in agents["plugins"] if p["name"] == "evolution-api-mcp")
    assert entry["source"]["source"] == "local"
    assert (ROOT / entry["source"]["path"]).is_dir()
    assert entry["policy"] == {"installation": "AVAILABLE", "authentication": "ON_USE"}
    assert entry["category"] == "Productivity"


def test_claude_plugin_manifest_points_at_existing_mcp_json():
    manifest = _load(PLUGIN / ".claude-plugin/plugin.json")
    assert (PLUGIN / manifest["mcpServers"]).is_file()


def test_openai_interface_fields():
    manifest = _load(PLUGIN / "plugin.json")
    interface = manifest["extensions"]["com.openai"]["interface"]
    assert interface["displayName"] == "Evolution API Assistant"
    assert len(interface["shortDescription"]) <= MAX_OPENAI_SHORT_DESCRIPTION
    assert interface["privacyPolicyURL"] == f"{PUBLIC_URL}/privacy"
    assert interface["termsOfServiceURL"] == f"{PUBLIC_URL}/terms"
    assert interface["websiteURL"] == "https://github.com/singleflo/evolution-api-mcp"


def test_icon_fields_point_at_real_512_square_pngs():
    interface = _load(PLUGIN / "plugin.json")["extensions"]["com.openai"]["interface"]
    for field in ("composerIcon", "logo"):
        target = PLUGIN / interface[field]
        assert target.is_file(), f"{field} points at {target}, which does not exist (run scripts/make_icon.py)"
        assert _png_size(target) == (512, 512)
