"""The real stdio server as a host runs it: handshake, tool listing, error reporting, guide, stdout purity."""

from __future__ import annotations

import json
import os
import queue
import shlex
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from mcp_types.version import LATEST_HANDSHAKE_VERSION

from evolution_api_mcp import __version__, policy, registry
from evolution_api_mcp.toolsets import DEFAULT_TOOLSETS, TOOLSET_ORDER

ROOT = Path(__file__).resolve().parent.parent
REPLY_TIMEOUT = 120


def _environment(**extra: str) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("EVOLUTION_")}
    env.update({"EVOLUTION_API_URL": "", "EVOLUTION_INSTANCE_TOKEN": ""})
    env.update(extra)
    return env


class StdioSession:
    """`uv run evolution-api-mcp` behind `tee`, so the exact stdout bytes are captured next to the parsed replies."""

    def __init__(self, capture: Path, stderr_log: Path) -> None:
        self._stderr = stderr_log.open("w")
        self._proc = subprocess.Popen(
            ["sh", "-c", f"uv run evolution-api-mcp | tee {shlex.quote(str(capture))}"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr,
            text=True,
            cwd=ROOT,
            env=_environment(),
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
            line = self._replies.get(timeout=REPLY_TIMEOUT)
            assert line is not None, f"the server closed stdout before answering {method}"
            message = json.loads(line)
            if message.get("id") == request_id:
                return message

    def close(self) -> None:
        assert self._proc.stdin is not None
        self._proc.stdin.close()
        try:
            self._proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()
        self._stderr.close()


@pytest.fixture(scope="module")
def session_result(tmp_path_factory: pytest.TempPathFactory) -> dict:
    directory = tmp_path_factory.mktemp("stdio")
    capture = directory / "stdout.txt"
    session = StdioSession(capture, directory / "stderr.txt")
    try:
        initialize = session.request(
            1,
            "initialize",
            {
                "protocolVersion": LATEST_HANDSHAKE_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "protocol-test", "version": "0"},
            },
        )
        session.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        listing = session.request(2, "tools/list")
        call = session.request(3, "tools/call", {"name": "get_instance_status", "arguments": {}})
        guide = session.request(4, "resources/read", {"uri": "evolution://guide"})
    finally:
        session.close()
    return {
        "initialize": initialize,
        "listing": listing,
        "call": call,
        "guide": guide,
        "captured": capture.read_text(encoding="utf-8"),
        "stderr": (directory / "stderr.txt").read_text(encoding="utf-8"),
    }


def test_the_handshake_names_the_server_and_carries_the_instructions(session_result):
    result = session_result["initialize"]["result"]
    assert result["serverInfo"]["name"] == "evolution-api-mcp"
    assert result["serverInfo"]["title"] == "Evolution API Assistant"
    assert result["instructions"].startswith("Operates one Evolution API instance, i.e. one WhatsApp number.")


def test_unknown_credentials_list_the_default_toolsets_and_get_instance_status(session_result):
    listed = [tool["name"] for tool in session_result["listing"]["result"]["tools"]]

    # Blank credentials leave the integration unknown, so only toolset, kind and deny rules apply. Irreversible
    # tools need EVOLUTION_MCP_ALLOW_IRREVERSIBLE, which this session does not set.
    expected = [
        spec.name
        for spec in registry.specs()
        if (spec.toolset in DEFAULT_TOOLSETS or spec.universal)
        and spec.kind != "irreversible"
        and spec.name not in policy.DEFAULT_DENY
    ]
    assert listed == expected
    assert "get_instance_status" in listed
    assert "send_text_message" in listed
    assert "delete_message_for_everyone" not in listed
    assert "list_groups" not in listed


def test_listed_tools_carry_title_annotations_and_input_schema(session_result):
    for tool in session_result["listing"]["result"]["tools"]:
        assert tool["annotations"]["title"] == tool["title"]
        assert tool["annotations"]["openWorldHint"] is True
        assert isinstance(tool["annotations"]["readOnlyHint"], bool)
        assert isinstance(tool["annotations"]["destructiveHint"], bool)
        assert isinstance(tool["annotations"]["idempotentHint"], bool)
        assert tool["inputSchema"]["type"] == "object"


def test_a_call_without_credentials_is_a_tool_error_naming_them(session_result):
    result = session_result["call"]["result"]
    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert "Missing Evolution credentials" in text
    assert "EVOLUTION_API_URL" in text
    assert "EVOLUTION_INSTANCE_TOKEN" in text
    assert text.endswith("Nothing was sent.")


def test_the_guide_resource_is_served_as_markdown(session_result):
    (content,) = session_result["guide"]["result"]["contents"]
    assert content["uri"] == "evolution://guide"
    assert content["mimeType"] == "text/markdown"
    assert content["text"].startswith("# Evolution API Assistant guide")
    for heading in ("## Chat ids and phone numbers", "## Safety model", "## Errors"):
        assert heading in content["text"]


def test_stdout_carries_only_json_rpc(session_result):
    lines = [line for line in session_result["captured"].splitlines() if line.strip()]
    assert len(lines) >= 4
    for line in lines:
        message = json.loads(line)
        assert message["jsonrpc"] == "2.0"
    assert "Starting evolution-api-mcp" in session_result["stderr"]


def _run_cli(*args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "evolution_api_mcp.server", *args],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=_environment(**env),
        timeout=120,
        check=False,
    )


def test_list_tools_prints_the_catalog_without_credentials():
    done = _run_cli("--list-tools")

    assert done.returncode == 0
    catalog = json.loads(done.stdout)
    assert [entry["name"] for entry in catalog] == [spec.name for spec in registry.specs()]
    assert len(catalog) == 97
    assert set(catalog[0]) == {"name", "title", "toolset", "kind", "integrations", "local_only"}
    assert {entry["toolset"] for entry in catalog} == set(TOOLSET_ORDER)


def test_list_toolsets_prints_names_descriptions_counts_and_presets():
    done = _run_cli("--list-toolsets")

    assert done.returncode == 0
    listing = json.loads(done.stdout)
    assert [entry["name"] for entry in listing["toolsets"]] == list(TOOLSET_ORDER)
    assert sum(entry["tools"] for entry in listing["toolsets"]) == 97
    assert listing["presets"]["core"] == ["messaging", "chats", "contacts"]
    assert listing["presets"]["all"] == list(TOOLSET_ORDER)
    assert listing["default"] == ["messaging", "chats", "contacts"]


def test_version_prints_only_the_version():
    done = _run_cli("--version")

    assert done.returncode == 0
    assert done.stdout.strip() == f"evolution-api-mcp {__version__}"


def test_an_invalid_configuration_exits_with_code_2_and_a_message_on_stderr():
    done = _run_cli(EVOLUTION_MCP_TOOLSETS="bogus")

    assert done.returncode == 2
    assert done.stdout == ""
    assert "EVOLUTION_MCP_TOOLSETS" in done.stderr
    assert "bogus" in done.stderr
