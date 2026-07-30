"""Testes do comando start_api_app (não tocam o banco)."""

from django.core.management import call_command
from django.core.management.base import CommandError

import pytest

from apps.api.core.management.commands import start_api_app
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


@pytest.fixture
def projeto(tmp_path, settings, monkeypatch):
    """Aponta o comando para um projeto de mentira em `tmp_path`.

    Devolve o diretório raiz; o settings falso fica em `<raiz>/api_settings.py`.
    """
    settings.BASE_DIR = tmp_path
    (tmp_path / "apps").mkdir()

    settings_falso = tmp_path / "api_settings.py"
    settings_falso.write_text(SETTINGS_STUB, encoding="utf-8")
    monkeypatch.setattr(start_api_app, "settings_module_path", lambda: settings_falso)

    return tmp_path


def test_cria_a_estrutura_da_convencao(projeto):
    call_command("start_api_app", "vendas")

    app_dir = projeto / "apps" / "vendas"
    criados = sorted(str(caminho.relative_to(app_dir)) for caminho in app_dir.rglob("*"))

    assert criados == [
        "__init__.py",
        "admin.py",
        "apps.py",
        "docs.py",
        "filters.py",
        "handlers.py",
        "migrations",
        "migrations/__init__.py",
        "models.py",
        "serializers.py",
        "subapps",
        "tests",
        "tests/__init__.py",
        "urls.py",
        "views.py",
    ]


def test_tests_e_pacote_e_nao_arquivo(projeto):
    call_command("start_api_app", "vendas")

    app_dir = projeto / "apps" / "vendas"

    assert (app_dir / "tests" / "__init__.py").is_file()
    assert not (app_dir / "tests.py").exists()


def test_subapps_nasce_diretorio_vazio(projeto):
    call_command("start_api_app", "vendas")

    subapps = projeto / "apps" / "vendas" / "subapps"

    assert subapps.is_dir()
    assert list(subapps.iterdir()) == []


def test_apps_py_traz_o_dotted_path(projeto):
    call_command("start_api_app", "vendas")

    conteudo = (projeto / "apps" / "vendas" / "apps.py").read_text(encoding="utf-8")

    assert conteudo == (
        "from django.apps import AppConfig\n"
        "\n"
        "\n"
        "class VendasConfig(AppConfig):\n"
        '    default_auto_field = "django.db.models.BigAutoField"\n'
        '    name = "apps.vendas"\n'
    )


def test_urls_py_traz_o_router(projeto):
    call_command("start_api_app", "vendas")

    conteudo = (projeto / "apps" / "vendas" / "urls.py").read_text(encoding="utf-8")

    assert conteudo == (
        "from django.urls import include, path\n"
        "\n"
        "from rest_framework.routers import DefaultRouter\n"
        "\n"
        "router = DefaultRouter()\n"
        "\n"
        "urlpatterns = [\n"
        '    path("", include(router.urls)),\n'
        "]\n"
    )


def test_modulos_nascem_vazios(projeto):
    call_command("start_api_app", "vendas")

    app_dir = projeto / "apps" / "vendas"
    vazios = ["admin.py", "docs.py", "filters.py", "handlers.py", "models.py", "serializers.py", "views.py"]

    for nome in vazios:
        assert (app_dir / nome).read_text(encoding="utf-8") == "", nome


def test_registra_em_business_apps(projeto):
    call_command("start_api_app", "vendas")

    conteudo = (projeto / "api_settings.py").read_text(encoding="utf-8")

    assert '    "apps.vendas",\n' in conteudo


def test_destino_existente_falha_sem_escrever_nada(projeto):
    (projeto / "apps" / "vendas").mkdir()

    with pytest.raises(CommandError, match="já existe"):
        call_command("start_api_app", "vendas")

    assert list((projeto / "apps" / "vendas").iterdir()) == []
    assert "vendas" not in (projeto / "api_settings.py").read_text(encoding="utf-8")


