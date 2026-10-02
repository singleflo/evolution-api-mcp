"""The two portal guides cannot drift from the dossier or from the live hosted surface."""

from __future__ import annotations

import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from evolution_api_mcp import context, policy, registry, tools
from evolution_api_mcp.toolsets import TOOLSET_ORDER

ROOT = Path(__file__).resolve().parent.parent
DOSSIER_PATH = ROOT / "docs" / "listing" / "README.md"
SUBMIT_CLAUDE_PATH = ROOT / "docs" / "listing" / "SUBMIT-CLAUDE.md"
SUBMIT_OPENAI_PATH = ROOT / "docs" / "listing" / "SUBMIT-OPENAI.md"

# Dossier sections each store asks about directly: EVERY guide must carry
# them, checked per guide — concatenating the guides would let one guide
# cover for the other and hide an unfinished submission.
PER_GUIDE_SECTIONS = ("Country availability",)

# The hosted server is not deployed yet. A DNS failure for it is the
# expected state until deploy: attempted on every run, recorded as
# EXPECTED-PENDING-DEPLOY, never silently skipped.
EXPECTED_PENDING_HOSTS = ("evolution-mcp.singleflo.com",)

# The public repository does not exist until the owner creates and pushes it;
# a 404 for these addresses is the expected state until then.
EXPECTED_PENDING_PREFIXES = ("https://github.com/singleflo/evolution-api-mcp",)


def _guide_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def test_submit_guides_exist():
    assert SUBMIT_CLAUDE_PATH.is_file(), f"Missing {SUBMIT_CLAUDE_PATH}"
    assert SUBMIT_OPENAI_PATH.is_file(), f"Missing {SUBMIT_OPENAI_PATH}"


def test_dossier_section_coverage():
    dossier_text = DOSSIER_PATH.read_text(encoding="utf-8")
    h2_sections = re.findall(r"^##\s+(.+)$", dossier_text, re.MULTILINE)
    assert h2_sections, "No ## sections found in dossier"

    # Per-guide check for the sections both stores ask about.
    missing_per_guide = []
    for section in PER_GUIDE_SECTIONS:
        assert section in h2_sections, f"{section!r} is not a dossier ## section"
        for path in (SUBMIT_CLAUDE_PATH, SUBMIT_OPENAI_PATH):
            if not re.search(re.escape(section), _guide_text(path), re.IGNORECASE):
                missing_per_guide.append(f"{path} misses {section!r}")
    assert not missing_per_guide, f"Store-relevant dossier sections missing from guides: {missing_per_guide}"

    # Genuinely shared sections: each must be referenced in at least one guide.
    combined_guides = _guide_text(SUBMIT_CLAUDE_PATH) + "\n" + _guide_text(SUBMIT_OPENAI_PATH)

    unreferenced = [
        section for section in h2_sections if not re.search(re.escape(section), combined_guides, re.IGNORECASE)
    ]
    assert not unreferenced, f"The following dossier sections are not referenced in any guide: {unreferenced}"


def test_guides_urls_liveness():
    combined_text = _guide_text(SUBMIT_CLAUDE_PATH) + "\n" + _guide_text(SUBMIT_OPENAI_PATH)

    # Match URLs stopping at space, closing paren/bracket, or trailing backtick
    raw_urls = set(re.findall(r"https?://[^\s\)>\]\",`]+", combined_text))
    urls = {url.rstrip("`.,;") for url in raw_urls}
    assert urls, "No URLs found in submission guides"

    failed_urls: list[str] = []
    pending: list[str] = []
    for url in sorted(urls):
        host = urllib.parse.urlparse(url).netloc
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Python submission guide validator)"})
        # A push to main starts CI and the Coolify rebuild in parallel, and Traefik answers 503 while the
        # container is swapped, so for OUR host a transient 5xx is a deploy window to wait out, bounded; every
        # other host is checked strictly. A connection that never gets an answer is retried, backing off.
        attempts = 9 if host in EXPECTED_PENDING_HOSTS else 5
        for attempt in range(attempts):
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    code = resp.getcode()
                if code not in (200, 301, 302, 303, 307, 308):
                    failed_urls.append(f"{url} -> status {code}")
                break
            except urllib.error.HTTPError as e:
                # 403 / 401 login-gated URLs are accepted for portals (claude.ai, platform.openai.com).
                if e.code in (403, 401):
                    break
                # The OpenAI domain-challenge route 404s BY DESIGN until the operator pastes the token OpenAI
                # issues at submission time (EVOLUTION_REMOTE_OPENAI_CHALLENGE in the host's environment).
                if e.code == 404 and url.rstrip("/").endswith("/.well-known/openai-apps-challenge"):
                    pending.append(
                        f"{url} -> 404 — EXPECTED-UNTIL-CONFIGURED: EVOLUTION_REMOTE_OPENAI_CHALLENGE is unset; "
                        "it gets its value when OpenAI issues the challenge token"
                    )
                    break
                # The consent page answers 400 to a request carrying no pending authorisation, which is the
                # point of it: the pending id is handed only to the browser that came through /authorize.
                if e.code == 400 and url.rstrip("/").endswith("/consent"):
                    break
                # The repository is created by the owner after the plan's last wave.
                if e.code == 404 and url.startswith(EXPECTED_PENDING_PREFIXES):
                    pending.append(f"{url} -> 404 — EXPECTED-PENDING-PUBLISH: the public repository is not pushed yet")
                    break
                if e.code in (502, 503, 504) and attempt + 1 < attempts:
                    time.sleep(15)
                    continue
                failed_urls.append(f"{url} -> HTTPError {e.code}")
                break
            except Exception as e:
                # Every way a not-yet-deployed host can fail BEFORE answering HTTP counts as pending for OUR
                # host (DNS failure, or Traefik's default certificate failing verification). Any other host is
                # checked strictly. A typo in one of our paths still fails once the host is live, because that
                # arrives as an HTTP status and never reaches this branch.
                no_such_host = isinstance(getattr(e, "reason", None), socket.gaierror)
                if attempt + 1 < min(attempts, 5) and not (no_such_host and host in EXPECTED_PENDING_HOSTS):
                    time.sleep(3 * (attempt + 1))
                    continue
                if host in EXPECTED_PENDING_HOSTS:
                    pending.append(
                        f"{url} -> {type(e).__name__} ({getattr(e, 'reason', e)}) "
                        "— EXPECTED-PENDING-DEPLOY: the hosted server is not live yet"
                    )
                else:
                    failed_urls.append(f"{url} -> Exception {e}")
                break

    assert not failed_urls, f"URL liveness check failed for: {failed_urls}" + (
        f" | EXPECTED-PENDING (not failures): {pending}" if pending else ""
    )
    if pending:
        print("EXPECTED-PENDING (not failures):", pending)


