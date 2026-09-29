"""tools/list filtering: a connection is shown exactly the tools it may call (D11)."""

from __future__ import annotations

from typing import Any

from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from pydantic import BaseModel

from evolution_api_mcp import context, policy, registry
from evolution_api_mcp.context import Connection


def filter_tools_result(result: dict, conn: Connection) -> dict:
    """Drop from a serialized tools/list result every tool `conn` may not call; order is preserved."""
    return {**result, "tools": [t for t in result["tools"] if policy.visible(registry.get(t["name"]), conn)]}


class VisibilityMiddleware:
    """Server middleware that filters `tools/list` with the same rules the call gate applies.

    The local server caches the visible names for the process lifetime, but only once identity resolution has
    settled (integration known, credentials missing, or a cached refusal), so the listing never freezes without
    the integration rule and never changes as a side effect of other requests. The hosted server recomputes per
    request from the authorization bound to it, which the spec allows.
    """

    def __init__(self) -> None:
        self._local_names: frozenset[str] | None = None

    async def __call__(self, ctx: ServerRequestContext[Any, Any], call_next: CallNext) -> HandlerResult:
        result = await call_next(ctx)
        if ctx.method != "tools/list":
            return result
        if isinstance(result, BaseModel):
            result = result.model_dump(by_alias=True, mode="json", exclude_none=True)
        if not isinstance(result, dict):
            return result

        names = self._local_names
        if names is None:
            settled = context.listing_settled()
            conn = await context.connection_for_listing()
            if conn.mode == "hosted":
                return filter_tools_result(result, conn)
            names = frozenset(spec.name for spec in registry.specs() if policy.visible(spec, conn))
            if settled:
                self._local_names = names
        return {**result, "tools": [t for t in result["tools"] if t["name"] in names]}
