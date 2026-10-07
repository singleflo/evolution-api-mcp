"""The display time zone: formatting, detection, naive input, and its effect through a real tool call."""

import json
from datetime import datetime, timezone

import pytest

from evolution_api_mcp import clock, messages
from evolution_api_mcp.tools import chats
from evolution_api_mcp.tools.instance import get_instance_status
from tests.conftest import INSTANCE
from tests.fakes import program_directory

PERSON = "393331234567@s.whatsapp.net"
FIND_MESSAGES = f"/chat/findMessages/{INSTANCE}"
FETCH_INSTANCES = "/instance/fetchInstances"
STATE = f"/instance/connectionState/{INSTANCE}"


def test_default_zone_is_utc_and_keeps_the_z_suffix() -> None:
    assert clock.DISPLAY_ZONE.get() == "UTC"
    assert clock.fmt(datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)) == "2026-07-01T10:00:00Z"


def test_rome_summer_and_winter_offsets() -> None:
    with clock.displayed_in("Europe/Rome"):
        assert clock.fmt(datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)) == "2026-07-01T12:00:00+02:00"
        assert clock.fmt(datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)) == "2026-01-15T11:00:00+01:00"
    assert clock.DISPLAY_ZONE.get() == "UTC"


def test_messages_iso_follows_the_display_zone() -> None:
    assert messages.iso(1_700_000_000) == "2023-11-14T22:13:20Z"
    with clock.displayed_in("Europe/Rome"):
        assert messages.iso(1_700_000_000) == "2023-11-14T23:13:20+01:00"


def test_zone_rejects_an_unknown_name() -> None:
    assert clock.zone("UTC") is timezone.utc
    with pytest.raises(ValueError, match="Mars/Olympus"):
        clock.zone("Mars/Olympus")


def test_aware_reads_a_naive_value_in_the_display_zone_and_keeps_an_aware_one() -> None:
    naive = datetime(2026, 10, 2, 0, 0)
    with clock.displayed_in("Europe/Rome"):
        assert clock.aware(naive).utcoffset().total_seconds() == 7200
    assert clock.aware(naive).tzinfo is timezone.utc
    aware = datetime(2026, 10, 2, tzinfo=timezone.utc)
    with clock.displayed_in("Europe/Rome"):
        assert clock.aware(aware) is aware


def test_time_window_sends_a_naive_value_as_utc_from_the_display_zone() -> None:
    with clock.displayed_in("Europe/Rome"):
        window = messages.time_window(datetime(2026, 10, 2, 0, 0), datetime(2026, 10, 2, 12, 0))
    assert window == {"gte": "2026-10-01T22:00:00Z", "lte": "2026-10-02T10:00:00Z"}


# ---- detection ---------------------------------------------------------------------------------------------------


def test_detect_prefers_tz_and_strips_a_leading_colon(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clock.os.path, "realpath", lambda path: "/usr/share/zoneinfo/Asia/Tokyo")
    assert clock.detect_local_zone({"TZ": "Europe/Rome"}) == "Europe/Rome"
    assert clock.detect_local_zone({"TZ": ":Europe/Paris"}) == "Europe/Paris"


def test_detect_falls_back_to_the_localtime_link_when_tz_is_unusable(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def realpath(path: str) -> str:
        seen.append(path)
        return "/var/db/timezone/zoneinfo/Asia/Tokyo"

    monkeypatch.setattr(clock.os.path, "realpath", realpath)
    assert clock.detect_local_zone({"TZ": "Not/AZone"}) == "Asia/Tokyo"
    assert clock.detect_local_zone({}) == "Asia/Tokyo"
    assert seen == ["/etc/localtime", "/etc/localtime"]


def test_detect_ends_at_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clock.os.path, "realpath", lambda path: "/etc/localtime")
    assert clock.detect_local_zone({}) == "UTC"
    monkeypatch.setattr(clock.os.path, "realpath", lambda path: "/usr/share/zoneinfo/Nowhere/Land")
    assert clock.detect_local_zone({"TZ": ""}) == "UTC"


# ---- through the tools -------------------------------------------------------------------------------------------


def _record(message_id: str, ts: int) -> dict:
    return {
        "id": f"row-{message_id}",
        "key": {"id": message_id, "remoteJid": PERSON, "fromMe": False},
        "pushName": "Ana",
        "messageType": "conversation",
        "message": {"conversation": "Hello"},
        "messageTimestamp": ts,
    }


def _page(*records: dict) -> dict:
    return {"messages": {"total": len(records), "pages": 1, "currentPage": 1, "records": list(records)}}


@pytest.mark.anyio
async def test_read_messages_shows_times_in_the_connection_zone(evo, bound) -> None:
    program_directory(evo, INSTANCE)
    evo.on("POST", FIND_MESSAGES, json=_page(_record("3EB0AAAA01", 1_782_900_000)))
    with bound(evo, timezone="Europe/Rome"):
        result = json.loads(await chats.read_messages(PERSON))
    assert result["messages"][0]["timestamp"] == "2026-07-01T12:00:00+02:00"


@pytest.mark.anyio
async def test_read_messages_sends_a_naive_since_as_utc_from_the_connection_zone(evo, bound) -> None:
    program_directory(evo, INSTANCE)
    evo.on("POST", FIND_MESSAGES, json=_page(_record("3EB0AAAA01", 1_782_900_000)))
    with bound(evo, timezone="Europe/Rome"):
        await chats.read_messages(PERSON, since=datetime(2026, 10, 2, 0, 0))
    window = evo.last("POST", FIND_MESSAGES).json["where"]["messageTimestamp"]
    assert window["gte"] == "2026-10-01T22:00:00Z"


@pytest.mark.anyio
async def test_a_utc_connection_keeps_the_z_suffix_through_a_tool(evo, bound) -> None:
    program_directory(evo, INSTANCE)
    evo.on("POST", FIND_MESSAGES, json=_page(_record("3EB0AAAA01", 1_782_900_000)))
    with bound(evo):
        result = json.loads(await chats.read_messages(PERSON))
    assert result["messages"][0]["timestamp"] == "2026-07-01T10:00:00Z"


@pytest.mark.anyio
async def test_get_instance_status_reports_the_zone(evo, bound) -> None:
    evo.on(
        "GET",
        FETCH_INSTANCES,
        json=[{"name": INSTANCE, "connectionStatus": "open", "ownerJid": PERSON, "integration": "WHATSAPP-BAILEYS"}],
    )
    evo.on("GET", STATE, json={"instance": {"instanceName": INSTANCE, "state": "open"}})
    evo.on("GET", "/", json={"version": "2.3.7"})
    with bound(evo, timezone="Europe/Rome"):
        result = json.loads(await get_instance_status())
    assert result["timezone"] == "Europe/Rome"