def criar_app_falso(diretorio):
    """Cria o mínimo que faz um diretório ser reconhecido como app pai."""
    diretorio.mkdir(parents=True)
    (diretorio / "__init__.py").touch()
    (diretorio / "apps.py").touch()


def test_parent_de_primeiro_nivel_cria_dentro_de_subapps(projeto):
    criar_app_falso(projeto / "apps" / "vendas")

    call_command("start_api_app", "pedidos", "--parent", "vendas")

    assert (projeto / "apps" / "vendas" / "subapps" / "pedidos" / "apps.py").is_file()


def test_parent_cria_o_subapps_ausente_sem_init(projeto):
    criar_app_falso(projeto / "apps" / "vendas")

    call_command("start_api_app", "pedidos", "--parent", "vendas")

    subapps = projeto / "apps" / "vendas" / "subapps"

    assert subapps.is_dir()
    assert not (subapps / "__init__.py").exists()


def test_parent_de_segundo_nivel_gera_dotted_path_completo(projeto):
    criar_app_falso(projeto / "apps" / "vendas" / "subapps" / "pedidos")

    call_command("start_api_app", "itens", "--parent", "pedidos")

    destino = projeto / "apps" / "vendas" / "subapps" / "pedidos" / "subapps" / "itens"
    conteudo = (destino / "apps.py").read_text(encoding="utf-8")

    assert "class ItensConfig(AppConfig):" in conteudo
    assert 'name = "apps.vendas.subapps.pedidos.subapps.itens"' in conteudo
    assert '    "apps.vendas.subapps.pedidos.subapps.itens",\n' in (projeto / "api_settings.py").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "parent",
    ["vendas.subapps.pedidos", "vendas/subapps/pedidos", "apps.vendas.subapps.pedidos"],
)
def test_parent_como_caminho_explicito(projeto, parent):
    criar_app_falso(projeto / "apps" / "vendas" / "subapps" / "pedidos")

    call_command("start_api_app", "itens", "--parent", parent)

    assert (projeto / "apps" / "vendas" / "subapps" / "pedidos" / "subapps" / "itens" / "apps.py").is_file()


def test_parent_ambiguo_lista_os_candidatos(projeto):
    criar_app_falso(projeto / "apps" / "vendas" / "subapps" / "pedidos")
    criar_app_falso(projeto / "apps" / "estoque" / "subapps" / "pedidos")

    with pytest.raises(CommandError) as erro:
        call_command("start_api_app", "itens", "--parent", "pedidos")

    mensagem = str(erro.value)

    assert "apps/estoque/subapps/pedidos" in mensagem
    assert "apps/vendas/subapps/pedidos" in mensagem
    assert "--parent estoque.subapps.pedidos" in mensagem


def test_parent_inexistente_falha_alto(projeto):
    with pytest.raises(CommandError, match="não encontrado"):
        call_command("start_api_app", "pedidos", "--parent", "vendas")


def test_caminho_explicito_sem_apps_py_falha_alto(projeto):
    (projeto / "apps" / "vendas" / "subapps" / "pedidos").mkdir(parents=True)

    with pytest.raises(CommandError, match="não é um app"):
        call_command("start_api_app", "itens", "--parent", "vendas.subapps.pedidos")


def test_parent_invalido_nao_escreve_no_settings(projeto):
    original = (projeto / "api_settings.py").read_text(encoding="utf-8")

    with pytest.raises(CommandError):
        call_command("start_api_app", "pedidos", "--parent", "vendas")

    assert (projeto / "api_settings.py").read_text(encoding="utf-8") == original


def test_diretorios_ignorados_nao_viram_parent(projeto):
    criar_app_falso(projeto / "apps" / "vendas" / "tests" / "pedidos")

    with pytest.raises(CommandError, match="não encontrado"):
        call_command("start_api_app", "itens", "--parent", "pedidos")
