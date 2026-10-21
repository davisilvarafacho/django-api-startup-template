"""Fernet keyring read from the environment."""

from django.core.exceptions import ImproperlyConfigured

from utils.env import get_list_from_env


def get_sensitive_field_keys(*, require_configured: bool = False) -> list[bytes]:
    """Return the ordered Fernet keyring configured in the environment."""
    keys = [value.encode() for value in get_list_from_env("SENSITIVE_FIELD_KEYS")]
    if require_configured and not keys:
        raise ImproperlyConfigured("SENSITIVE_FIELD_KEYS deve conter ao menos uma chave Fernet.")
    return keys
