"""Sliding-window rate limit per connection (the MCP spec requires servers to rate limit tool invocations)."""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from typing import TYPE_CHECKING

from evolution_api_mcp.errors import ToolExecutionError

if TYPE_CHECKING:
    from evolution_api_mcp.context import Connection
    from evolution_api_mcp.registry import ToolSpec

WINDOW_SECONDS = 60.0

_windows: dict[tuple[str, str], deque[float]] = {}
_lock = threading.Lock()


def reset() -> None:
    """Forget every recorded call (test isolation)."""
    with _lock:
        _windows.clear()


def check(spec: ToolSpec, conn: Connection) -> None:
    """Record one call of `spec` on `conn`, or raise `ToolExecutionError` when the per-minute limit is reached."""
    write = spec.kind != "read"
    limit = conn.max_writes_per_minute if write else conn.max_reads_per_minute
    if limit <= 0:
        return
    key = (conn.subject or "local", "write" if write else "read")
    with _lock:
        now = time.monotonic()
        window = _windows.setdefault(key, deque())
        while window and now - window[0] >= WINDOW_SECONDS:
            window.popleft()
        if len(window) >= limit:
            seconds = max(1, math.ceil(window[0] + WINDOW_SECONDS - now))
            raise ToolExecutionError(
                f"Rate limit reached: at most {limit} {'changes' if write else 'reads'} per minute on this "
                f"connection. Try again in {seconds} seconds."
            )
        window.append(now)


def charge(conn: Connection, *, writes: int) -> None:
    """Record `writes` more changes on `conn` (a call that sends several messages), or raise when they do not fit.

    `check` has already recorded the call itself; a tool that delivers N messages charges the other N - 1 here, before
    the first one leaves. A limit of 0 turns the limit off.
    """
    limit = conn.max_writes_per_minute
    if limit <= 0 or writes <= 0:
        return
    key = (conn.subject or "local", "write")
    with _lock:
        now = time.monotonic()
        window = _windows.setdefault(key, deque())
        while window and now - window[0] >= WINDOW_SECONDS:
            window.popleft()
        if len(window) + writes > limit:
            seconds = max(1, math.ceil(window[0] + WINDOW_SECONDS - now)) if window else 1
            raise ToolExecutionError(
                f"Rate limit reached: at most {limit} changes per minute on this connection. "
                f"Try again in {seconds} seconds."
            )
        window.extend([now] * writes)
