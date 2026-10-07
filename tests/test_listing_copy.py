"""The listing dossier cannot drift: sizes, sections and live annotations.

docs/listing/README.md is the file a human pastes from at submission time.
The stores enforce hard limits (Claude: tagline 55, description 2000; OpenAI:
prompts 128, display name 30, short description 30) and require the
annotation values to match each tool's real behaviour. These tests parse the
dossier and fail on a size violation, a missing section, or an annotation
table row that no longer equals what `tools/list` returns on the wire for the
connection the stores scan: a hosted WHATSAPP-BUSINESS instance, policy
`standard`, every toolset ticked.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from mcp import Client

from evolution_api_mcp import context, policy, registry
from evolution_api_mcp.server import build_server
from evolution_api_mcp.toolsets import TOOLSET_ORDER

ROOT = Path(__file__).resolve().parent.parent
LISTING = ROOT / "docs" / "listing" / "README.md"

API_KEY_PATTERN = re.compile(r"\b[0-9a-f]{40}\b")
UUID_PATTERN = re.compile(r"\b[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\b")
CASE_LABELS = ("Prompt:", "Expected tool:", "Expected result:", "Fixture data:")
NON_AFFILIATION = (
    "Evolution API Assistant is an independent project by Persevida SL, "
    "not affiliated with or endorsed by Meta, WhatsApp or the Evolution API project."
)
SECRET_SCAN_DIRS = ("docs/listing", "plugins", ".claude-plugin", ".agents/plugins")


def _blocks_by_heading(text: str) -> dict[str, list[str]]:
    """Every fenced ``` block, attributed to the nearest preceding heading."""
    blocks: dict[str, list[str]] = {}
    heading = ""
    inside = False
    current: list[str] = []
    for line in text.splitlines():
        if line.startswith("```"):
            if inside:
                blocks.setdefault(heading, []).append("\n".join(current).strip())
                current = []
            inside = not inside
            continue
        if inside:
            current.append(line)
        elif line.startswith("#"):
            heading = line.lstrip("#").strip()
    return blocks


def _first(blocks: dict[str, list[str]], heading: str) -> str:
    assert heading in blocks, f"no fenced block under the {heading!r} heading"
    return blocks[heading][0]


def _level(heading_line: str) -> int:
    return len(heading_line) - len(heading_line.lstrip("#"))


def _sections(text: str, pattern: str) -> list[str]:
    """Each heading matching `pattern` with its body, up to the next heading of the same or a higher level."""
    lines = text.splitlines()
    sections = []
    for start, line in enumerate(lines):
        if not re.match(pattern, line):
            continue
        level = _level(line)
        stop = next(
            (i for i in range(start + 1, len(lines)) if lines[i].startswith("#") and _level(lines[i]) <= level),
            len(lines),
        )
        sections.append("\n".join(lines[start:stop]))
    return sections


def _check_copy(text: str) -> None:
    """Every paste-ready field exists and fits the store's published limit."""
    blocks = _blocks_by_heading(text)

    name = _first(blocks, "Name")
    assert 0 < len(name) <= 100, f"name is {len(name)} chars, limit 100"
    plugin_name = _first(blocks, "Plugin name")
    assert 0 < len(plugin_name) <= 64, f"plugin name is {len(plugin_name)} chars, limit 64"
    display_name = _first(blocks, "Display name")
    assert 0 < len(display_name) <= 30, f"display name is {len(display_name)} chars, limit 30"
    tagline = _first(blocks, "Tagline")
    assert 0 < len(tagline) <= 55, f"tagline is {len(tagline)} chars, limit 55: {tagline!r}"
    short_description = _first(blocks, "Short description")
    assert 0 < len(short_description) <= 30, (
        f"short description is {len(short_description)} chars, limit 30: {short_description!r}"
    )
    long_description = _first(blocks, "Long description")
    assert 0 < len(long_description) <= 2000, f"long description is {len(long_description)} chars, limit 2000"


