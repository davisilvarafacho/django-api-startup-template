# `start_api_app` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Criar o management command `start_api_app`, que gera apps já no formato de `.ai/CONVENTIONS.md`, em qualquer profundidade de `subapps/`, e os registra em `BUSINESS_APPS`.

**Architecture:** O comando herda de `django.core.management.templates.TemplateCommand` (a mesma base do `startapp`) e aponta para um diretório de template próprio com arquivos `.py-tpl`. Isso reaproveita a validação de nome, a renderização e a checagem de conflito de arquivos do Django. O destino é calculado pelo comando (nunca vem do CLI) e o dotted path do app é injetado nas `options`, que o `TemplateCommand` insere no contexto do template.

**Tech Stack:** Django 5.2.8, Python 3.12, pytest + pytest-django, ruff, uv.

**Spec:** `.ai/brainstorming/spec/2026-07-29-start-api-app-design.md`

## Global Constraints

- Todo app gerado nasce dentro de `apps/` (`.ai/CONVENTIONS.md` §1.1). O comando nunca escreve na raiz do projeto.
- Módulos do app são arquivos `.py`; `tests` é **pacote** (§1.2).
- ruff: `line-length = 150`, `target-version = "py312"`, docstrings no estilo Google, isort com ordem de seções `future, standard-library, django, rest-framework, third-party, first-party, local-folder` e `known-first-party = ["apps"]`.
- ruff tem `T20` ativo: nada de `print()`. Saída do comando sai por `self.stdout.write`.
- ruff tem `RET` ativo: evite `x = ...; return x` (RET504).
- Testes são **DB-less** (§9): nenhum teste deste plano usa o marker `django_db`.
- Mensagens de commit seguem Conventional Commits (validado por commitlint no hook `commit-msg`).
- Strings em código usam aspas duplas (padrão do `ruff-format`).
- Aplicar `--parent` e localizar o bloco `BUSINESS_APPS` **antes** de escrever qualquer arquivo: falha não deixa app pela metade.
- Comando de teste: `uv run --group test pytest <caminho> -v`.

---

## File Structure

| Arquivo | Responsabilidade |
| --- | --- |
| `apps/api/core/management/commands/start_api_app.py` | O comando: resolução de destino, busca do app pai, e as duas funções de módulo (`settings_module_path`, `insert_business_app`) |
| `apps/api/core/management/commands/app_template/` | Template do `TemplateCommand`: a forma do app gerado, como arquivos reais |
| `apps/api/core/tests/test_start_api_app.py` | Testes do comando e das duas funções |

Um único módulo de comando é o certo aqui: as duas funções auxiliares existem só para servir esse comando, e separá-las em `utils/` criaria uma dependência sem nenhum segundo consumidor. O módulo fica em torno de 110 linhas.

A forma do app gerado vive no diretório de template, não em strings dentro do Python — é onde alguém vai olhar quando quiser mudar o scaffold.

---

### Task 1: As duas funções de módulo

O comando precisa de duas coisas antes de saber criar arquivo nenhum: onde está o `settings.py` do projeto, e como inserir uma linha em `BUSINESS_APPS`. As duas são independentes do `TemplateCommand` e testáveis sem tocar disco (a segunda) ou sem fixture nenhuma (a primeira).

**Files:**

- Create: `apps/api/core/management/commands/start_api_app.py`
- Test: `apps/api/core/tests/test_start_api_app.py`

**Interfaces:**

- Consumes: nada.
- Produces:
  - `APP_TEMPLATE_DIR: Path` — módulo-level, `Path(__file__).resolve().parent / "app_template"`.
  - `IGNORED_DIRS: frozenset[str]` — `{"__pycache__", "migrations", "tests"}`.
  - `settings_module_path() -> Path` — caminho do `settings.py` do projeto. Levanta `CommandError` se não localizar. É a costura que os testes das Tasks 2 e 3 substituem por `monkeypatch`.
  - `insert_business_app(source: str, dotted_path: str) -> str | None` — devolve o novo conteúdo do settings, ou `None` se `dotted_path` já estiver registrado. Levanta `CommandError` se o bloco `BUSINESS_APPS = [` não existir.

