"""MCP resources: the usage guide (`evolution://guide`) and the tested call sequences (`evolution://recipes`)."""

from __future__ import annotations

from importlib.resources import files

from mcp.server import MCPServer

GUIDE_URI = "evolution://guide"
RECIPES_URI = "evolution://recipes"


def _asset(name: str) -> str:
    """Read a packaged Markdown asset when the resource is read, not when it is registered."""
    return files("evolution_api_mcp").joinpath("assets", name).read_text(encoding="utf-8")


def _read_guide() -> str:
    return _asset("guide.md")


def _read_recipes() -> str:
    return _asset("recipes.md")


def register(mcp: MCPServer) -> None:
    """Mount the guide and recipes resources."""
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
    mcp.resource(
        RECIPES_URI,
        name="evolution-recipes",
        title="Evolution API Assistant recipes",
        description=(
            "Tested call sequences for common WhatsApp tasks: what is new, who wrote, finding attachments, "
            "replying, forwarding and exporting."
        ),
        mime_type="text/markdown",
    )(_read_recipes)
