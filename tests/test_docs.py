"""The documentation states numbers and lists that come from the code; these tests keep the two in step."""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

from evolution_api_mcp import config, context, policy, registry
from evolution_api_mcp.toolsets import DEFAULT_TOOLSETS, TOOLSET_ORDER

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
SCRIPT = ROOT / "scripts" / "generate_docs.py"


def _table(header_prefix: str) -> list[list[str]]:
    """The body rows of the Markdown table whose header row starts with `header_prefix`, split into cells."""
    lines = README.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(header_prefix))
    rows: list[list[str]] = []
    for line in lines[start + 2 :]:
        if not line.startswith("|"):
            break
        rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
    return rows


def _generator():
    spec = importlib.util.spec_from_file_location("generate_docs", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _connection(**overrides: object) -> context.Connection:
    values: dict[str, object] = {
        "mode": "local",
        "policy": "standard",
        "toolsets": DEFAULT_TOOLSETS,
        "allow": None,
        "deny": policy.DEFAULT_DENY,
        "deny_is_default": True,
        "irreversible_granted": False,
        "identity": context.InstanceIdentity(name="inst", integration=registry.BAILEYS),
        "subject": None,
        "default_delay_ms": 1200,
        "max_writes_per_minute": 30,
        "max_reads_per_minute": 0,
    }
    values.update(overrides)
    return context.Connection(**values)  # type: ignore[arg-type]


def _hosted_business(**overrides: object) -> context.Connection:
    return _connection(
        mode="hosted",
        toolsets=frozenset(TOOLSET_ORDER),
        identity=context.InstanceIdentity(name="inst", integration=registry.BUSINESS),
        subject="t_docs",
        max_reads_per_minute=120,
        **overrides,
    )


def _visible_count(connection: context.Connection) -> int:
    return sum(1 for spec in registry.specs() if policy.visible(spec, connection))


def test_the_tools_document_is_generated_from_the_registry():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"], capture_output=True, text=True, cwd=ROOT, check=False
    )

    assert result.returncode == 0, result.stderr


def test_check_fails_when_the_document_drifts_and_passes_when_it_matches(tmp_path, monkeypatch):
    generator = _generator()
    document = tmp_path / "TOOLS.md"
    monkeypatch.setattr(generator, "OUTPUT", document)

    assert generator.main(["--check"]) == 1
    document.write_text(generator.render(), encoding="utf-8")
    assert generator.main(["--check"]) == 0
    document.write_text(generator.render() + "stale line\n", encoding="utf-8")
    assert generator.main(["--check"]) == 1


def test_the_generated_document_lists_every_tool_once_per_toolset_table():
    text = _generator().render()

    for spec in registry.specs():
        assert text.count(f"| `{spec.name}` |") == 1, spec.name


def test_the_readme_toolset_table_counts_equal_the_registry():
    rows = _table("| Toolset | Tools | Covers |")
    stated = {row[0].strip("`"): int(row[1]) for row in rows}

    expected = {name: sum(1 for spec in registry.specs() if spec.toolset == name) for name in TOOLSET_ORDER}
    assert stated == expected
    assert list(stated) == list(TOOLSET_ORDER)


def test_the_readme_integration_matrix_equals_the_registry():
    rows = _table("| Toolset | Tools | Baileys | Business | Evolution |")
    integrations = (registry.BAILEYS, registry.BUSINESS, registry.EVOLUTION)
    specs = registry.specs()

    for row in rows:
        label = row[0].strip("`*")
        members = specs if label == "All" else [spec for spec in specs if spec.toolset == label]
        expected = [len(members)] + [sum(1 for spec in members if name in spec.integrations) for name in integrations]
        assert [int(cell.strip("*")) for cell in row[1:]] == expected, label
    assert [row[0].strip("`*") for row in rows] == [*TOOLSET_ORDER, "All"]


def test_the_readme_visibility_table_equals_what_the_policy_shows():
    everything = frozenset(TOOLSET_ORDER)
    expected = {
        "Local, WhatsApp Web (Baileys) instance, default toolsets and settings": _connection(),
        "Local, Baileys instance, all toolsets": _connection(toolsets=everything),
        "Local, Baileys instance, all toolsets, `EVOLUTION_MCP_ALLOW_IRREVERSIBLE=yes`": _connection(
            toolsets=everything, irreversible_granted=True
        ),
        "Local, Baileys instance, all toolsets, read-only": _connection(toolsets=everything, policy="read"),
        "Hosted, WhatsApp Business Platform instance, standard policy, all toolsets": _hosted_business(),
        "Hosted, WhatsApp Business Platform instance, read policy, all toolsets": _hosted_business(policy="read"),
    }

    rows = _table("| Connection | Tools visible |")

    assert {row[0]: int(row[1]) for row in rows} == {
        label: _visible_count(connection) for label, connection in expected.items()
    }


def test_the_readme_prose_counts_match_the_registry():
    specs = registry.specs()
    local_only = sorted(spec.name for spec in specs if spec.local_only)
    irreversible = [spec for spec in specs if spec.kind == "irreversible"]

    assert f"{len(specs)} tools, one per operation" in README
    assert f"grouped in {len(TOOLSET_ORDER)} toolsets" in README
    assert f"`all` enables all {len(TOOLSET_ORDER)}" in README
    assert f"{len(local_only)} tools are registered only by the local server" in README
    assert f"The hosted server registers the other {len(specs) - len(local_only)}." in README
    assert f"The {len(irreversible)} irreversible tools run only when" in README
    assert f"the {len(policy.DEFAULT_DENY)} on the default deny list and the {len(irreversible)} irreversible" in README
    for name in local_only:
        assert f"`{name}`" in README.split("**Local-only tools.**")[1].split("**Pacing")[0], name
    for name in (spec.name for spec in irreversible):
        assert f"`{name}`" in README.split("## Tools and resources")[1].split("Resource:")[0], name


def test_the_readme_names_the_default_deny_list_and_every_setting():
    deny_paragraph = README.split("* **Deny list.**")[1].split("* **Allow list.**")[0]
    listed = set(re.findall(r"`([a-z_]+)`", deny_paragraph)) & {spec.name for spec in registry.specs()}

    assert listed == set(policy.DEFAULT_DENY)
    for variable in (
        config.ENV_URL,
        config.ENV_TOKEN,
        config.ENV_TOOLSETS,
        config.ENV_ALLOW,
        config.ENV_DENY,
        config.ENV_ALLOW_IRREVERSIBLE,
        config.ENV_DELAY,
        config.ENV_WRITES,
        config.ENV_FILE_ROOTS,
        config.ENV_DOWNLOAD_DIR,
        "EVOLUTION_MCP_DATA_DIR",
    ):
        assert f"`{variable}`" in README, variable


def test_the_readme_states_the_defaults_the_config_applies():
    assert f"| `EVOLUTION_MCP_DEFAULT_DELAY_MS` | `{config.DEFAULT_DELAY_MS}` |" in README
    assert f"| `EVOLUTION_MCP_MAX_WRITES_PER_MINUTE` | `{config.DEFAULT_MAX_WRITES_PER_MINUTE}` |" in README
    assert f"`0` to `{config.MAX_DELAY_MS}`" in README
    for folder in config.DEFAULT_ROOT_NAMES:
        assert f"`{folder}`" in README, folder
