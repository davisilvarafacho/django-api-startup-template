"""Fernet keyring read from the environment."""

import os

from django.core.exceptions import ImproperlyConfigured


def get_sensitive_field_keys(*, require_configured: bool = False) -> list[bytes]:
    """Return the ordered Fernet keyring configured in the environment."""
    keys = [value.strip().encode() for value in os.environ.get("SENSITIVE_FIELD_KEYS", "").split(",") if value.strip()]
    if require_configured and not keys:
        raise ImproperlyConfigured("SENSITIVE_FIELD_KEYS deve conter ao menos uma chave Fernet.")
    return keys
