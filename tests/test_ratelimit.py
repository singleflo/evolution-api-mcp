"""Sliding-window rate limit: writes and reads are counted per connection."""

from __future__ import annotations

import types

import pytest

from evolution_api_mcp import ratelimit
from evolution_api_mcp.errors import ToolExecutionError
from evolution_api_mcp.registry import ToolSpec

WRITE = ToolSpec(name="send_thing", title="Send thing", toolset="messaging", kind="destructive", idempotent=False)
READ = ToolSpec(name="list_things", title="List things", toolset="chats", kind="read", idempotent=True)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    fake = Clock()
    monkeypatch.setattr(ratelimit, "time", types.SimpleNamespace(monotonic=fake.monotonic))
    return fake


def test_the_thirty_first_write_in_a_minute_is_refused_with_the_wait(make_connection, clock):
    conn = make_connection(max_writes_per_minute=30)
    for _ in range(30):
        ratelimit.check(WRITE, conn)
        clock.now += 1
    with pytest.raises(ToolExecutionError) as excinfo:
        ratelimit.check(WRITE, conn)
    # The oldest write happened 30 seconds ago and leaves the window in 30 more.
    assert str(excinfo.value) == (
        "Rate limit reached: at most 30 changes per minute on this connection. Try again in 30 seconds."
    )


def test_the_window_slides_so_a_slot_frees_when_the_oldest_call_ages_out(make_connection, clock):
    conn = make_connection(max_writes_per_minute=2)
    ratelimit.check(WRITE, conn)
    clock.now += 10
    ratelimit.check(WRITE, conn)
    with pytest.raises(ToolExecutionError, match="Try again in 50 seconds"):
        ratelimit.check(WRITE, conn)
    clock.now += 50
    ratelimit.check(WRITE, conn)
    with pytest.raises(ToolExecutionError, match="Try again in 10 seconds"):
        ratelimit.check(WRITE, conn)


def test_a_refused_call_does_not_extend_the_wait(make_connection, clock):
    conn = make_connection(max_writes_per_minute=1)
    ratelimit.check(WRITE, conn)
    for _ in range(5):
        clock.now += 1
        with pytest.raises(ToolExecutionError):
            ratelimit.check(WRITE, conn)
    clock.now += 55
    ratelimit.check(WRITE, conn)


def test_reads_are_unlimited_when_the_read_limit_is_zero(make_connection, clock):
    conn = make_connection(max_writes_per_minute=1, max_reads_per_minute=0)
    for _ in range(500):
        ratelimit.check(READ, conn)


def test_writes_are_unlimited_when_the_write_limit_is_zero(make_connection, clock):
    conn = make_connection(max_writes_per_minute=0)
    for _ in range(500):
        ratelimit.check(WRITE, conn)


def test_hosted_reads_are_limited_at_the_configured_rate(make_connection, clock):
    conn = make_connection(mode="hosted", subject="t_abc", max_reads_per_minute=120, max_writes_per_minute=30)
    for _ in range(120):
        ratelimit.check(READ, conn)
    with pytest.raises(ToolExecutionError) as excinfo:
        ratelimit.check(READ, conn)
    assert str(excinfo.value) == (
        "Rate limit reached: at most 120 reads per minute on this connection. Try again in 60 seconds."
    )


def test_reads_and_writes_are_counted_separately(make_connection, clock):
    conn = make_connection(max_writes_per_minute=1, max_reads_per_minute=1)
    ratelimit.check(WRITE, conn)
    ratelimit.check(READ, conn)
    with pytest.raises(ToolExecutionError, match="changes"):
        ratelimit.check(WRITE, conn)
    with pytest.raises(ToolExecutionError, match="reads"):
        ratelimit.check(READ, conn)


def test_tenants_have_independent_windows(make_connection, clock):
    first = make_connection(mode="hosted", subject="t_one", max_writes_per_minute=1)
    second = make_connection(mode="hosted", subject="t_two", max_writes_per_minute=1)
    ratelimit.check(WRITE, first)
    ratelimit.check(WRITE, second)
    with pytest.raises(ToolExecutionError):
        ratelimit.check(WRITE, first)


def test_reset_clears_every_window(make_connection, clock):
    conn = make_connection(max_writes_per_minute=1)
    ratelimit.check(WRITE, conn)
    ratelimit.reset()
    ratelimit.check(WRITE, conn)
