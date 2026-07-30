"""Cria um app da API já no formato exigido por `.ai/CONVENTIONS.md`."""

import re
from importlib.util import find_spec
from pathlib import Path

from django.conf import settings
from django.core.management.base import CommandError
from django.core.management.templates import TemplateCommand

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

        base_dir = Path(settings.BASE_DIR)
        apps_root = base_dir / "apps"

        if parent:
            parent_dir = self.find_parent_app(apps_root, parent)
            destination = parent_dir / "subapps" / app_name
        else:
            destination = apps_root / app_name

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