def _hosted_business_connection() -> context.Connection:
    """What the stores scan: hosted, WHATSAPP-BUSINESS, policy standard, every toolset enabled."""
    return context.Connection(
        mode="hosted",
        policy="standard",
        toolsets=frozenset(TOOLSET_ORDER),
        allow=None,
        deny=policy.DEFAULT_DENY,
        deny_is_default=True,
        irreversible_granted=False,
        identity=context.InstanceIdentity(name="inst", integration=registry.BUSINESS),
        subject="t_dossier",
        default_delay_ms=1200,
        max_writes_per_minute=30,
        max_reads_per_minute=120,
    )


def _visible_specs() -> list[registry.ToolSpec]:
    conn = _hosted_business_connection()
    return [spec for spec in registry.specs() if policy.visible(spec, conn)]


async def _wire_tools() -> list:
    with context.override_for_tests(_hosted_business_connection(), object()):  # type: ignore[arg-type]
        async with Client(build_server(mode="hosted")) as client:
            return list((await client.list_tools()).tools)


def _table_rows(text: str) -> dict[str, tuple[bool, bool, bool]]:
    section = _sections(text, r"^##+ Tool annotations")[0]
    rows = re.findall(r"^\| `([a-z_]+)` \| (yes|no) \| (yes|no) \| (yes|no) \|", section, re.MULTILINE)
    return {
        name: (read_only == "yes", destructive == "yes", open_world == "yes")
        for name, read_only, destructive, open_world in rows
    }


def test_paste_ready_copy_respects_the_store_limits():
    _check_copy(LISTING.read_text(encoding="utf-8"))


def test_the_checker_catches_a_drifted_tagline():
    """The QA probe: a 60-char tagline must fail, not pass silently."""
    text = LISTING.read_text(encoding="utf-8")
    drifted = text.replace(_blocks_by_heading(text)["Tagline"][0], "x" * 60, 1)
    with pytest.raises(AssertionError, match="tagline is 60 chars"):
        _check_copy(drifted)


def test_the_long_description_states_the_live_surface_and_the_non_affiliation_line():
    long_description = _first(_blocks_by_heading(LISTING.read_text(encoding="utf-8")), "Long description")
    visible = _visible_specs()
    match = re.search(r"(\d+) tools in (\d+) toolsets", long_description)
    assert match, "the long description must state '<n> tools in <m> toolsets'"
    assert int(match.group(1)) == len(visible)
    assert int(match.group(2)) == len({spec.toolset for spec in visible})
    assert long_description.endswith(NON_AFFILIATION)
    assert "WhatsApp" not in _first(_blocks_by_heading(LISTING.read_text(encoding="utf-8")), "Name")


def test_exactly_three_starter_prompts_within_128_chars():
    blocks = _blocks_by_heading(LISTING.read_text(encoding="utf-8"))
    prompts = blocks.get("Starter prompts", [])
    assert len(prompts) == 3, f"expected exactly 3 starter prompts, found {len(prompts)}"
    for prompt in prompts:
        assert 0 < len(prompt) <= 128, f"prompt is {len(prompt)} chars: {prompt!r}"
        assert "@" not in prompt, f"starter prompt contains an @mention: {prompt!r}"


def test_five_positive_and_three_negative_cases_with_all_fields():
    text = LISTING.read_text(encoding="utf-8")
    positives = _sections(text, r"^#+ Positive test case \d")
    negatives = _sections(text, r"^#+ Negative test case \d")
    assert len(positives) == 5, f"expected 5 positive test cases, found {len(positives)}"
    assert len(negatives) == 3, f"expected 3 negative test cases, found {len(negatives)}"
    for case in positives + negatives:
        for label in CASE_LABELS:
            assert label in case, f"test case missing {label!r}:\n{case[:300]}"
    for case in negatives:
        assert "Why not:" in case, f"negative case without its reason:\n{case[:300]}"


