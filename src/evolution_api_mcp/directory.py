"""The name directory: who and what a chat id is, read from Evolution and kept in memory.

It is built from four Evolution answers (contacts, groups, a page of recent messages, the instance row), cached per
tenant for `TTL_SECONDS` and never written to disk. Tools use it to show names next to ids and to accept a name where a
chat is expected (`calls.resolve_chat`). A source Evolution fails to answer is listed in `Directory.incomplete`; the
rest of the directory is still usable.
"""

from __future__ import annotations

import re
import time
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Literal

import anyio

from evolution_api_mcp import discovery, jid, messages
from evolution_api_mcp.client import EvolutionClient, EvolutionError
from evolution_api_mcp.context import instance_path
from evolution_api_mcp.errors import ToolExecutionError
from evolution_api_mcp.registry import BAILEYS

if TYPE_CHECKING:
    from evolution_api_mcp.context import Connection

TTL_SECONDS = 600
REBUILD_AFTER_SECONDS = 60
MESSAGE_SAMPLE = 100
_OWN_NAME_PLACEHOLDER = "Você"  # what Evolution stores as the pushName of this account's own messages
_PHONE_PUNCTUATION = re.compile(r"[\s+()\-.]")
_DEVICE_SUFFIX = re.compile(r":\d+(?=@)", re.ASCII)

Kind = Literal["person", "group"]


@dataclass(frozen=True)
class Entry:
    chat_id: str
    name: str | None
    kind: Kind
    phone: str | None
    saved: bool