# ------------------------------------------------------------ the tool count
# Both guides tell the submitter how many tools the portal will find, and one of them lists every name. The
# dossier is compared against the live server by test_listing_copy; these guides must be compared against the
# same surface: what a hosted WHATSAPP-BUSINESS connection with policy standard and every toolset ticked lists.
def _live_tool_names() -> list[str]:
    conn = context.Connection(
        mode="hosted",
        policy="standard",
        toolsets=frozenset(TOOLSET_ORDER),
        allow=None,
        deny=policy.DEFAULT_DENY,
        deny_is_default=True,
        irreversible_granted=False,
        identity=context.InstanceIdentity(name="inst", integration=registry.BUSINESS),
        subject="t_guides",
        default_delay_ms=1200,
        max_writes_per_minute=30,
        max_reads_per_minute=120,
    )
    tools.load_all()
    return [spec.name for spec in registry.specs() if policy.visible(spec, conn)]


def test_every_tool_count_in_the_guides_matches_the_live_surface():
    """A submitter reading "38 tools" and seeing another number in the portal cannot tell which side is wrong.
    The count in the guides is the server's, or it is a lie that survives to the review."""
    expected = len(_live_tool_names())

    wrong = []
    stated = 0
    for path in (SUBMIT_CLAUDE_PATH, SUBMIT_OPENAI_PATH):
        for count in re.findall(r"(\d+)\s+tools\b", _guide_text(path)):
            stated += 1
            if int(count) != expected:
                wrong.append(f"{path}: says {count} tools, the hosted surface lists {expected}")
    assert stated, "neither guide states a tool count"
    assert not wrong, wrong


def test_the_openai_guide_lists_every_tool_by_name():
    """The MCP tab's snapshot is the reviewed contract, so the guide spells it out. A tool missing from that list
    is a tool nobody checked."""
    names = _live_tool_names()
    text = _guide_text(SUBMIT_OPENAI_PATH)

    missing = [name for name in names if f"`{name}`" not in text]
    assert not missing, f"{SUBMIT_OPENAI_PATH} never names: {missing}"


def test_the_openai_guide_names_no_tool_outside_the_live_surface():
    live = set(_live_tool_names())
    guide = _guide_text(SUBMIT_OPENAI_PATH)
    snapshot = re.search(r"Snapshot contents: \d+ tools \((.*?)\), along with", guide, re.DOTALL)
    assert snapshot, "the MCP tab's snapshot list is missing"
    listed = re.findall(r"`([a-z_]+)`", snapshot.group(1))
    assert len(listed) == len(set(listed)), "a tool is listed twice in the snapshot"
    assert set(listed) == live


# ------------------------------------------------------------ prompts
# A prompt in the OpenAI guide and the same prompt in the dossier are one fact typed twice.
def _dossier_prompts() -> tuple[list[str], list[str]]:
    text = DOSSIER_PATH.read_text(encoding="utf-8")
    starters = re.search(r"^## Starter prompts\n(.*?)^## ", text, re.DOTALL | re.MULTILINE)
    assert starters
    starter_prompts = [_squash(block) for block in re.findall(r"```text\n(.*?)```", starters.group(1), re.DOTALL)]
    cases = [_squash(prompt) for prompt in re.findall(r"^- Prompt:(.*?)(?=\n- Expected tool:)", text, re.DOTALL | re.M)]
    return starter_prompts, cases


def test_every_prompt_of_the_dossier_appears_verbatim_in_the_openai_guide():
    starters, cases = _dossier_prompts()
    assert len(starters) == 3
    assert len(cases) == 8
    guide = _squash(_guide_text(SUBMIT_OPENAI_PATH))
    missing = [prompt for prompt in [*starters, *cases] if prompt not in guide]
    assert not missing, f"prompts in the dossier but not in {SUBMIT_OPENAI_PATH}: {missing}"
