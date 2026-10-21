"""Testes dos helpers de ambiente (não tocam o banco)."""

import ast
import os
import re
from pathlib import Path
from typing import get_args

import pytest

from utils.env import EnviromentVar, get_bool_from_env, get_env_var, get_int_from_env, get_list_from_env


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


@pytest.mark.parametrize(("valor", "esperado"), [("true", True), ("True", True), (" TRUE ", True), ("false", False), ("False", False)])
def test_get_bool_from_env_converte_true_e_false(monkeypatch, valor, esperado):
    monkeypatch.setenv("AXES_ENABLED", valor)
    assert get_bool_from_env("AXES_ENABLED", not esperado) is esperado


@pytest.mark.parametrize("valor", ["1", "0", "yes", "no", "on", "None", "[]", "verdade"])
def test_get_bool_from_env_rejeita_qualquer_valor_alem_de_true_e_false(monkeypatch, valor):
    monkeypatch.setenv("AXES_ENABLED", valor)
    with pytest.raises(ValueError, match="AXES_ENABLED"):
        get_bool_from_env("AXES_ENABLED", True)


@pytest.mark.parametrize("valor", ["", "   "])
def test_get_bool_from_env_trata_vazio_como_ausencia(monkeypatch, valor):
    monkeypatch.setenv("AXES_ENABLED", valor)
    assert get_bool_from_env("AXES_ENABLED", True) is True


def test_get_bool_from_env_devolve_o_default_quando_a_variavel_nao_existe(monkeypatch):
    monkeypatch.delenv("AXES_ENABLED", raising=False)
    assert get_bool_from_env("AXES_ENABLED", False) is False


_HELPERS_DE_ENV = {"get_env_var", "get_bool_from_env", "get_int_from_env", "get_list_from_env"}
_RAIZ = Path(__file__).resolve().parents[2]
_DIRETORIOS_IGNORADOS = {".venv", ".git", ".examples", "node_modules", ".trail", "__pycache__"}


def _variaveis_lidas_no_codigo() -> set[str]:
    """Nomes literais passados aos helpers de `utils.env` em todo o repositório."""
    nomes = set()
    for diretorio, subdiretorios, arquivos in os.walk(_RAIZ):
        subdiretorios[:] = [d for d in subdiretorios if d not in _DIRETORIOS_IGNORADOS]
        for arquivo in arquivos:
            if not arquivo.endswith(".py"):
                continue
            arvore = ast.parse(Path(diretorio, arquivo).read_text(encoding="utf-8"))
            for no in ast.walk(arvore):
                if not isinstance(no, ast.Call) or not no.args:
                    continue
                funcao = no.func.id if isinstance(no.func, ast.Name) else getattr(no.func, "attr", None)
                primeiro = no.args[0]
                if funcao in _HELPERS_DE_ENV and isinstance(primeiro, ast.Constant) and isinstance(primeiro.value, str):
                    nomes.add(primeiro.value)
    return nomes


def test_enviroment_var_lista_exatamente_as_variaveis_lidas_pelo_codigo():
    declaradas = set(get_args(EnviromentVar))
    lidas = _variaveis_lidas_no_codigo()
    assert lidas - declaradas == set(), "lidas mas ausentes de EnviromentVar (utils/env.py)"
    assert declaradas - lidas == set(), "declaradas em EnviromentVar mas nunca lidas (remova-as)"


def test_toda_variavel_declarada_esta_documentada_no_env_example():
    exemplo = (_RAIZ / ".env.example").read_text(encoding="utf-8")
    documentadas = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", exemplo, flags=re.MULTILINE))
    assert set(get_args(EnviromentVar)) - documentadas == set(), "declaradas mas ausentes de .env.example"