- [ ] **Step 1: Escreva os testes que falham**

Crie `apps/api/core/tests/test_start_api_app.py`:

```python
"""Testes do comando start_api_app (não tocam o banco)."""

from pathlib import Path

import pytest

from django.core.management.base import CommandError

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

    assert resultado == """BUSINESS_APPS = [
    "apps.api.autenticacao",
    "apps.api.base",
    "apps.api.core",
    "apps.faturamento",
    "apps.organizacoes",
    "apps.usuarios",
]
"""


def test_insere_no_fim_quando_e_o_ultimo_alfabeticamente():
    resultado = insert_business_app(SETTINGS_STUB, "apps.vendas")

    assert resultado.endswith("""    "apps.usuarios",
    "apps.vendas",
]
""")


def test_app_ja_registrado_devolve_none():
    assert insert_business_app(SETTINGS_STUB, "apps.usuarios") is None


def test_preserva_o_resto_do_arquivo():
    source = f'DEBUG = True\n\n{SETTINGS_STUB}\nMIDDLEWARE = []\n'

    resultado = insert_business_app(source, "apps.faturamento")

    assert resultado.startswith("DEBUG = True\n\n")
    assert resultado.endswith("]\n\nMIDDLEWARE = []\n")


def test_bloco_ausente_falha_alto():
    with pytest.raises(CommandError, match="BUSINESS_APPS"):
        insert_business_app("DEBUG = True\n", "apps.faturamento")
```

- [ ] **Step 2: Rode os testes para ver falhar**

Run: `uv run --group test pytest apps/api/core/tests/test_start_api_app.py -v`
Expected: FAIL na coleta — `ModuleNotFoundError: No module named 'apps.api.core.management.commands.start_api_app'`

- [ ] **Step 3: Escreva a implementação mínima**

Crie `apps/api/core/management/commands/start_api_app.py`:

