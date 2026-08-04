"""Conversion between Python values and the plaintext protected by the cipher."""

import json

TEXT = "text"
JSON = "json"


def serialize(value, kind: str) -> str:
    """Return the plaintext representation of ``value`` for the given kind."""
    if kind == JSON:
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    return str(value)


def deserialize(plaintext: str, kind: str):
    """Return the Python value encoded in ``plaintext`` for the given kind."""
    if kind == JSON:
        return json.loads(plaintext)
    return plaintext
