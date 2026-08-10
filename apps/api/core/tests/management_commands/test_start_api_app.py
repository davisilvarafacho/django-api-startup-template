"""Testes do comando start_api_app (não tocam o banco)."""

from django.core.management import call_command
from django.core.management.base import CommandError

import pytest

from apps.api.core.management.commands import start_api_app
from apps.api.core.management.commands.start_api_app import (
    business_app_labels,
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


def test_bloco_vazio_em_uma_linha_nao_e_confundido_com_bloco_ausente():
    # `BUSINESS_APPS = []` é um formato plausível num settings de template; a
    # mensagem de erro não pode dar a entender que o bloco simplesmente não
    # está lá, quando ele está bem ali só que numa forma que o comando não
    # sabe editar.
    with pytest.raises(CommandError, match="formato esperado"):
        insert_business_app("BUSINESS_APPS = []\n", "apps.faturamento")


def test_entrada_sem_virgula_final_falha_em_vez_de_corromper():
    # Reprodução exata do bug relatado: sem a validação, a última entrada sem
    # vírgula vira concatenação implícita de string com a entrada nova.
    source = 'BUSINESS_APPS = [\n    "apps.usuarios"\n]\n'

    with pytest.raises(CommandError, match="BUSINESS_APPS"):
        insert_business_app(source, "apps.zebra")


def test_entrada_com_aspas_simples_falha_em_vez_de_duplicar():
    source = "BUSINESS_APPS = [\n    'apps.usuarios',\n]\n"

    with pytest.raises(CommandError, match="BUSINESS_APPS"):
        insert_business_app(source, "apps.usuarios")


def test_entrada_com_comentario_final_e_aceita_e_ordenada_corretamente():
    source = 'BUSINESS_APPS = [\n    "apps.organizacoes",  # legado\n    "apps.usuarios",\n]\n'

    resultado = insert_business_app(source, "apps.faturamento")

    assert resultado == ('BUSINESS_APPS = [\n    "apps.faturamento",\n    "apps.organizacoes",  # legado\n    "apps.usuarios",\n]\n')


def test_business_app_labels_mapeia_label_ao_dotted_path():
    assert business_app_labels(SETTINGS_STUB) == {
        "autenticacao": "apps.api.autenticacao",
        "base": "apps.api.base",
        "core": "apps.api.core",
        "organizacoes": "apps.organizacoes",
        "usuarios": "apps.usuarios",
    }


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


@pytest.mark.parametrize(
    "nome",
    [
        "123abc",  # não é identificador válido
        "class",  # keyword: isidentifier() é True, mas não é nome de app válido
        "../../fora/evil",  # tentativa de path traversal
        "/tmp/evil",  # caminho absoluto
        "com espaco",
    ],
)
def test_nome_invalido_nao_cria_nada_nem_altera_settings(projeto, nome):
    original = (projeto / "api_settings.py").read_text(encoding="utf-8")

    with pytest.raises(CommandError):
        call_command("start_api_app", nome)

    assert list((projeto / "apps").iterdir()) == []
    assert (projeto / "api_settings.py").read_text(encoding="utf-8") == original


def test_nome_colidindo_com_modulo_importavel_nao_deixa_diretorio_orfao(projeto):
    # `json` só é rejeitado dentro de `super().handle()`, depois do `mkdir` —
    # exatamente o caso em que a limpeza em `except Exception` precisa agir.
    with pytest.raises(CommandError):
        call_command("start_api_app", "json")

    assert not (projeto / "apps" / "json").exists()


def test_bloco_business_apps_ausente_via_call_command_nao_cria_nada(projeto):
    (projeto / "api_settings.py").write_text("DEBUG = True\n", encoding="utf-8")

    with pytest.raises(CommandError, match="BUSINESS_APPS"):
        call_command("start_api_app", "vendas")

    assert not (projeto / "apps" / "vendas").exists()
    assert (projeto / "api_settings.py").read_text(encoding="utf-8") == "DEBUG = True\n"


def test_business_apps_malformado_via_call_command_nao_cria_nada(projeto):
    original = 'BUSINESS_APPS = [\n    "apps.usuarios"\n]\n'
    (projeto / "api_settings.py").write_text(original, encoding="utf-8")

    with pytest.raises(CommandError, match="BUSINESS_APPS"):
        call_command("start_api_app", "vendas")

    assert not (projeto / "apps" / "vendas").exists()
    assert (projeto / "api_settings.py").read_text(encoding="utf-8") == original


def test_registro_ja_existente_apenas_avisa_e_cria_o_app(projeto, capsys):
    settings_com_vendas = SETTINGS_STUB.replace(
        '    "apps.usuarios",\n',
        '    "apps.usuarios",\n    "apps.vendas",\n',
    )
    (projeto / "api_settings.py").write_text(settings_com_vendas, encoding="utf-8")

    call_command("start_api_app", "vendas")

    assert (projeto / "apps" / "vendas" / "apps.py").is_file()
    assert (projeto / "api_settings.py").read_text(encoding="utf-8") == settings_com_vendas
    assert "já estava em BUSINESS_APPS" in capsys.readouterr().out


def test_label_duplicado_falha_alto_e_nao_escreve_nada(projeto):
    criar_app_falso(projeto / "apps" / "vendas")
    criar_app_falso(projeto / "apps" / "estoque")
    call_command("start_api_app", "pedidos", "--parent", "vendas")
    original = (projeto / "api_settings.py").read_text(encoding="utf-8")

    with pytest.raises(CommandError, match="pedidos"):
        call_command("start_api_app", "pedidos", "--parent", "estoque")

    assert not (projeto / "apps" / "estoque" / "subapps" / "pedidos").exists()
    assert (projeto / "api_settings.py").read_text(encoding="utf-8") == original


def test_diretorio_existente_recebe_a_estrutura(projeto):
    destino = projeto / "apps" / "vendas"
    destino.mkdir()

    call_command("start_api_app", "vendas", str(destino))

    assert (destino / "apps.py").is_file()
    assert (destino / "tests" / "__init__.py").is_file()
    assert (destino / "subapps").is_dir()
    assert '    "apps.vendas",\n' in (projeto / "api_settings.py").read_text(encoding="utf-8")


def test_diretorio_existente_preserva_o_que_ja_estava_la(projeto):
    destino = projeto / "apps" / "vendas"
    destino.mkdir()
    (destino / "README.md").write_text("anotações", encoding="utf-8")

    call_command("start_api_app", "vendas", str(destino))

    assert (destino / "README.md").read_text(encoding="utf-8") == "anotações"


def test_diretorio_aninhado_gera_o_dotted_path_completo(projeto):
    destino = projeto / "apps" / "vendas" / "subapps" / "pedidos"
    destino.mkdir(parents=True)

    call_command("start_api_app", "pedidos", str(destino))

    assert 'name = "apps.vendas.subapps.pedidos"' in (destino / "apps.py").read_text(encoding="utf-8")
    assert '    "apps.vendas.subapps.pedidos",\n' in (projeto / "api_settings.py").read_text(encoding="utf-8")


def test_diretorio_relativo_ao_cwd(projeto, monkeypatch):
    (projeto / "apps" / "vendas").mkdir()
    monkeypatch.chdir(projeto)

    call_command("start_api_app", "vendas", "apps/vendas")

    assert (projeto / "apps" / "vendas" / "apps.py").is_file()


def test_diretorio_inexistente_falha_alto_e_nao_escreve_nada(projeto):
    original = (projeto / "api_settings.py").read_text(encoding="utf-8")

    with pytest.raises(CommandError, match="não existe"):
        call_command("start_api_app", "vendas", str(projeto / "apps" / "vendas"))

    assert not (projeto / "apps" / "vendas").exists()
    assert (projeto / "api_settings.py").read_text(encoding="utf-8") == original


def test_diretorio_vazio_falha_alto(projeto):
    with pytest.raises(CommandError, match="não pode ser vazio"):
        call_command("start_api_app", "vendas", "")

    assert not (projeto / "apps" / "vendas").exists()


def test_diretorio_fora_de_apps_falha_alto(projeto):
    fora = projeto / "fora"
    fora.mkdir()

    with pytest.raises(CommandError, match="fora de apps/"):
        call_command("start_api_app", "vendas", str(fora))

    assert list(fora.iterdir()) == []


def test_o_proprio_apps_nao_serve_como_destino(projeto):
    with pytest.raises(CommandError, match="fora de apps/"):
        call_command("start_api_app", "vendas", str(projeto / "apps"))


def test_componente_do_caminho_que_nao_e_modulo_falha_alto(projeto):
    destino = projeto / "apps" / "meu-dominio" / "vendas"
    destino.mkdir(parents=True)

    with pytest.raises(CommandError, match="meu-dominio"):
        call_command("start_api_app", "vendas", str(destino))

    assert list(destino.iterdir()) == []


def test_diretorio_com_parent_falha_alto(projeto):
    criar_app_falso(projeto / "apps" / "vendas")
    destino = projeto / "apps" / "vendas" / "subapps" / "pedidos"
    destino.mkdir(parents=True)

    with pytest.raises(CommandError, match="apenas um dos dois"):
        call_command("start_api_app", "pedidos", str(destino), "--parent", "vendas")


def test_arquivo_conflitante_no_destino_falha_sem_registrar(projeto):
    destino = projeto / "apps" / "vendas"
    destino.mkdir()
    (destino / "models.py").write_text("# meu model\n", encoding="utf-8")
    original = (projeto / "api_settings.py").read_text(encoding="utf-8")

    with pytest.raises(CommandError, match="models.py"):
        call_command("start_api_app", "vendas", str(destino))

    assert (destino / "models.py").read_text(encoding="utf-8") == "# meu model\n"
    assert (projeto / "api_settings.py").read_text(encoding="utf-8") == original


def test_falha_com_destino_explicito_nao_apaga_o_diretorio(projeto):
    # `json` colide com um módulo importável e só é rejeitado dentro do
    # `super().handle()`; a limpeza do `except` não pode levar junto um
    # diretório que já era do usuário.
    destino = projeto / "apps" / "json"
    destino.mkdir()
    (destino / "README.md").write_text("anotações", encoding="utf-8")

    with pytest.raises(CommandError):
        call_command("start_api_app", "json", str(destino))

    assert (destino / "README.md").read_text(encoding="utf-8") == "anotações"


def test_parent_vazio_falha_alto_e_nao_escreve_nada(projeto):
    original = (projeto / "api_settings.py").read_text(encoding="utf-8")

    with pytest.raises(CommandError, match="--parent"):
        call_command("start_api_app", "vendas", "--parent", "")

    assert not (projeto / "apps" / "vendas").exists()
    assert (projeto / "api_settings.py").read_text(encoding="utf-8") == original
