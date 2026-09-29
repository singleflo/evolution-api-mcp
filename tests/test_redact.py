"""redact(): secrets never survive into a projection, structure and non-secrets do."""

from __future__ import annotations

from evolution_api_mcp.redact import REDACTED, SECRET_KEYS, redact


def test_secrets_are_replaced_at_every_depth_and_everything_else_is_kept() -> None:
    row = {
        "name": "inst",
        "token": "ABCD-1234",
        "Chatwoot": {"enabled": True, "url": "https://cw.example.com", "token": "cw-secret"},
        "Proxy": {"host": "proxy.example.com", "password": "hunter2"},
        "Setting": {"wavoipToken": "wavoip-secret", "alwaysOnline": False},
        "webhooks": [{"url": "https://hook.example.com", "headers": {"apikey": "k"}}, "plain"],
    }

    assert redact(row) == {
        "name": "inst",
        "token": REDACTED,
        "Chatwoot": {"enabled": True, "url": "https://cw.example.com", "token": REDACTED},
        "Proxy": {"host": "proxy.example.com", "password": REDACTED},
        "Setting": {"wavoipToken": REDACTED, "alwaysOnline": False},
        "webhooks": [{"url": "https://hook.example.com", "headers": {"apikey": REDACTED}}, "plain"],
    }


def test_key_matching_ignores_case_but_not_substrings() -> None:
    result = redact({"ApiKey": "a", "PASSWORD": "b", "basicAuthPassword": "c", "tokenCount": 5, "secretary": "x"})

    assert result == {
        "ApiKey": REDACTED,
        "PASSWORD": REDACTED,
        "basicAuthPassword": REDACTED,
        "tokenCount": 5,
        "secretary": "x",
    }


def test_empty_secret_values_stay_so_that_unset_is_distinguishable_from_set() -> None:
    result = redact({"password": "", "token": None, "secret": [], "apikey": {}, "jwt_key": "x"})

    assert result == {"password": "", "token": None, "secret": [], "apikey": {}, "jwt_key": REDACTED}


def test_a_secret_key_holding_a_structure_is_redacted_whole() -> None:
    assert redact({"secret": {"inner": "value"}}) == {"secret": REDACTED}


def test_input_is_not_mutated_and_lists_are_copied() -> None:
    original = {"items": [{"token": "t"}], "password": "p"}

    result = redact(original)

    assert original == {"items": [{"token": "t"}], "password": "p"}
    assert result["items"] is not original["items"]  # type: ignore[index]


def test_scalars_and_non_string_keys_pass_through() -> None:
    assert redact("token") == "token"
    assert redact(5) == 5
    assert redact(None) is None
    assert redact({1: "x", "n": 0}) == {1: "x", "n": 0}


def test_secret_key_set_is_lowercase_so_matching_by_lowering_the_key_is_complete() -> None:
    assert all(key == key.lower() for key in SECRET_KEYS)
