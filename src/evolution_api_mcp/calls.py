"""The one way a tool talks to Evolution and turns bad input into a tool error.

Every tool module uses these helpers so failures read the same everywhere: reads report "Nothing was changed",
writes that may have been applied report UNCERTAIN together with a re-read of the state.
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Literal

from evolution_api_mcp import directory, jid, messages
from evolution_api_mcp.client import EvolutionClient, EvolutionError, EvolutionHTTPError
from evolution_api_mcp.context import Connection, InstanceIdentity, instance_path
from evolution_api_mcp.errors import ToolExecutionError, raise_evolution_failure
from evolution_api_mcp.registry import BAILEYS, BUSINESS

BUSINESS_MEDIA_NOTE = (
    " On WhatsApp Business Platform instances Evolution stores received media only when its S3/MinIO storage "
    "is enabled."
)


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


def parse_chat(value: str) -> str:
    """A phone number or chat id as a JID, with no name lookup; bad input becomes a tool error."""
    try:
        return jid.normalize_chat(value)
    except ValueError as exc:
        raise ToolExecutionError(str(exc)) from exc


def parse_group(value: str) -> str:
    """A group id or its digits as a `…@g.us` JID, with no name lookup; bad input becomes a tool error."""
    try:
        return jid.normalize_group(value)
    except ValueError as exc:
        raise ToolExecutionError(str(exc)) from exc


def parse_phone(value: str) -> str:
    """The digits of a phone number or person chat id; groups and @lid ids are refused."""
    digits = jid.phone_of(parse_chat(value))
    if digits is None:
        raise ToolExecutionError(f"{value} is not a phone number; this tool takes international phone numbers.")
    return digits


# --- names ---------------------------------------------------------------------------------------------------------

Purpose = Literal["read", "write", "send"]
Kind = directory.Kind

_PURPOSE_SUFFIX: dict[str, str] = {"read": "", "write": " Nothing was changed.", "send": " Nothing was sent."}
_PHONE_PUNCTUATION = re.compile(r"[\s+()\-.]")
_MAX_CANDIDATES = 10


def is_literal_chat(value: str) -> bool:
    """A chat id (anything with an `@`) or a phone number, as opposed to a contact or group name."""
    value = value.strip()
    digits = _PHONE_PUNCTUATION.sub("", value)
    return "@" in value or (digits.isascii() and digits.isdigit())


def _candidates(entries: list[directory.Entry]) -> str:
    shown = "; ".join(f"{entry.name or entry.chat_id} ({entry.chat_id})" for entry in entries[:_MAX_CANDIDATES])
    extra = len(entries) - _MAX_CANDIDATES
    return f"{shown} and {extra} more" if extra > 0 else shown


def _incomplete_note(names: directory.Directory) -> str:
    if not names.incomplete:
        return ""
    sources = ", ".join(names.incomplete)
    return f" Evolution did not return the {sources} list, so names there were not checked."


def _ambiguous(value: str, entries: list[directory.Entry], purpose: Purpose) -> ToolExecutionError:
    return ToolExecutionError(
        f"'{value}' matches {len(entries)} chats: {_candidates(entries)}. Pass the chat_id of the one you mean."
        + _PURPOSE_SUFFIX[purpose]
    )


async def _resolve_name(
    client: EvolutionClient, conn: Connection, value: str, *, purpose: Purpose, kind: Kind | None
) -> str:
    """The chat_id of the contact or group called `value`: a unique exact name, or for reads a unique partial one."""
    names = await directory.get(client, conn)
    for attempt in range(2):
        exact = names.exact(value, kind)
        if len(exact) == 1:
            return exact[0].chat_id
        if exact:
            raise _ambiguous(value, exact, purpose)
        if purpose == "read":
            partial = names.partial(value, kind)
            if len(partial) == 1:
                return partial[0].chat_id
            if partial:
                raise _ambiguous(value, partial, purpose)
        if attempt == 0:
            names = await directory.get(client, conn, rebuild=True)

    note = _incomplete_note(names) + _PURPOSE_SUFFIX[purpose]
    close = names.partial(value, kind)
    if close:
        raise ToolExecutionError(
            f"No contact or group is named exactly '{value}'. Close matches: {_candidates(close)}. "
            f"Pass the chat_id of the one you mean.{note}"
        )
    raise ToolExecutionError(
        f"No contact or group named '{value}' was found. find_chats searches names and numbers.{note}"
    )


async def resolve_chat(
    client: EvolutionClient, conn: Connection, value: str, *, purpose: Purpose, kind: Kind | None = None
) -> str:
    """A chat JID from a phone number, a chat id or a contact or group name.

    Numbers and ids are parsed as they are and never touch Evolution. A name needs one exact match; reads also accept
    one partial match. An ambiguous or unknown name is refused with the candidates (`purpose` decides the closing
    "Nothing was changed/sent." of the refusal).
    """
    if is_literal_chat(value):
        return parse_chat(value)
    return await _resolve_name(client, conn, value, purpose=purpose, kind=kind)


async def resolve_group(client: EvolutionClient, conn: Connection, value: str, *, purpose: Purpose) -> str:
    """A group JID from its id, its digits or its exact name (reads also take one partial name)."""
    if value.strip().endswith("@g.us") or jid.is_group_id(value):
        return parse_group(value)
    return await _resolve_name(client, conn, value, purpose=purpose, kind="group")


async def resolve_person(client: EvolutionClient, conn: Connection, value: str, *, purpose: Purpose) -> tuple[str, str]:
    """`(chat id, phone digits)` of a person given as a phone number, a chat id or a contact name.

    A person WhatsApp shows only as an @lid id resolves when the directory has learned the phone number behind it.
    """
    suffix = _PURPOSE_SUFFIX[purpose]
    if is_literal_chat(value):
        chat_jid = parse_chat(value)
        if jid.is_group(chat_jid):
            raise ToolExecutionError(
                f"{value} is not a phone number; this tool takes international phone numbers.{suffix}"
            )
    else:
        chat_jid = await _resolve_name(client, conn, value, purpose=purpose, kind="person")

    digits = jid.phone_of(chat_jid)
    if digits is None and chat_jid.endswith("@lid"):
        digits = (await directory.get(client, conn)).phone_of(chat_jid)
        if digits is not None:
            chat_jid = f"{digits}@s.whatsapp.net"
    if digits is None:
        raise ToolExecutionError(
            f"{value} has no known phone number: WhatsApp shows this person only as {chat_jid}.{suffix}"
        )
    return chat_jid, digits


async def find_stored(
    client: EvolutionClient, conn: Connection, message_id: str, chat_jid: str | None, *, purpose: Purpose
) -> dict | None:
    """The stored row of `message_id` (within one chat when `chat_jid` is given), or None when there is none.

    An id shared by several chats is a tool error that names them; an Evolution failure is reported as one.
    """
    try:
        return await messages.find_message(client, conn.identity, message_id, chat_jid)
    except messages.AmbiguousMessageId as exc:
        raise ToolExecutionError(
            f"Message {message_id} exists in {len(exc.chat_ids)} chats ({', '.join(exc.chat_ids)}). "
            f"Pass chat to pick one.{_PURPOSE_SUFFIX[purpose]}"
        ) from exc
    except EvolutionError as exc:
        await raise_evolution_failure(exc, phase="before_mutation")
        raise  # unreachable: raise_evolution_failure always raises


async def stored_message(
    client: EvolutionClient,
    conn: Connection,
    message_id: str,
    chat_jid: str | None,
    *,
    purpose: Purpose,
    not_found_note: str = "",
) -> dict:
    """The stored row of `message_id`, or a tool error: the id is unknown, or it exists in several chats.

    `not_found_note` is appended to the not-found message (before the purpose suffix).
    """
    row = await find_stored(client, conn, message_id, chat_jid, purpose=purpose)
    if row is None:
        raise ToolExecutionError(
            f"Message {message_id} was not found{' in this chat' if chat_jid else ''}. "
            f"read_messages, search_messages and list_recent_messages show message ids."
            f"{not_found_note}{_PURPOSE_SUFFIX[purpose]}"
        )
    return row


def decoded_size(encoded: str) -> int:
    """The byte size of base64 text once decoded, without decoding it."""
    padding = 2 if encoded.endswith("==") else 1 if encoded.endswith("=") else 0
    return len(encoded) * 3 // 4 - padding


def decode_media(encoded: str, message_id: str) -> bytes:
    """The bytes of Evolution's base64 media, or a tool error when the text is not valid base64."""
    try:
        return base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise ToolExecutionError(f"Evolution returned unreadable media for message {message_id}.") from None


