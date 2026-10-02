"""The installed-artifact suite: a real MCP session against the BUILT WHEEL.

`tests/test_protocol.py` proves the protocol against the dev checkout (`uv run evolution-api-mcp`). That is not the
thing a user installs. This module spawns the console script out of `dist/*.whl` through `uvx`, exactly as the
README's host configs do, and drives `initialize` -> `tools/list` -> `tools/call` -> `resources/read` over real stdio.

It exists because a clean exit proves nothing: `printf '' | uvx --from dist/*.whl evolution-api-mcp` exits 0 because
EOF on stdin closes the session BEFORE any tool runs. Only an actual `tools/call` forces credential resolution, only
`tools/list` proves every `tools/*.py` module made it into the wheel, and only `resources/read` proves
`assets/guide.md` did.

No Evolution server is involved: the credential variables are blanked, so `get_instance_status` fails before any
request is sent, and that failure is the vehicle.

Marked `wheel` and excluded from the default run: it needs `uv build` output on disk and network for `uvx` to
resolve dependencies. CI builds the wheel first (`.github/workflows/tests.yml`).
"""

from __future__ import annotations

import json
import os
import queue
import shlex
import subprocess
import threading
from pathlib import Path
from typing import NamedTuple

import pytest
from mcp_types.version import LATEST_HANDSHAKE_VERSION

from evolution_api_mcp import policy, registry
from evolution_api_mcp.toolsets import DEFAULT_TOOLSETS

pytestmark = pytest.mark.wheel

ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT / "dist"
REPLY_TIMEOUT_SECONDS = 180

REQUIRED_CREDENTIALS = {"EVOLUTION_API_URL", "EVOLUTION_INSTANCE_TOKEN"}
# Blanked rather than absent: blank counts as unset, and no inherited variable can reach a real server.
NO_CREDENTIALS = {"EVOLUTION_API_URL": "", "EVOLUTION_INSTANCE_TOKEN": ""}
A_TOKEN_IS_SET = {**NO_CREDENTIALS, "EVOLUTION_INSTANCE_TOKEN": "dummy-token-never-sent-anywhere"}

# The anchor the blame list is parsed from: see `_blamed()`.
BLAME_PREFIX = "Missing Evolution credentials: "


class WheelSession(NamedTuple):
    """One session against the artifact: the parsed replies and the raw stdout lines."""

    listing: dict
    call: dict
    guide: dict
    stdout_lines: list[str]


@pytest.fixture(scope="module")
def wheel() -> Path:
    """The artifact under test. Absent means the operator skipped `uv build`."""
    built = sorted(DIST_DIR.glob("evolution_api_mcp-*.whl"), key=lambda p: p.stat().st_mtime)
    if not built:
        pytest.fail(f"No wheel in {DIST_DIR}. Run `uv build`: this suite tests the artifact.")
    return built[-1]


def _launch_command(wheel: Path, capture: Path) -> str:
    """The wheel's console script behind `tee`, so the raw stdout bytes are kept next to the parsed replies.

    `--refresh-package` is load-bearing, not caution: uvx keys its cached environment on name==version, so a rebuilt
    wheel with the same version is silently ignored and the session would run the PREVIOUS build.
    """
    launch = f"uvx --refresh-package evolution-api-mcp --from {shlex.quote(str(wheel))} evolution-api-mcp"
    return f"{launch} | tee {shlex.quote(str(capture))}"


def _environment(credentials: dict[str, str]) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("EVOLUTION_")}
    env.update(credentials)
    return env


class _Stdio:
    def __init__(self, wheel: Path, workdir: Path, credentials: dict[str, str]) -> None:
        self._capture = workdir / "stdout.jsonl"
        self._stderr = (workdir / "stderr.log").open("w", encoding="utf-8")
        self._proc = subprocess.Popen(
            ["sh", "-c", _launch_command(wheel, self._capture)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr,
            text=True,
            env=_environment(credentials),
        )
        self._replies: queue.Queue[str | None] = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            self._replies.put(line)
        self._replies.put(None)

    def send(self, message: dict) -> None:
        assert self._proc.stdin is not None
        self._proc.stdin.write(json.dumps(message) + "\n")
        self._proc.stdin.flush()

    def request(self, request_id: int, method: str, params: dict | None = None) -> dict:
        self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
        while True:
            line = self._replies.get(timeout=REPLY_TIMEOUT_SECONDS)
            assert line is not None, f"the installed server closed stdout before answering {method}"
            message = json.loads(line)
            if message.get("id") == request_id:
                return message

    def close(self) -> list[str]:
        assert self._proc.stdin is not None
        self._proc.stdin.close()
        try:
            self._proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()
        self._stderr.close()
        return [line for line in self._capture.read_text(encoding="utf-8").splitlines() if line.strip()]


def _run_session(wheel: Path, workdir: Path, credentials: dict[str, str]) -> WheelSession:
    """Drive the one call that forces the installed server to resolve credentials."""
    stdio = _Stdio(wheel, workdir, credentials)
    try:
        stdio.request(
            1,
            "initialize",
            {
                "protocolVersion": LATEST_HANDSHAKE_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "wheel-e2e", "version": "0"},
            },
        )
        stdio.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        listing = stdio.request(2, "tools/list")
        call = stdio.request(3, "tools/call", {"name": "get_instance_status", "arguments": {}})
        guide = stdio.request(4, "resources/read", {"uri": "evolution://guide"})
    finally:
        lines = stdio.close()
    return WheelSession(listing=listing, call=call, guide=guide, stdout_lines=lines)


