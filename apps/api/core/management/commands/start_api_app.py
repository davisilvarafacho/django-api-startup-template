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
