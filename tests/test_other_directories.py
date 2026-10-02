"""docs/listing/OTHER-DIRECTORIES.md: every confirmed directory carries the five fields a submitter needs."""

from __future__ import annotations

import re
from pathlib import Path

DOC_PATH = Path(__file__).resolve().parent.parent / "docs" / "listing" / "OTHER-DIRECTORIES.md"

REQUIRED_FIELDS = [
    "URL",
    "What to paste",
    "Transport",
    "Review required",
    "Cost",
]


def _read() -> str:
    assert DOC_PATH.is_file(), f"File {DOC_PATH} does not exist"
    return DOC_PATH.read_text(encoding="utf-8")


def test_other_directories_file_exists():
    assert DOC_PATH.is_file(), f"File {DOC_PATH} does not exist"


def test_other_directories_no_tbd():
    assert "TBD" not in _read(), "Found TBD placeholder in OTHER-DIRECTORIES.md"


def test_other_directories_sections_and_fields():
    sections: list[tuple[str, set[str]]] = []
    in_confirmed = False
    current_section: str | None = None
    current_fields: set[str] = set()

    for line in _read().splitlines():
        stripped = line.strip()
        if stripped == "## Confirmed directories":
            in_confirmed = True
            continue
        if stripped.startswith("## ") and in_confirmed:
            break  # reached the next top-level section, e.g. "## Dropped directories"

        if in_confirmed and stripped.startswith("### "):
            if current_section:
                sections.append((current_section, current_fields))
            current_section = stripped[4:].strip()
            current_fields = set()
        elif in_confirmed and current_section and stripped.startswith("- **"):
            match = re.match(r"-\s*\*\*([^*]+)\*\*:", stripped)
            if match:
                current_fields.add(match.group(1).strip())

    if current_section:
        sections.append((current_section, current_fields))

    assert sections, "No confirmed catalog sections found under '## Confirmed directories'"

    for name, fields in sections:
        for required in REQUIRED_FIELDS:
            assert required in fields, f"Section '{name}' missing field '{required}' (found: {sorted(fields)})"


def test_other_directories_name_this_project_and_no_other():
    text = _read()
    assert "io.github.singleflo/evolution-api-mcp" in text
    assert "EVOLUTION_API_URL" in text and "EVOLUTION_INSTANCE_TOKEN" in text
    assert "odoo" not in text.lower(), "a value carried over from the reference project was left in"
