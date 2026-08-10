"""Testes dos helpers de ambiente (não tocam o banco)."""

import pytest

from utils.env import get_env_var, get_int_from_env, get_list_from_env


def test_get_list_from_env(monkeypatch):
    monkeypatch.setenv("DJANGO_ALLOWED_HOSTS", "a.com, b.com ,c.com")
    assert get_list_from_env("DJANGO_ALLOWED_HOSTS") == ["a.com", "b.com", "c.com"]


def test_get_list_from_env_default(monkeypatch):
    monkeypatch.delenv("DJANGO_ALLOWED_HOSTS", raising=False)
    assert get_list_from_env("DJANGO_ALLOWED_HOSTS", ["x"]) == ["x"]
    assert get_list_from_env("DJANGO_ALLOWED_HOSTS") == []


def test_get_env_var_default(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert get_env_var("SENTRY_DSN") is None
    assert get_env_var("SENTRY_DSN", "fallback") == "fallback"


def test_get_int_from_env_devolve_o_default_quando_a_variavel_nao_existe(monkeypatch):
    monkeypatch.delenv("METADATA_MAX_KEYS", raising=False)
    assert get_int_from_env("METADATA_MAX_KEYS", 50) == 50


def test_get_int_from_env_converte_o_valor_do_ambiente(monkeypatch):
    monkeypatch.setenv("METADATA_MAX_KEYS", "7")
    assert get_int_from_env("METADATA_MAX_KEYS", 50) == 7


def test_get_int_from_env_trata_string_vazia_como_ausencia(monkeypatch):
    monkeypatch.setenv("METADATA_MAX_KEYS", "")
    assert get_int_from_env("METADATA_MAX_KEYS", 50) == 50


def test_get_int_from_env_rejeita_valor_nao_numerico(monkeypatch):
    monkeypatch.setenv("METADATA_MAX_KEYS", "muitas")
    with pytest.raises(ValueError, match="METADATA_MAX_KEYS"):
        get_int_from_env("METADATA_MAX_KEYS", 50)
