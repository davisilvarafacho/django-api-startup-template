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