@pytest.fixture(scope="module")
def uncredentialed(wheel: Path, tmp_path_factory: pytest.TempPathFactory) -> WheelSession:
    """The claim under test: a tool call with nothing configured."""
    return _run_session(wheel, tmp_path_factory.mktemp("wheel-none"), NO_CREDENTIALS)


@pytest.fixture(scope="module")
def with_a_token(wheel: Path, tmp_path_factory: pytest.TempPathFactory) -> WheelSession:
    """The control: same call, but the token IS supplied."""
    return _run_session(wheel, tmp_path_factory.mktemp("wheel-token"), A_TOKEN_IS_SET)


def _text(session: WheelSession) -> str:
    return session.call["result"]["content"][0]["text"]


def _blamed(text: str) -> set[str]:
    """The variables the installed server itself computed as missing.

    Parsed, never substring-matched: only this list is derived from the environment, so only this list can tell
    the two sessions apart (the closing sentences do not name the variables, but a substring test on the whole text
    would stop being load-bearing the day they do).
    """
    _, _, tail = text.partition(BLAME_PREFIX)
    listed, _, _ = tail.partition(".")
    return {name for name in listed.split(", ") if name}


def test_the_installed_wheel_reaches_the_credential_check(uncredentialed: WheelSession):
    """Given the built wheel, When a tool is called uncredentialed, Then both required variables are blamed."""
    assert uncredentialed.call["result"]["isError"] is True
    assert _blamed(_text(uncredentialed)) == REQUIRED_CREDENTIALS


def test_supplying_the_token_takes_it_off_the_blame_list(with_a_token: WheelSession):
    """Given a token IS set, When the same call runs, Then only the URL is blamed.

    This is what makes the test above load-bearing: both sessions fail, and only the blame list, computed from the
    environment by the installed code, differs.
    """
    assert with_a_token.call["result"]["isError"] is True
    assert _blamed(_text(with_a_token)) == REQUIRED_CREDENTIALS - {"EVOLUTION_INSTANCE_TOKEN"}


def test_the_wheel_ships_every_tool_module(uncredentialed: WheelSession):
    """Given blank credentials, When tools/list runs, Then the visible tools are exactly the core set."""
    listed = [tool["name"] for tool in uncredentialed.listing["result"]["tools"]]
    expected = [
        spec.name
        for spec in registry.specs()
        if (spec.toolset in DEFAULT_TOOLSETS or spec.universal)
        and spec.kind != "irreversible"
        and spec.name not in policy.DEFAULT_DENY
    ]
    assert listed == expected


def test_the_wheel_ships_the_guide_resource(uncredentialed: WheelSession):
    (content,) = uncredentialed.guide["result"]["contents"]
    assert content["uri"] == "evolution://guide"
    assert content["mimeType"] == "text/markdown"
    assert content["text"].startswith("# Evolution API Assistant guide")


def test_that_failure_reaches_the_wire_as_isError_from_the_artifact(uncredentialed: WheelSession):
    """Given the session, When stdout is read, Then isError:true is literally on it.

    The client-side assertion could hold while the SDK encoded the failure as a JSON-RPC `error` envelope, which a
    model never sees as tool output. This reads the bytes the installed server actually wrote, and parsing every
    one of them as JSON is also the stdout-purity gate for the artifact.
    """
    messages = [json.loads(line) for line in uncredentialed.stdout_lines]
    tool_results = [message["result"] for message in messages if "content" in message.get("result", {})]

    assert len(messages) >= 4
    assert {message["jsonrpc"] for message in messages} == {"2.0"}
    assert [message for message in messages if "error" in message] == []
    assert len(tool_results) == 1
    assert tool_results[0]["isError"] is True
