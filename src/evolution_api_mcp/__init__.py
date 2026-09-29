"""Evolution API Assistant: an MCP server for one Evolution API (WhatsApp) instance."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("evolution-api-mcp")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0+unknown"
