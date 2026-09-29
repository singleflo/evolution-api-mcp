from __future__ import annotations

import pytest

from evolution_api_mcp import jid


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # plain numbers
        ("393331234567", "393331234567@s.whatsapp.net"),
        ("+39 (333) 123-4567", "393331234567@s.whatsapp.net"),
        # device suffix is stripped, with and without a domain
        ("393331234567:12@s.whatsapp.net", "393331234567@s.whatsapp.net"),
        ("393331234567:12", "393331234567@s.whatsapp.net"),
        # known domains pass through untouched
        ("120363025246125486@g.us", "120363025246125486@g.us"),
        ("abc123@lid", "abc123@lid"),
        ("status@broadcast", "status@broadcast"),
        # 18+ digits are a group
        ("120363025246125486", "120363025246125486@g.us"),
        ("5511999999999-1234567890123", "5511999999999-1234567890123@g.us"),
        # Brazil: the extra 9 is dropped for DDD >= 31 when the subscriber number starts with 7 or more
        ("5531987654321", "553187654321@s.whatsapp.net"),
        ("5531967654321", "5531967654321@s.whatsapp.net"),
        ("5511987654321", "5511987654321@s.whatsapp.net"),
        ("5531707654321", "5531707654321@s.whatsapp.net"),
        # Mexico and Argentina: a 13-digit number loses its fourth digit
        ("5215512345678", "525512345678@s.whatsapp.net"),
        ("5491123456789", "541123456789@s.whatsapp.net"),
        # other countries with 13 digits are untouched
        ("4912345678901", "4912345678901@s.whatsapp.net"),
        # a 12-digit Mexican number is untouched
        ("525512345678", "525512345678@s.whatsapp.net"),
    ],
)
def test_create_jid_matches_evolution(raw: str, expected: str) -> None:
    assert jid.create_jid(raw) == expected


def test_normalize_chat_accepts_phone_formats_and_chat_ids() -> None:
    assert jid.normalize_chat("+39 (333) 123-4567") == "393331234567@s.whatsapp.net"
    assert jid.normalize_chat("  393331234567 ") == "393331234567@s.whatsapp.net"
    assert jid.normalize_chat("39.333.123.4567") == "393331234567@s.whatsapp.net"
    assert jid.normalize_chat("393331234567:12@s.whatsapp.net") == "393331234567@s.whatsapp.net"
    assert jid.normalize_chat("120363025246125486@g.us") == "120363025246125486@g.us"
    assert jid.normalize_chat("987654321@lid") == "987654321@lid"
    assert jid.normalize_chat("5531987654321") == "553187654321@s.whatsapp.net"


@pytest.mark.parametrize("bad", ["abc", "123", "x@foo.com", "@g.us", "1234567890123456", "393331234567@broadcast", ""])
def test_normalize_chat_rejects_with_actionable_message(bad: str) -> None:
    with pytest.raises(ValueError) as caught:
        jid.normalize_chat(bad)
    assert str(caught.value) == (
        f"Unsupported chat id '{bad.strip()}'. Use an international phone number (country code first) "
        "or a chat id from list_chats."
    )


def test_normalize_group() -> None:
    assert jid.normalize_group("120363025246125486") == "120363025246125486@g.us"
    assert jid.normalize_group("120363025246125486@g.us") == "120363025246125486@g.us"
    assert jid.normalize_group("5511999999999-1234567890@g.us") == "5511999999999-1234567890@g.us"
    for bad in ("abc", "393331234567@s.whatsapp.net", "@g.us", ""):
        with pytest.raises(ValueError):
            jid.normalize_group(bad)


def test_phone_of_only_for_people() -> None:
    assert jid.phone_of("393331234567@s.whatsapp.net") == "393331234567"
    assert jid.phone_of("393331234567:5@s.whatsapp.net") == "393331234567"
    assert jid.phone_of("120363025246125486@g.us") is None
    assert jid.phone_of("987654321@lid") is None
    assert jid.phone_of("abc@s.whatsapp.net") is None


def test_is_group() -> None:
    assert jid.is_group("120363025246125486@g.us")
    assert not jid.is_group("393331234567@s.whatsapp.net")
    assert not jid.is_group("987654321@lid")


def test_parse_invite_accepts_code_and_link() -> None:
    code = "AbCdEfGhIjKlMnOpQrStUv"
    assert jid.parse_invite(code) == code
    assert jid.parse_invite(f"https://chat.whatsapp.com/{code}") == code
    assert jid.parse_invite(f"  https://chat.whatsapp.com/{code}?mode=ems_copy_t ") == code
    for bad in ("short", "https://example.com/AbCdEfGhIjKlMnOpQrStUv", f"http://chat.whatsapp.com/{code}", ""):
        with pytest.raises(ValueError):
            jid.parse_invite(bad)
