"""MCP resources: the usage guide, served as `evolution://guide`."""

from __future__ import annotations

from importlib.resources import files

from mcp.server import MCPServer

GUIDE_URI = "evolution://guide"


def _read_guide() -> str:
    """Read the guide when the resource is read, not when it is registered."""
    return files("evolution_api_mcp").joinpath("assets", "guide.md").read_text(encoding="utf-8")


def register(mcp: MCPServer) -> None:
    """Mount the guide resource."""
    mcp.resource(
        GUIDE_URI,
        name="evolution-guide",
        title="Evolution API Assistant guide",
        description=(
            "Chat ids, what works on each integration, where history comes from, pacing and WhatsApp rules, "
            "toolsets and the safety model."
        ),
        mime_type="text/markdown",
    )(_read_guide)
