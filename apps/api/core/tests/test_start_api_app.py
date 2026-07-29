"""Testes do comando start_api_app (não tocam o banco)."""

from django.core.management.base import CommandError

import pytest

from apps.api.core.management.commands.start_api_app import (
    insert_business_app,
    settings_module_path,
)

SETTINGS_STUB = """BUSINESS_APPS = [
    "apps.api.autenticacao",
    "apps.api.base",
    "apps.api.core",
    "apps.organizacoes",
    "apps.usuarios",
]
"""


def test_settings_module_path_localiza_o_arquivo_real():
    caminho = settings_module_path()

    assert caminho.is_file()
    assert caminho.name == "settings.py"
    assert caminho.parent.name == "api"


def test_insere_na_posicao_alfabetica():
    resultado = insert_business_app(SETTINGS_STUB, "apps.faturamento")

    assert (
        resultado
        == """BUSINESS_APPS = [
    "apps.api.autenticacao",
    "apps.api.base",
    "apps.api.core",
    "apps.faturamento",
    "apps.organizacoes",
    "apps.usuarios",
]
"""
    )


def test_insere_no_fim_quando_e_o_ultimo_alfabeticamente():
    resultado = insert_business_app(SETTINGS_STUB, "apps.vendas")

    assert resultado.endswith("""    "apps.usuarios",
    "apps.vendas",
]
""")


def test_app_ja_registrado_devolve_none():
    assert insert_business_app(SETTINGS_STUB, "apps.usuarios") is None


def test_preserva_o_resto_do_arquivo():
    source = f"DEBUG = True\n\n{SETTINGS_STUB}\nMIDDLEWARE = []\n"

    resultado = insert_business_app(source, "apps.faturamento")

    assert resultado.startswith("DEBUG = True\n\n")
    assert resultado.endswith("]\n\nMIDDLEWARE = []\n")


def test_bloco_ausente_falha_alto():
    with pytest.raises(CommandError, match="BUSINESS_APPS"):
        insert_business_app("DEBUG = True\n", "apps.faturamento")