```python
"""Cria um app da API já no formato exigido por `.ai/CONVENTIONS.md`."""

import re
from importlib.util import find_spec
from pathlib import Path

from django.conf import settings
from django.core.management.base import CommandError

APP_TEMPLATE_DIR = Path(__file__).resolve().parent / "app_template"

# Diretórios que nunca são um app; a busca por `--parent` não desce neles.
IGNORED_DIRS = frozenset({"__pycache__", "migrations", "tests"})

BUSINESS_APPS_BLOCK = re.compile(r"^BUSINESS_APPS = \[\n(?P<entries>.*?)^\]", re.MULTILINE | re.DOTALL)


def settings_module_path():
    """Localiza o arquivo de settings do projeto a partir de `DJANGO_SETTINGS_MODULE`.

    Returns:
        O caminho do arquivo de settings em uso.

    Raises:
        CommandError: Se o módulo de settings não tiver um arquivo no disco.
    """
    spec = find_spec(settings.SETTINGS_MODULE)
    if spec is None or spec.origin is None:
        raise CommandError(f"não foi possível localizar o arquivo de '{settings.SETTINGS_MODULE}'.")

    return Path(spec.origin)


def insert_business_app(source, dotted_path):
    """Insere `dotted_path` em `BUSINESS_APPS` na posição alfabética.

    As demais linhas da lista são preservadas byte a byte — nada de reordenação
    global do que já estava lá.

    Args:
        source: Conteúdo atual do arquivo de settings.
        dotted_path: Caminho pontilhado do app novo (ex.: `apps.vendas`).

    Returns:
        O novo conteúdo do arquivo, ou `None` se o app já estava registrado.

    Raises:
        CommandError: Se o bloco `BUSINESS_APPS = [` não existir no arquivo.
    """
    match = BUSINESS_APPS_BLOCK.search(source)
    if match is None:
        raise CommandError("bloco `BUSINESS_APPS = [` não encontrado no arquivo de settings.")

    entries = match.group("entries").splitlines()
    nova_linha = f'    "{dotted_path}",'
    if nova_linha in entries:
        return None

    posicao = len(entries)
    for indice, linha in enumerate(entries):
        if linha.strip().strip('",') > dotted_path:
            posicao = indice
            break

    entries.insert(posicao, nova_linha)
    bloco = "BUSINESS_APPS = [\n" + "\n".join(entries) + "\n]"
    return source[: match.start()] + bloco + source[match.end() :]
```

- [ ] **Step 4: Rode os testes para ver passar**

Run: `uv run --group test pytest apps/api/core/tests/test_start_api_app.py -v`
Expected: PASS, 6 testes.

- [ ] **Step 5: Rode o lint**

Run: `uv run ruff check apps/api/core/management/commands/start_api_app.py apps/api/core/tests/test_start_api_app.py && uv run ruff format --check apps/api/core/management/commands/start_api_app.py`
Expected: sem erros. Se o `ruff format` reclamar, rode-o sem `--check` para aplicar e siga.

- [ ] **Step 6: Commit**

```bash
git add apps/api/core/management/commands/start_api_app.py apps/api/core/tests/test_start_api_app.py
git commit -m "feat: adicionar helpers de registro em BUSINESS_APPS"
```

---

### Task 2: Template do app e criação em `apps/<nome>`

Deliverable: `python manage.py start_api_app vendas` cria `apps/vendas/` completo e registra o app. O `--parent` fica para a Task 3.

**Files:**

- Create: `apps/api/core/management/commands/app_template/__init__.py-tpl` (vazio)
- Create: `apps/api/core/management/commands/app_template/admin.py-tpl` (vazio)
- Create: `apps/api/core/management/commands/app_template/apps.py-tpl`
- Create: `apps/api/core/management/commands/app_template/docs.py-tpl` (vazio)
- Create: `apps/api/core/management/commands/app_template/filters.py-tpl` (vazio)
- Create: `apps/api/core/management/commands/app_template/handlers.py-tpl` (vazio)
- Create: `apps/api/core/management/commands/app_template/models.py-tpl` (vazio)
- Create: `apps/api/core/management/commands/app_template/serializers.py-tpl` (vazio)
- Create: `apps/api/core/management/commands/app_template/urls.py-tpl`
- Create: `apps/api/core/management/commands/app_template/views.py-tpl` (vazio)
- Create: `apps/api/core/management/commands/app_template/migrations/__init__.py-tpl` (vazio)
- Create: `apps/api/core/management/commands/app_template/tests/__init__.py-tpl` (vazio)
- Modify: `apps/api/core/management/commands/start_api_app.py` (adiciona a classe `Command`)
- Test: `apps/api/core/tests/test_start_api_app.py`

**Interfaces:**

- Consumes: `APP_TEMPLATE_DIR`, `settings_module_path()`, `insert_business_app()` da Task 1.
- Produces: `Command`, subclasse de `TemplateCommand`, com `handle(self, **options)`. A Task 3 acrescenta o método `find_parent_app` e o argumento `--parent`.

Notas sobre o `TemplateCommand` do Django 5.2 que ditam a implementação:

- `TemplateCommand.handle(app_or_project, name, target=None, **options)` — quando `target` é passado, o diretório **precisa já existir**, senão levanta "Destination directory does not exist". Então o comando faz `mkdir` antes de delegar.
- Por causa disso, a checagem de "destino já existe" **não** vem de graça (ela só existe no caminho em que `target is None`). O comando checa explicitamente. Comportamento observável é o mesmo da spec: `CommandError` e nenhum arquivo criado.
- `handle` monta o `Context` com `**options` dentro. Basta pôr `app_dotted_path` nas options para `{{ app_dotted_path }}` funcionar no template.
- `handle` lê `options["verbosity"]`, `options["extensions"]`, `options["files"]` e `options["template"]`. Como o `add_arguments` daqui **não** chama `super()` (para o CLI expor só `name` e `--parent`), `extensions`, `files` e `template` são definidos em `handle`.
- A chave `name` precisa ser removida das options antes do `super().handle(...)`, senão colide com o parâmetro posicional `name`.
- `git` não versiona diretório vazio, então `subapps/` não pode vir do template: é criado com `mkdir` depois da renderização.
- `run_formatters` procura o `black`, que não está instalado aqui — é no-op.

- [ ] **Step 1: Escreva os testes que falham**

Acrescente ao topo de `apps/api/core/tests/test_start_api_app.py` os imports novos (o bloco de imports fica assim):

```python
"""Testes do comando start_api_app (não tocam o banco)."""

from pathlib import Path

import pytest

from django.core.management import call_command
from django.core.management.base import CommandError

from apps.api.core.management.commands import start_api_app
from apps.api.core.management.commands.start_api_app import (
    insert_business_app,
    settings_module_path,
)
```

Acrescente a fixture e os testes ao fim do arquivo:

```python
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
```

- [ ] **Step 2: Rode os testes para ver falhar**

Run: `uv run --group test pytest apps/api/core/tests/test_start_api_app.py -v`
Expected: FAIL — `CommandError: Unknown command: 'start_api_app'`, porque o módulo ainda não define a classe `Command`.

- [ ] **Step 3: Crie os arquivos vazios do template**

```bash
mkdir -p apps/api/core/management/commands/app_template/migrations apps/api/core/management/commands/app_template/tests
touch apps/api/core/management/commands/app_template/__init__.py-tpl \
      apps/api/core/management/commands/app_template/admin.py-tpl \
      apps/api/core/management/commands/app_template/docs.py-tpl \
      apps/api/core/management/commands/app_template/filters.py-tpl \
      apps/api/core/management/commands/app_template/handlers.py-tpl \
      apps/api/core/management/commands/app_template/models.py-tpl \
      apps/api/core/management/commands/app_template/serializers.py-tpl \
      apps/api/core/management/commands/app_template/views.py-tpl \
      apps/api/core/management/commands/app_template/migrations/__init__.py-tpl \
      apps/api/core/management/commands/app_template/tests/__init__.py-tpl
```

- [ ] **Step 4: Escreva os dois templates com conteúdo**

`apps/api/core/management/commands/app_template/apps.py-tpl`:

```python
from django.apps import AppConfig


class {{ camel_case_app_name }}Config(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "{{ app_dotted_path }}"
```

`apps/api/core/management/commands/app_template/urls.py-tpl`:

```python
from django.urls import include, path

from rest_framework.routers import DefaultRouter

router = DefaultRouter()

urlpatterns = [
    path("", include(router.urls)),
]
```

- [ ] **Step 5: Acrescente a classe `Command`**

No fim de `apps/api/core/management/commands/start_api_app.py`, e o import do `TemplateCommand` junto dos outros imports do django:

```python
from django.core.management.templates import TemplateCommand
```

```python
class Command(TemplateCommand):
    help = "Cria um app da API já no formato exigido pela convenção do projeto."
    missing_args_message = "Informe o nome do app."

    def add_arguments(self, parser):
        parser.add_argument("name", help="Nome do app novo.")

    def handle(self, **options):
        app_name = options.pop("name")

        base_dir = Path(settings.BASE_DIR)
        destination = base_dir / "apps" / app_name

        if destination.exists():
            raise CommandError(f"'{destination}' já existe.")

        dotted_path = ".".join(destination.relative_to(base_dir).parts)

        # Antes de criar arquivo nenhum: um app que não entra em BUSINESS_APPS é
        # um app órfão, pior que nenhum app.
        caminho_settings = settings_module_path()
        source = caminho_settings.read_text(encoding="utf-8")
        registrado = insert_business_app(source, dotted_path)

        destination.mkdir(parents=True)

        options["template"] = str(APP_TEMPLATE_DIR)
        options["extensions"] = ["py"]
        options["files"] = []
        options["app_dotted_path"] = dotted_path
        super().handle("app", app_name, str(destination), **options)

        # `subapps/` não pode vir do template: git não versiona diretório vazio.
        (destination / "subapps").mkdir()

        if registrado is None:
            self.stdout.write(self.style.WARNING(f"'{dotted_path}' já estava em BUSINESS_APPS."))
        else:
            caminho_settings.write_text(registrado, encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(f"'{dotted_path}' registrado em BUSINESS_APPS."))

        self.stdout.write(self.style.SUCCESS(f"App criado em {destination.relative_to(base_dir)}/"))
```

- [ ] **Step 6: Rode os testes para ver passar**

Run: `uv run --group test pytest apps/api/core/tests/test_start_api_app.py -v`
Expected: PASS, 14 testes.

- [ ] **Step 7: Rode o lint e a suíte inteira**

Run: `uv run ruff check . && uv run --group test pytest -q`
Expected: lint sem erros; suíte no mesmo estado de antes da task (nenhum teste novo quebrado). O diretório `app_template/` não é coletado pelo pytest nem pelo ruff — os arquivos são `.py-tpl`.

- [ ] **Step 8: Commit**

```bash
git add apps/api/core/management/commands/app_template apps/api/core/management/commands/start_api_app.py apps/api/core/tests/test_start_api_app.py
git commit -m "feat: adicionar comando start_api_app"
```

---

### Task 3: `--parent` em qualquer profundidade

Deliverable: `start_api_app itens --parent pedidos` acha o app `pedidos` em qualquer nível de `apps/` e cria `itens` dentro do `subapps/` dele, criando esse `subapps/` se não existir.

**Files:**

- Modify: `apps/api/core/management/commands/start_api_app.py`
- Test: `apps/api/core/tests/test_start_api_app.py`

**Interfaces:**

- Consumes: `IGNORED_DIRS` (Task 1) e a classe `Command` (Task 2).
- Produces: `Command.find_parent_app(self, apps_root: Path, parent: str) -> Path` — devolve o diretório do app pai. Levanta `CommandError` quando não acha, quando o nome é ambíguo, ou quando o caminho explícito não tem `apps.py`.

Regra de resolução, como na spec: se `parent` contém `.` ou `/`, é caminho explícito relativo a `apps/` (aceitando os dois separadores e descartando um prefixo `apps.`/`apps/`); senão, é buscado pelo nome entre os diretórios que contêm `apps.py`.

- [ ] **Step 1: Escreva os testes que falham**

Acrescente ao fim de `apps/api/core/tests/test_start_api_app.py`:

```python
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
```

- [ ] **Step 2: Rode os testes para ver falhar**

Run: `uv run --group test pytest apps/api/core/tests/test_start_api_app.py -v`
Expected: FAIL — `CommandError: Error: unrecognized arguments: --parent vendas`

- [ ] **Step 3: Adicione o argumento e a busca**

Em `apps/api/core/management/commands/start_api_app.py`, acrescente o argumento no `add_arguments`:

```python
    def add_arguments(self, parser):
        parser.add_argument("name", help="Nome do app novo.")
        parser.add_argument(
            "--parent",
            help="Nome do app pai (em qualquer profundidade) ou caminho relativo a apps/; o app novo nasce em <pai>/subapps/.",
        )
```

Acrescente o método `find_parent_app` à classe `Command`:

```python
    def find_parent_app(self, apps_root, parent):
        """Resolve o diretório do app pai a partir do nome ou de um caminho explícito.

        Args:
            apps_root: O diretório `apps/` do projeto.
            parent: Nome do app pai, ou caminho relativo a `apps/` quando contém
                `.` ou `/`.

        Returns:
            O diretório do app pai.

        Raises:
            CommandError: Se o pai não existir, não for um app, ou se o nome
                corresponder a mais de um app.
        """
        if "." in parent or "/" in parent:
            relativo = parent.replace(".", "/").strip("/").removeprefix("apps/")
            candidato = apps_root / relativo
            if not (candidato / "apps.py").is_file():
                raise CommandError(f"'{parent}' não é um app: {candidato / 'apps.py'} não existe.")

            return candidato

        encontrados = sorted(
            achado.parent
            for achado in apps_root.rglob("apps.py")
            if achado.parent.name == parent and not IGNORED_DIRS.intersection(achado.relative_to(apps_root).parts)
        )
        if not encontrados:
            raise CommandError(f"app '{parent}' não encontrado em apps/.")

        if len(encontrados) > 1:
            candidatos = "\n".join(f"  {achado.relative_to(apps_root.parent)}" for achado in encontrados)
            sugestao = ".".join(encontrados[0].relative_to(apps_root).parts)
            raise CommandError(f"mais de um app chamado '{parent}':\n{candidatos}\nuse o caminho completo: --parent {sugestao}")

        return encontrados[0]
```

- [ ] **Step 4: Ligue o `--parent` no cálculo do destino**

Em `Command.handle`, troque o trecho que calcula `destination`:

```python
    def handle(self, **options):
        app_name = options.pop("name")
        parent = options.pop("parent")

        base_dir = Path(settings.BASE_DIR)
        apps_root = base_dir / "apps"

        if parent:
            parent_dir = self.find_parent_app(apps_root, parent)
            destination = parent_dir / "subapps" / app_name
        else:
            destination = apps_root / app_name
```

O resto de `handle` fica intacto: `destination.mkdir(parents=True)` já cria o `subapps/` intermediário quando ele não existe, e o `dotted_path` derivado do caminho relativo já funciona em qualquer profundidade.

- [ ] **Step 5: Rode os testes para ver passar**

Run: `uv run --group test pytest apps/api/core/tests/test_start_api_app.py -v`
Expected: PASS, 25 testes (os 14 anteriores + 11 desta task, contando os 3 casos do `parametrize`).

- [ ] **Step 6: Verifique o comando de verdade, fora do tmp_path**

Run: `uv run python manage.py start_api_app vendas && uv run python manage.py start_api_app pedidos --parent vendas`
Expected: dois apps criados em `apps/vendas/` e `apps/vendas/subapps/pedidos/`, e duas linhas novas em `api/settings.py`.

Confirme que o projeto sobe com os apps novos registrados:

Run: `uv run python manage.py check`
Expected: `System check identified no issues`.

Depois desfaça a verificação — ela não entra no commit:

```bash
rm -rf apps/vendas
git checkout api/settings.py
```

- [ ] **Step 7: Rode o lint e a suíte inteira**

Run: `uv run ruff check . && uv run --group test pytest -q`
Expected: lint sem erros, suíte verde. Confirme com `git status --short` que `apps/vendas` e a alteração em `api/settings.py` não sobraram do step anterior.

- [ ] **Step 8: Commit**

```bash
git add apps/api/core/management/commands/start_api_app.py apps/api/core/tests/test_start_api_app.py
git commit -m "feat: resolver app pai em qualquer profundidade no start_api_app"
```

---

### Task 4: Documentar o comando na convenção

A convenção é a fonte da verdade que o comando passa a automatizar. Sem esta task, quem lê `.ai/CONVENTIONS.md` continua criando apps na mão.

**Files:**

- Modify: `.ai/CONVENTIONS.md:14-38` (seções §1.1 e §1.2)

**Interfaces:**

- Consumes: o comando entregue nas Tasks 2 e 3.
- Produces: nada consumido por outra task.

- [ ] **Step 1: Acrescente a subseção do comando**

Em `.ai/CONVENTIONS.md`, logo após o bloco de árvore de diretórios que fecha a §1.2, insira o texto abaixo (a fence interna é uma fence `bash` de verdade no arquivo final):

````markdown
### 1.3. Criação de Apps

Apps **devem** ser criados pelo comando do projeto, não pelo `startapp` do Django:

```bash
python manage.py start_api_app vendas                    # apps/vendas/
python manage.py start_api_app pedidos --parent vendas   # apps/vendas/subapps/pedidos/
```

O comando cria a estrutura desta seção (incluindo `tests/` como pacote e um
`subapps/` para apps do mesmo domínio), e registra o app em `BUSINESS_APPS`. O
`--parent` aceita o nome de qualquer app já existente, em qualquer profundidade.
````

As subseções seguintes da §1 são renumeradas: a antiga §1.3 (QuerySets) passa a §1.4.

- [ ] **Step 2: Corrija as referências à seção renumerada**

Run: `grep -rn "§1.3\|1.3." .ai/CONVENTIONS.md docs/ apps/ --include="*.md" --include="*.py"`
Expected: revise cada ocorrência que aponte para "QuerySets" como §1.3 e passe para §1.4. Se o grep não achar nenhuma referência cruzada, siga.

- [ ] **Step 3: Commit**

```bash
git add .ai/CONVENTIONS.md
git commit -m "docs: documentar start_api_app na convenção de apps"
```

---

## Self-Review

**Cobertura da spec:**

| Requisito da spec | Task |
| --- | --- |
| Interface `start_api_app <nome> [--parent <app_pai>]` | 2 (nome), 3 (`--parent`) |
| Subclasse de `TemplateCommand` com template próprio | 2 |
| `app_dotted_path` injetado via `options` | 2 |
| Estrutura de 13 entradas, sem `dashboards.py` | 2 (`test_cria_a_estrutura_da_convencao`) |
| Módulos vazios; exceções `urls.py` e `apps.py` | 2 (`test_modulos_nascem_vazios`, `test_urls_py_traz_o_router`, `test_apps_py_traz_o_dotted_path`) |
| `AppConfig` sem `verbose_name` | 2 (asserção de igualdade exata do `apps.py`) |
| `subapps/` vazio, sem `__init__.py`, criado em código | 2 (`test_subapps_nasce_diretorio_vazio`), 3 (`test_parent_cria_o_subapps_ausente_sem_init`) |
| Busca do pai por nome em qualquer profundidade | 3 |
| `IGNORED_DIRS` na busca | 3 (`test_diretorios_ignorados_nao_viram_parent`) |
| Caminho explícito com `.`/`/` e prefixo `apps.` descartado | 3 (`parametrize` de `test_parent_como_caminho_explicito`) |
| Ambiguidade lista candidatos + sugere caminho | 3 (`test_parent_ambiguo_lista_os_candidatos`) |
| Dotted path = caminho relativo a `BASE_DIR` | 3 (`test_parent_de_segundo_nivel_gera_dotted_path_completo`) |
| Registro alfabético, idempotente, preservando linhas | 1 |
| Settings localizado por `DJANGO_SETTINGS_MODULE` | 1 |
| Falha antes de escrever arquivos | 2 (`test_destino_existente_falha_sem_escrever_nada`), 3 (`test_parent_invalido_nao_escreve_no_settings`) |
| Tabela de erros (6 linhas) | 1 (bloco ausente), 2 (destino existente; nome inválido vem do `validate_name`), 3 (pai inexistente, ambíguo, caminho sem `apps.py`) |
| Template em `.py-tpl`, comando ao lado do `rotate_sensitive_fields.py` | 2 |
| Testes DB-less, seam `settings_module_path` por `monkeypatch` | 1, 2 (fixture `projeto`) |

Uma linha da tabela de erros da spec — "nome inválido, keyword, ou colidindo com módulo importável" — não recebe teste próprio: é comportamento do `validate_name` do Django, já coberto pela suíte do próprio Django. Testá-lo aqui seria testar a biblioteca.

**Desvio da spec, deliberado:** a spec diz herdar de `startapp.Command`; o plano herda direto de `TemplateCommand`. O único conteúdo de `startapp.Command` é um `handle` que este comando precisa contornar (ele lê `directory` do CLI, que aqui não existe); herdar dele exigiria um `super(StartAppCommand, self).handle(...)` sem ganho nenhum. Todo o reuso citado na spec — `validate_name`, renderização, resolução do diretório de template — está em `TemplateCommand`.

**Ajuste na tabela de erros:** "diretório de destino já existe" não é herdado, como a spec supõe. O `TemplateCommand` só faz essa checagem no caminho em que nenhum `target` é passado, e aqui o `target` é sempre calculado. O comando checa explicitamente; o comportamento observável (`CommandError`, nada criado) é o que a spec descreve.

**Placeholders:** nenhum. Todo step de código traz o código; todo step de comando traz o comando e o resultado esperado.

**Consistência de tipos:** `settings_module_path()` devolve `Path` e é chamado sem argumento na Task 2 e substituído por `lambda: settings_falso` nos testes. `insert_business_app(source, dotted_path)` devolve `str | None` e a Task 2 trata o `None` no ramo do warning. `find_parent_app(apps_root, parent)` recebe e devolve `Path`, consistente entre o Step 3 e o Step 4 da Task 3. `APP_TEMPLATE_DIR` e `IGNORED_DIRS` são definidos na Task 1 e consumidos nas Tasks 2 e 3 com os mesmos nomes.
