"""Removal of secrets from Evolution configuration before a tool projects it."""

from __future__ import annotations

SECRET_KEYS = frozenset(
    {
        "apikey",
        "api_key",
        "token",
        "secret",
        "password",
        "basicauthpassword",
        "wavoiptoken",
        "accesstoken",
        "access_token",
        "jwt_key",
    }
)
REDACTED = "[redacted]"


def redact(value: object) -> object:
    """Return a deep copy of `value` in which every secret is replaced by "[redacted]".

    Dicts and lists are copied recursively. A dict entry whose lower-cased key is in `SECRET_KEYS`
    becomes `REDACTED` when its value is non-empty; empty values (None, "", empty containers) stay
    as they are, so "no secret stored" remains distinguishable from "secret stored".
    """
    if isinstance(value, dict):
        out: dict[object, object] = {}
        for key, item in value.items():
            if isinstance(key, str) and key.lower() in SECRET_KEYS and item not in (None, "", [], {}):
                out[key] = REDACTED
            else:
                out[key] = redact(item)
        return out
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value