async def fetch_media(
    client: EvolutionClient,
    conn: Connection,
    message_id: str,
    chat_jid: str | None,
    *,
    allowed: frozenset[str],
    not_allowed: str,
    purpose: Purpose,
    row: dict | None = None,
) -> tuple[dict, dict, str]:
    """Find the stored message and ask Evolution for its media. Returns `(row, answer, normalized_type)`.

    `not_allowed` is the refusal text for a type outside `allowed`; `{id}` and `{type}` are filled in. A caller that
    already holds the stored row passes it as `row` to skip the lookup.
    """
    identity = conn.identity
    note = BUSINESS_MEDIA_NOTE if identity.integration == BUSINESS else ""
    if row is None:
        row = await stored_message(client, conn, message_id, chat_jid, purpose=purpose, not_found_note=note)
    stored = row.get("message")
    kind = messages.normalize_type(row.get("messageType"), stored if isinstance(stored, dict) else {})
    if kind not in allowed:
        raise ToolExecutionError(not_allowed.format(id=message_id, type=kind))

    if identity.integration == BAILEYS:
        message: dict = {"key": {"id": message_id}}
    else:
        message = {"key": row.get("key"), "messageType": row.get("messageType"), "message": row.get("message")}
    answer = await call(
        client, identity, "POST", "chat/getBase64FromMediaMessage", json={"message": message}, timeout=300
    )
    body = answer if isinstance(answer, dict) else {}
    encoded = body.get("base64")
    if not isinstance(encoded, str) or not encoded:
        raise ToolExecutionError(f"Evolution returned no media for message {message_id}.")
    return row, body, kind


def invite_code(value: str) -> str:
    """A group invite code from a bare code or a chat.whatsapp.com link."""
    try:
        return jid.parse_invite(value)
    except ValueError as exc:
        raise ToolExecutionError(str(exc)) from exc
