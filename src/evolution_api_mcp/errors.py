"""Tool result formatting and the Evolution failure -> isError mapping.

**How isError works in SDK 2.x**: the SDK forwards the text of `ToolError` (and only of
`ToolError`) to the client as a `CallToolResult(is_error=True)`; any other exception is treated
as a crash and its text stays on the server. So the whole mechanism is: raise
`ToolExecutionError` for a failure, return a string for a success. No custom JSON-RPC error
codes exist, by construction.

**Three outcomes** for a tool call:

* success: `tool_result(payload)` returns compact JSON text;
* refused or failed with nothing sent or changed: `ToolExecutionError` whose text says so;
* UNCERTAIN: a write that was sent but not confirmed (timeout, 5xx). The text carries the state
  as re-read from Evolution and tells the caller not to repeat the call before checking it.

Nothing in this module ever retries.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Literal, NoReturn

from mcp.server.mcpserver.exceptions import ToolError

from evolution_api_mcp.client import (
    EvolutionError,
    EvolutionHTTPError,
    EvolutionUncertain,
    EvolutionUnreachable,
)

MAX_RESULT_CHARS = 10_000
TRUNCATION_NOTICE = "\n… cut at 10000 characters: ask for fewer items (limit) or a narrower time range."

# Baileys wraps delivery timeouts and socket resets in HTTP 400; those say nothing about whether
# the message left, so they are handled like a 5xx.
UNCERTAIN_4XX_MARKERS = ("Timed Out", "timed out", "ETIMEDOUT", "ECONNRESET", "socket hang up")

# A Literal, not a bool: `phase="after_mutation_possible"` names the claim it licenses at the call
# site, where a bare `True` would name nothing.
Phase = Literal["before_mutation", "after_mutation_possible"]


class ToolExecutionError(ToolError):
    """Raised for every tool failure so the SDK returns isError:true with this exact message.

    It has to be a `ToolError` rather than a plain exception: SDK 2.1+ forwards only `ToolError`
    text to the client and hides the text of any other exception.
    """


def _dumps(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _trim_longest_list(payload: dict) -> str | None:
    """`payload` with its longest list cut to the items that fit, as valid JSON, or None when no cut fits.

    Cutting the text mid-way would hand the model a broken document; dropping trailing items and saying how many
    were kept keeps it parseable and tells it exactly what is missing.
    """
    candidates = [key for key, value in payload.items() if isinstance(value, list) and len(value) > 1]
    if not candidates:
        return None
    key = max(candidates, key=lambda k: len(_dumps(payload[k])))
    items: list = payload[key]

    def render(kept: int) -> str:
        notice = f"Showing {kept} of {len(items)} {key}; ask for fewer items (limit) or a narrower time range."
        return _dumps({**payload, key: items[:kept], "truncated": notice})

    low, high = 0, len(items) - 1
    while low < high:  # largest count whose rendering still fits
        middle = (low + high + 1) // 2
        if len(render(middle)) <= MAX_RESULT_CHARS:
            low = middle
        else:
            high = middle - 1
    return render(low) if low > 0 and len(render(low)) <= MAX_RESULT_CHARS else None


def tool_result(payload: object, *, notice: str = TRUNCATION_NOTICE) -> str:
    """Serialise `payload` as compact JSON and cap it at MAX_RESULT_CHARS.

    A `str` passes through unchanged. When the cap bites on a dict holding a list, the list is cut to the items
    that fit and the result says how many were kept. Anything else is cut at the cap and `notice` names the remedy
    the caller can apply.
    """
    text = payload if isinstance(payload, str) else _dumps(payload)
    if len(text) <= MAX_RESULT_CHARS:
        return text
    if isinstance(payload, dict) and (trimmed := _trim_longest_list(payload)) is not None:
        return trimmed
    return text[:MAX_RESULT_CHARS] + notice


def _sentence(message: str) -> str:
    """`message` without trailing periods, so it can be followed by our own full stop."""
    return message.rstrip(" .") or message


def _is_uncertain_http(exc: EvolutionHTTPError) -> bool:
    if exc.status >= 500:
        return True
    if exc.status == 401:
        return False
    return any(marker in exc.message for marker in UNCERTAIN_4XX_MARKERS)


async def _reread_state(reread: Callable[[], Awaitable[object]] | None) -> str:
    """The state as it was actually observed; never one that was not read."""
    if reread is None:
        return "not re-read"
    try:
        return json.dumps(await reread(), ensure_ascii=False, default=str)
    except Exception:  # a failed read proves nothing
        return "not re-read"


async def raise_evolution_failure(
    exc: EvolutionError,
    *,
    phase: Phase,
    reread: Callable[[], Awaitable[object]] | None = None,
) -> NoReturn:
    """Raise the `ToolExecutionError` that describes `exc`.

    `phase` is what the caller knows and this function cannot: whether a write had already been
    attempted. Only `before_mutation` may claim "Nothing was changed" for a failure whose outcome
    is in doubt. `reread` (called at most once, only for the UNCERTAIN text) returns the state to
    show.
    """
    if isinstance(exc, EvolutionUnreachable):
        raise ToolExecutionError(f"Could not reach Evolution at {exc.host}: {exc.reason}. Nothing was sent.") from exc

    if isinstance(exc, EvolutionHTTPError):
        if exc.status == 401:
            raise ToolExecutionError(
                "Evolution rejected the instance token (401 Unauthorized). Nothing was changed. "
                "Local server: update EVOLUTION_INSTANCE_TOKEN. "
                "Hosted: disconnect and reconnect with the instance's current token."
            ) from exc
        if not _is_uncertain_http(exc):
            raise ToolExecutionError(
                f"Evolution refused the request ({exc.status}): {_sentence(exc.message)}. Nothing was changed."
            ) from exc
        detail, message = f"HTTP {exc.status}", exc.message
    elif isinstance(exc, EvolutionUncertain):
        detail, message = f"no answer from {exc.host}", exc.reason
    else:
        detail, message = type(exc).__name__, str(exc)

    if phase == "before_mutation":
        raise ToolExecutionError(f"Evolution failed ({detail}): {_sentence(message)}. Nothing was changed.") from exc
    state = await _reread_state(reread)
    raise ToolExecutionError(
        f"UNCERTAIN: Evolution did not confirm the result ({detail}: {_sentence(message)}); "
        f"the change may already have been applied. Verified state: {state}. "
        "Do NOT repeat the call before checking this state."
    ) from exc
