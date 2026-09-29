"""The one way a tool talks to Evolution and turns bad input into a tool error.

Every tool module uses these helpers so failures read the same everywhere: reads report "Nothing was changed",
writes that may have been applied report UNCERTAIN together with a re-read of the state.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Literal

from evolution_api_mcp import jid
from evolution_api_mcp.client import EvolutionClient, EvolutionError, EvolutionHTTPError
from evolution_api_mcp.context import InstanceIdentity, instance_path
from evolution_api_mcp.errors import ToolExecutionError, raise_evolution_failure


async def call(
    client: EvolutionClient,
    identity: InstanceIdentity,
    method: Literal["GET", "POST", "PUT", "DELETE"],
    endpoint: str,
    *segments: str,
    json: object | None = None,
    params: Mapping[str, str] | None = None,
    timeout: float | None = None,
    write: bool = False,
    reread: Callable[[], Awaitable[object]] | None = None,
    on_http_error: Callable[[EvolutionHTTPError], None] | None = None,
) -> object:
    """Call `endpoint` on the identity's instance (`/<endpoint>/<segments…>/<instance>`) and return the parsed body.

    `write=True` marks a call that may have been applied even when it fails, so a failure without a clean refusal is
    reported as UNCERTAIN (with `reread`'s result when given). `on_http_error` sees an `EvolutionHTTPError` first and
    may raise a more specific `ToolExecutionError`; when it returns, the standard failure handling runs.
    """
    path = instance_path(identity, endpoint, *segments)
    try:
        return await client.request(method, path, json=json, params=params, timeout=timeout)
    except EvolutionError as exc:
        if on_http_error is not None and isinstance(exc, EvolutionHTTPError):
            on_http_error(exc)
        await raise_evolution_failure(
            exc,
            phase="after_mutation_possible" if write else "before_mutation",
            reread=reread if write else None,
        )
        raise  # unreachable: raise_evolution_failure always raises


def chat(value: str) -> str:
    """A phone number or chat id as a JID; bad input becomes a tool error."""
    try:
        return jid.normalize_chat(value)
    except ValueError as exc:
        raise ToolExecutionError(str(exc)) from exc


def group(value: str) -> str:
    """A group id or its digits as a `…@g.us` JID; bad input becomes a tool error."""
    try:
        return jid.normalize_group(value)
    except ValueError as exc:
        raise ToolExecutionError(str(exc)) from exc


def phone(value: str) -> str:
    """The digits of a phone number or person chat id; groups and @lid ids are refused."""
    digits = jid.phone_of(chat(value))
    if digits is None:
        raise ToolExecutionError(f"{value} is not a phone number; this tool takes international phone numbers.")
    return digits


def invite_code(value: str) -> str:
    """A group invite code from a bare code or a chat.whatsapp.com link."""
    try:
        return jid.parse_invite(value)
    except ValueError as exc:
        raise ToolExecutionError(str(exc)) from exc