def test_test_cases_carry_no_placeholders_and_expect_real_hosted_tools():
    text = LISTING.read_text(encoding="utf-8")
    visible = {spec.name for spec in _visible_specs()}
    for case in _sections(text, r"^#+ Positive test case \d"):
        assert not re.search(r"<[^>\n]+>|TBD|TODO", case), f"placeholder inside a test case:\n{case[:300]}"
        expected = re.search(r"- Expected tool:(.*?)(?=\n- Expected result:)", case, re.DOTALL)
        assert expected, case[:200]
        names = re.findall(r"`([a-z_]+)`", expected.group(1))
        tool_names = [name for name in names if name in {spec.name for spec in registry.specs()}]
        assert tool_names, f"no registered tool named in the expected tools:\n{expected.group(1)}"
        assert set(tool_names) <= visible, f"expected tool not visible to the hosted Business surface: {tool_names}"
    for case in _sections(text, r"^#+ Negative test case \d"):
        assert not re.search(r"<[^>\n]+>|TBD|TODO", case), f"placeholder inside a test case:\n{case[:300]}"
        assert re.search(r"- Expected tool: none", case), f"a negative case must expect no tool:\n{case[:300]}"


@pytest.mark.anyio
async def test_annotation_table_matches_the_live_wire_annotations():
    table = _table_rows(LISTING.read_text(encoding="utf-8"))
    live = {}
    for tool in await _wire_tools():
        assert tool.annotations is not None, tool.name
        # Anthropic's directory reads the title from the annotations object, not from the tool's own title
        # field, and flags every tool whose annotations.title is empty — measured on the reference project:
        # 22 of 22 flagged on a real submission where Tool.title was set but annotations.title was not.
        assert tool.annotations.title, f"annotations.title missing on {tool.name}"
        assert tool.annotations.title == tool.title, tool.name
        live[tool.name] = (
            tool.annotations.read_only_hint,
            tool.annotations.destructive_hint,
            tool.annotations.open_world_hint,
        )
    assert len(live) == 41
    assert set(live) == {spec.name for spec in _visible_specs()}, "the wire listing and policy.visible disagree"
    assert set(table) == set(live), "table rows and live tools disagree on names"
    assert table == live, "the dossier's annotation table has drifted from the live annotations:\n" + "\n".join(
        f"  {name}: table={table.get(name)} live={live[name]}" for name in sorted(live) if table.get(name) != live[name]
    )


def test_the_annotation_rows_follow_the_registry_kinds_and_are_listed_by_toolset_then_name():
    text = LISTING.read_text(encoding="utf-8")
    section = _sections(text, r"^##+ Tool annotations")[0]
    listed = re.findall(r"^\| `([a-z_]+)` \|", section, re.MULTILINE)
    expected = [spec.name for spec in _visible_specs()]
    assert listed == expected
    assert "openWorldHint" in section and "`yes` throughout" in section


def test_urls_sections_and_no_real_credentials():
    text = LISTING.read_text(encoding="utf-8")
    blocks = _blocks_by_heading(text)
    assert _first(blocks, "Privacy URL") == "https://evolution-mcp.singleflo.com/privacy"
    assert _first(blocks, "Terms URL") == "https://evolution-mcp.singleflo.com/terms"
    assert _first(blocks, "Documentation URL").startswith("https://github.com/singleflo/evolution-api-mcp")
    assert _first(blocks, "Support URL").startswith("https://github.com/singleflo/evolution-api-mcp")
    assert "worldwide" in _first(blocks, "Country availability").lower()
    assert "Reviewer test account" in text, "the reviewer-account template is missing"
    assert "screenshots are not required" in text.lower()
    assert not API_KEY_PATTERN.search(text), "a 40-hex string that looks like a real API key sits in the dossier"
    assert not UUID_PATTERN.search(text), "a UUID (the shape of an Evolution instance token) sits in the dossier"


def test_every_tool_count_in_the_dossier_matches_the_live_surface():
    text = LISTING.read_text(encoding="utf-8")
    expected = len(_visible_specs())
    counts = re.findall(r"(\d+)\s+tools\b", text)
    assert counts, "the dossier states no tool count"
    assert {int(count) for count in counts} == {expected}


def test_no_credential_shaped_string_sits_in_the_listing_or_plugin_files():
    offenders: list[str] = []
    for directory in SECRET_SCAN_DIRS:
        base = ROOT / directory
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix in {".png", ".mov", ".mp4"}:
                continue
            content = path.read_text(encoding="utf-8", errors="ignore")
            if API_KEY_PATTERN.search(content) or UUID_PATTERN.search(content):
                offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"credential-shaped strings in: {offenders}"
