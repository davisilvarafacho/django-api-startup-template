"""Testes dos helpers de ambiente (não tocam o banco)."""

from utils.env import get_env_var, get_list_from_env


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