def normalize(text: str) -> str:
    """Lower-case, accent-free words separated by single spaces: "Mário  Rossi!" becomes "mario rossi"."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char)).casefold()
    return " ".join("".join(char if char.isalnum() else " " for char in stripped).split())


def _digits_query(text: str) -> str | None:
    """The digits of a phone-number query (at least 3), or None when `text` is a name."""
    cleaned = _PHONE_PUNCTUATION.sub("", text.strip())
    return cleaned if len(cleaned) >= 3 and cleaned.isascii() and cleaned.isdigit() else None


def _local_part(chat_id: str) -> str:
    return chat_id.split("@", 1)[0].split(":", 1)[0]


def _usable_name(name: object, chat_id: str) -> str | None:
    """A display name, or None when the value is empty or Evolution's fallback (the jid's own digits)."""
    if not isinstance(name, str):
        return None
    name = name.strip()
    if not name or name == _OWN_NAME_PLACEHOLDER or name == _local_part(chat_id):
        return None
    return name


def _dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _rows(body: object) -> list[dict]:
    return [row for row in body if isinstance(row, dict)] if isinstance(body, list) else []


@dataclass
class Directory:
    entries: dict[str, Entry] = field(default_factory=dict)
    lid_phone: dict[str, str] = field(default_factory=dict)
    me: frozenset[str] = frozenset()
    me_name: str | None = None
    incomplete: tuple[str, ...] = ()
    built_at: float = 0.0
    _phone_lid: dict[str, str] = field(default_factory=dict, repr=False)

    # --- lookups ---------------------------------------------------------------------------------------------

    def _counterpart(self, chat_id: str) -> str | None:
        """The phone JID behind a known @lid, or the @lid behind a phone JID, when both are known."""
        if chat_id.endswith("@lid"):
            digits = self.lid_phone.get(chat_id)
            return f"{digits}@s.whatsapp.net" if digits else None
        digits = jid.phone_of(chat_id)
        return self._phone_lid.get(digits) if digits else None

    def name_of(self, chat_id: str | None) -> str | None:
        if not chat_id:
            return None
        entry = self.entries.get(chat_id)
        if entry is not None and entry.name:
            return entry.name
        other = self._counterpart(chat_id)
        entry = self.entries.get(other) if other else None
        return entry.name if entry is not None else None

    def phone_of(self, chat_id: str | None) -> str | None:
        """Digits for a phone JID, or for an @lid whose phone number is known."""
        if not chat_id:
            return None
        digits = jid.phone_of(chat_id)
        if digits is not None:
            return digits
        known = self.lid_phone.get(chat_id)
        if known is not None:
            return known
        entry = self.entries.get(chat_id)
        return entry.phone if entry is not None else None

    def identities(self, chat_id: str) -> tuple[str, ...]:
        """`chat_id` followed by the same person's other id (phone JID or @lid) when both are known."""
        other = self._counterpart(chat_id)
        return (chat_id, other) if other is not None and other != chat_id else (chat_id,)

    def _collapse(self, entries: list[Entry]) -> list[Entry]:
        """Drop an @lid entry when the same person's phone-JID entry is in the list, so one person is one hit."""
        ids = {entry.chat_id for entry in entries}
        return [
            entry
            for entry in entries
            if not (entry.chat_id.endswith("@lid") and self._counterpart(entry.chat_id) in ids)
        ]

    def _ranked(self, scored: list[tuple[int, str, Entry]]) -> list[Entry]:
        """Entries by score, then normalized name, then chat_id; one hit per person."""
        scored.sort(key=lambda item: (item[0], item[1], item[2].chat_id))
        keep = {entry.chat_id for entry in self._collapse([entry for _, _, entry in scored])}
        return [entry for _, _, entry in scored if entry.chat_id in keep]

    def _by_name(self, wanted: str, kind: Kind | None) -> list[tuple[int, str, Entry]]:
        scored: list[tuple[int, str, Entry]] = []
        if not wanted:
            return scored
        for entry in self.entries.values():
            if kind is not None and entry.kind != kind:
                continue
            raw = self.name_of(entry.chat_id)
            name = normalize(raw) if raw else ""
            if not name:
                continue
            if name == wanted:
                score = 0
            elif name.startswith(wanted):
                score = 1
            elif any(word.startswith(wanted) for word in name.split()):
                score = 2
            elif wanted in name:
                score = 3
            else:
                continue
            scored.append((score, name, entry))
        return scored

    def _by_digits(self, digits: str, kind: Kind | None) -> list[tuple[int, str, Entry]]:
        scored: list[tuple[int, str, Entry]] = []
        for entry in self.entries.values():
            if kind is not None and entry.kind != kind:
                continue
            candidates = [value for value in (self.phone_of(entry.chat_id), _local_part(entry.chat_id)) if value]
            if digits in candidates:
                score = 0
            elif any(value.startswith(digits) for value in candidates):
                score = 1
            elif any(digits in value for value in candidates):
                score = 3
            else:
                continue
            scored.append((score, normalize(self.name_of(entry.chat_id) or ""), entry))
        return scored

    def exact(self, text: str, kind: Kind | None = None) -> list[Entry]:
        """Entries whose normalized name equals the normalized text."""
        return self._ranked([item for item in self._by_name(normalize(text), kind) if item[0] == 0])

    def partial(self, text: str, kind: Kind | None = None) -> list[Entry]:
        """Entries whose name contains the text (an exact match included), best first."""
        return self._ranked(self._by_name(normalize(text), kind))

    def search(self, text: str, kind: Kind | None = None) -> list[Entry]:
        """Entries matching a name, or a phone number of at least 3 digits, best first."""
        digits = _digits_query(text)
        return self._ranked(self._by_digits(digits, kind) if digits else self._by_name(normalize(text), kind))

    # --- learning --------------------------------------------------------------------------------------------

    def _link(self, lid: str, phone_jid: str) -> None:
        digits = jid.phone_of(phone_jid)
        if digits is None:
            return
        self.lid_phone[lid] = digits
        self._phone_lid[digits] = lid
        entry = self.entries.get(lid)
        if entry is not None and entry.phone != digits:
            self.entries[lid] = replace(entry, phone=digits)

    def _name(self, chat_id: str, name: str) -> None:
        """Name `chat_id`, and the same person's other id, when it has no name yet."""
        digits = self.phone_of(chat_id)
        for target in (chat_id, self._counterpart(chat_id)):
            if target is None:
                continue
            entry = self.entries.get(target)
            if entry is None and target == chat_id:
                self.entries[target] = Entry(target, name, "person", self.phone_of(target) or digits, False)
            elif entry is not None and not entry.name:
                self.entries[target] = replace(entry, name=name)

    def learn(self, rows: Iterable[dict]) -> None:
        """Take what stored message rows reveal: @lid to phone links, sender names and this account's own ids."""
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = _dict(row.get("key"))
            for first, second in (("remoteJid", "remoteJidAlt"), ("participant", "participantAlt")):
                a, b = key.get(first), key.get(second)
                if not isinstance(a, str) or not isinstance(b, str):
                    continue
                if a.endswith("@lid") and jid.phone_of(b):
                    self._link(a, b)
                elif b.endswith("@lid") and jid.phone_of(a):
                    self._link(b, a)

            participant = key.get("participant")
            if key.get("fromMe") is True:
                if isinstance(participant, str) and participant.endswith("@lid"):
                    self.me |= {_DEVICE_SUFFIX.sub("", participant)}
                continue
            chat_id = key.get("remoteJid")
            sender = participant if isinstance(participant, str) and participant else chat_id
            if not isinstance(sender, str) or sender.endswith("@g.us") or "@" not in sender:
                continue
            name = _usable_name(row.get("pushName"), sender)
            if name is not None:
                self._name(sender, name)


