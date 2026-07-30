"""Cria um app da API já no formato exigido por `.ai/CONVENTIONS.md`."""

import keyword
import re
import shutil
from importlib.util import find_spec
from pathlib import Path

from django.conf import settings
from django.core.management.base import CommandError
from django.core.management.templates import TemplateCommand

APP_TEMPLATE_DIR = Path(__file__).resolve().parent / "app_template"

# Diretórios que nunca são um app; a busca por `--parent` não desce neles.
IGNORED_DIRS = frozenset({"__pycache__", "migrations", "tests"})

BUSINESS_APPS_BLOCK = re.compile(r"^BUSINESS_APPS = \[\n(?P<entries>.*?)^\]", re.MULTILINE | re.DOTALL)

# Uma entrada válida dentro do bloco: uma string entre aspas duplas, vírgula
# final, comentário opcional. Formatos fora disso são recusados em vez de
# adivinhados — ver `_parse_business_apps`.
ENTRY = re.compile(r'^\s*"([^"]+)",\s*(#.*)?$')


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


def _parse_business_apps(source):
    """Localiza o bloco `BUSINESS_APPS` e valida o formato de cada entrada.

    Recusa em vez de adivinhar: uma entrada fora do formato
    `    "apps.exemplo",` (aspas simples, sem vírgula, indentação com tabs
    misturada a comentários mal-formados etc.) derruba o comando em vez de
    produzir um bloco corrompido silenciosamente. Este repositório é um
    template — os settings gerados a partir dele não têm garantia de estarem
    normalizados pelo `ruff format`.

    Args:
        source: Conteúdo atual do arquivo de settings.

    Returns:
        Uma tupla `(match, entries, dotted_paths)`: o `re.Match` do bloco, a
        lista de linhas brutas dentro dele (preservadas para reconstrução
        byte a byte) e os dotted paths extraídos de cada entrada.

    Raises:
        CommandError: Se o bloco não existir no arquivo, ou se alguma linha
            não-vazia dentro dele não estiver no formato esperado.
    """
    match = BUSINESS_APPS_BLOCK.search(source)
    if match is None:
        raise CommandError("BUSINESS_APPS não encontrado no formato esperado (uma linha por app).")

    entries = match.group("entries").splitlines()
    linhas_com_conteudo = [linha for linha in entries if linha.strip()]
    if not all(ENTRY.match(linha) for linha in linhas_com_conteudo):
        raise CommandError("BUSINESS_APPS tem um formato inesperado; registre o app manualmente.")

    dotted_paths = [ENTRY.match(linha).group(1) for linha in linhas_com_conteudo]
    return match, entries, dotted_paths


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
        CommandError: Se o bloco `BUSINESS_APPS` não existir no arquivo, ou se
            alguma entrada dentro dele não estiver no formato esperado.
    """
    match, entries, dotted_paths = _parse_business_apps(source)

    if dotted_path in dotted_paths:
        return None

    posicao = len(entries)
    for indice, linha in enumerate(entries):
        if not linha.strip():
            continue
        if ENTRY.match(linha).group(1) > dotted_path:
            posicao = indice
            break

    entries.insert(posicao, f'    "{dotted_path}",')
    bloco = "BUSINESS_APPS = [\n" + "\n".join(entries) + "\n]"
    return source[: match.start()] + bloco + source[match.end() :]


def business_app_labels(source):
    """Mapeia cada label já registrado em `BUSINESS_APPS` ao seu dotted path.

    O label de um app é o último componente do dotted path — é como o Django
    deriva `AppConfig.label` quando ele não é definido explicitamente. Dois
    apps com dotted paths diferentes mas o mesmo último componente colidem no
    label e derrubam `manage.py check` com `ImproperlyConfigured`.

    Args:
        source: Conteúdo atual do arquivo de settings.

    Returns:
        Um dict `{label: dotted_path}` para cada app já registrado.

    Raises:
        CommandError: Se o bloco `BUSINESS_APPS` não existir no arquivo, ou se
            alguma entrada dentro dele não estiver no formato esperado.
    """
    _, _, dotted_paths = _parse_business_apps(source)
    return {caminho.rsplit(".", 1)[-1]: caminho for caminho in dotted_paths}


class Command(TemplateCommand):
    help = "Cria um app da API já no formato exigido pela convenção do projeto."
    missing_args_message = "Informe o nome do app."

    def add_arguments(self, parser):
        parser.add_argument("name", help="Nome do app novo.")
        parser.add_argument(
            "--parent",
            help="Nome do app pai (em qualquer profundidade) ou caminho relativo a apps/; o app novo nasce em <pai>/subapps/.",
        )

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

    def handle(self, **options):
        app_name = options.pop("name")
        parent = options.pop("parent")

        # `TemplateCommand.handle()` faz esta mesma checagem, mas só depois de
        # `mkdir` — tarde demais para evitar um diretório órfão (possivelmente
        # fora de apps/, se `name` viesse com `/` ou `..`). Valida aqui, antes
        # de qualquer path math.
        if not app_name.isidentifier() or keyword.iskeyword(app_name):
            raise CommandError(f"'{app_name}' não é um nome de app válido.")

        if parent is not None and not parent:
            raise CommandError("--parent não pode ser vazio; omita a opção para criar o app em apps/.")

        base_dir = Path(settings.BASE_DIR)
        apps_root = base_dir / "apps"

        if parent is not None:
            parent_dir = self.find_parent_app(apps_root, parent)
            destination = parent_dir / "subapps" / app_name
        else:
            destination = apps_root / app_name

        if destination.exists():
            raise CommandError(f"'{destination}' já existe.")

        dotted_path = ".".join(destination.relative_to(base_dir).parts)
        label = dotted_path.rsplit(".", 1)[-1]

        # Antes de criar arquivo nenhum: um app que não entra em BUSINESS_APPS é
        # um app órfão, pior que nenhum app. E um label duplicado derruba o
        # projeto inteiro (`ImproperlyConfigured: Application labels aren't
        # unique`), então também é rejeitado aqui — nunca depois da escrita.
        caminho_settings = settings_module_path()
        source = caminho_settings.read_text(encoding="utf-8")

        labels_existentes = business_app_labels(source)
        dotted_path_existente = labels_existentes.get(label)
        if dotted_path_existente is not None and dotted_path_existente != dotted_path:
            raise CommandError(f"'{dotted_path_existente}' já usa o label '{label}'; escolha outro nome para '{app_name}'.")

        registrado = insert_business_app(source, dotted_path)

        # --- a partir daqui, escreve no disco ---
        destination.mkdir(parents=True)
        try:
            options["template"] = str(APP_TEMPLATE_DIR)
            options["extensions"] = ["py"]
            options["files"] = []
            options["app_dotted_path"] = dotted_path
            super().handle("app", app_name, str(destination), **options)

            # `subapps/` não pode vir do template: git não versiona diretório vazio.
            (destination / "subapps").mkdir()
        except Exception:
            # `super().handle()` roda a validação completa do Django (nome
            # colidindo com módulo importável, por exemplo) só agora — depois
            # do `mkdir`. Se falhar aqui, o diretório não pode sobreviver.
            shutil.rmtree(destination, ignore_errors=True)
            raise

        if registrado is None:
            self.stdout.write(self.style.WARNING(f"'{dotted_path}' já estava em BUSINESS_APPS."))
        else:
            caminho_settings.write_text(registrado, encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(f"'{dotted_path}' registrado em BUSINESS_APPS."))

        self.stdout.write(self.style.SUCCESS(f"App criado em {destination.relative_to(base_dir)}/"))
