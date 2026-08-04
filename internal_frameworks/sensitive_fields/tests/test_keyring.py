"""Tests for the Fernet keyring read from the environment."""

from django.core.exceptions import ImproperlyConfigured

import pytest

from internal_frameworks.sensitive_fields.keyring import get_sensitive_field_keys


def test_keyring_remove_espacos_e_preserva_ordem(monkeypatch):
    monkeypatch.setenv("SENSITIVE_FIELD_KEYS", "new-key, old-key")

    assert get_sensitive_field_keys(require_configured=True) == [b"new-key", b"old-key"]


def test_keyring_ausente_falha_quando_obrigatorio(monkeypatch):
    monkeypatch.delenv("SENSITIVE_FIELD_KEYS", raising=False)

    with pytest.raises(ImproperlyConfigured, match="SENSITIVE_FIELD_KEYS"):
        get_sensitive_field_keys(require_configured=True)