# --- building ----------------------------------------------------------------------------------------------------


def _own_id(owner: object) -> str | None:
    return _DEVICE_SUFFIX.sub("", owner) if isinstance(owner, str) and owner else None


async def _contacts(client: EvolutionClient, conn: Connection, directory: Directory) -> None:
    body = await client.request("POST", instance_path(conn.identity, "chat/findContacts"), json={"where": {}})
    for row in _rows(body):
        chat_id = row.get("remoteJid")
        if not isinstance(chat_id, str) or not chat_id:
            continue
        directory.entries[chat_id] = Entry(
            chat_id=chat_id,
            name=_usable_name(row.get("pushName"), chat_id),
            kind="group" if chat_id.endswith("@g.us") else "person",
            phone=jid.phone_of(chat_id),
            saved=bool(row.get("isSaved")),
        )


async def _groups(client: EvolutionClient, conn: Connection, directory: Directory) -> None:
    body = await client.request(
        "GET", instance_path(conn.identity, "group/fetchAllGroups"), params={"getParticipants": "false"}
    )
    for row in _rows(body):
        raw = row.get("id")
        if not isinstance(raw, str) or not raw:
            continue
        chat_id = raw if "@" in raw else f"{raw}@g.us"
        subject = row.get("subject")
        name = subject if isinstance(subject, str) and subject else None
        directory.entries[chat_id] = Entry(chat_id, name, "group", None, True)


async def _messages(client: EvolutionClient, conn: Connection, directory: Directory) -> None:
    page = await messages.fetch_page(client, conn.identity, where={}, page_size=MESSAGE_SAMPLE, page=1)
    directory.learn(page["records"])


async def _instance(client: EvolutionClient, conn: Connection, directory: Directory) -> None:
    row = await discovery.fetch_instance_row(client, conn.identity)
    own = _own_id(row.get("ownerJid"))
    if own is not None:
        directory.me |= {own}
    profile = row.get("profileName")
    directory.me_name = profile if isinstance(profile, str) and profile else None


async def build(client: EvolutionClient, conn: Connection) -> Directory:
    """Read the four sources in order; a source that fails is named in `incomplete` and the build goes on."""
    directory = Directory()
    incomplete: list[str] = []
    sources = [("contacts", _contacts)]
    if conn.identity.integration == BAILEYS:
        sources.append(("groups", _groups))
    sources += [("messages", _messages), ("instance", _instance)]
    for name, load in sources:
        try:
            await load(client, conn, directory)
        except (EvolutionError, ToolExecutionError):
            incomplete.append(name)
    directory.incomplete = tuple(incomplete)
    directory.built_at = time.monotonic()
    return directory


# --- cache -------------------------------------------------------------------------------------------------------

_cache: dict[str, Directory] = {}
_locks: dict[str, anyio.Lock] = {}


def _key(conn: Connection) -> str:
    return conn.subject or "local"


async def get(client: EvolutionClient, conn: Connection, *, rebuild: bool = False) -> Directory:
    """The cached directory of this connection, built on first use.

    Entries older than `TTL_SECONDS` are dropped on every call. `rebuild=True` builds again only when the cached
    directory is at least `REBUILD_AFTER_SECONDS` old, so repeated misses cannot hammer Evolution.
    """
    now = time.monotonic()
    for stale in [key for key, cached in _cache.items() if now - cached.built_at > TTL_SECONDS]:
        del _cache[stale]
    key = _key(conn)

    def fresh_enough() -> Directory | None:
        cached = _cache.get(key)
        if cached is not None and not (rebuild and time.monotonic() - cached.built_at >= REBUILD_AFTER_SECONDS):
            return cached
        return None

    if (cached := fresh_enough()) is not None:
        return cached
    async with _locks.setdefault(key, anyio.Lock()):
        if (cached := fresh_enough()) is not None:
            return cached
        built = await build(client, conn)
        _cache[key] = built
        return built


def forget(key: str) -> None:
    """Drop the directory cached for one tenant subject (or "local")."""
    _cache.pop(key, None)
    _locks.pop(key, None)


def reset() -> None:
    """Drop every cached directory."""
    _cache.clear()
    _locks.clear()
