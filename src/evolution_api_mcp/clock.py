"""The display time zone: how times are shown to the model and how a time given without a zone is read.

Wire values sent to Evolution stay UTC; only what a tool shows or accepts from the model goes through here.
"""

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone, tzinfo
from zoneinfo import ZoneInfo

UTC_NAME = "UTC"
DISPLAY_ZONE: ContextVar[str] = ContextVar("evolution_display_zone", default=UTC_NAME)

_UTC_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_ZONEINFO_MARKER = "/zoneinfo/"


def zone(name: str) -> tzinfo:
    """The time zone called `name`: `timezone.utc` for "UTC", else the IANA zone. Raises ValueError when unknown."""
    if name == UTC_NAME:
        return timezone.utc
    try:
        return ZoneInfo(name)
    except (KeyError, ValueError, OSError) as exc:
        raise ValueError(f"unknown time zone '{name}'") from exc


def _accepted(name: str) -> bool:
    if not name:
        return False
    try:
        zone(name)
    except ValueError:
        return False
    return True


def detect_local_zone(environ: Mapping[str, str]) -> str:
    """The computer's IANA zone: `TZ`, then the `/etc/localtime` link target, else "UTC"."""
    from_env = environ.get("TZ", "").strip().lstrip(":")
    if _accepted(from_env):
        return from_env
    target = os.path.realpath("/etc/localtime")
    if _ZONEINFO_MARKER in target:
        from_link = target.rsplit(_ZONEINFO_MARKER, 1)[1]
        if _accepted(from_link):
            return from_link
    return UTC_NAME


@contextmanager
def displayed_in(name: str) -> Iterator[None]:
    """Show times in the zone `name` within this block."""
    token = DISPLAY_ZONE.set(name)
    try:
        yield
    finally:
        DISPLAY_ZONE.reset(token)


def current_zone() -> tzinfo:
    return zone(DISPLAY_ZONE.get())


def fmt(moment: datetime) -> str:
    """`moment` in the display zone: `2026-07-01T10:00:00Z` for UTC, else `2026-07-01T12:00:00+02:00`."""
    local = moment.astimezone(current_zone())
    if DISPLAY_ZONE.get() == UTC_NAME:
        return local.strftime(_UTC_FORMAT)
    return local.isoformat(timespec="seconds")


def aware(value: datetime) -> datetime:
    """`value` unchanged when it carries a zone; a naive value is read in the display zone."""
    if value.tzinfo is None:
        return value.replace(tzinfo=current_zone())
    return value
