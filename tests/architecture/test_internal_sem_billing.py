"""Guarda da variante `internal`: assinaturas e faturamento não voltam por merge.

Arquivos novos que a branch `startup` criar dentro de `apps/assinaturas/`
entram no merge sem conflito. Estes testes falham se o billing reaparecer.
"""

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_ROOTS = ("api", "apps", "internal_frameworks", "utils", "scripts", "tests")
MODULOS_PROIBIDOS = ("apps.assinaturas", "django_checkouts")
ATRIBUTOS_PROIBIDOS = {"assinatura", "utilizacao_seats", "situacao_acesso"}
APPS_PROIBIDOS_EM_MIGRATIONS = {"assinaturas", "faturamento"}


def _arquivos_python():
    for raiz in PRODUCTION_ROOTS:
        yield from (REPOSITORY_ROOT / raiz).rglob("*.py")


def _relativo(caminho: Path) -> str:
    return caminho.relative_to(REPOSITORY_ROOT).as_posix()


def test_app_de_assinaturas_nao_existe():
    assert not (REPOSITORY_ROOT / "apps" / "assinaturas").exists()


def test_nenhum_modulo_importa_billing():
    violacoes = []
    for caminho in _arquivos_python():
        arvore = ast.parse(caminho.read_text(), filename=str(caminho))
        for no in ast.walk(arvore):
            if isinstance(no, ast.Import):
                modulos = [alias.name for alias in no.names]
            elif isinstance(no, ast.ImportFrom) and no.module:
                modulos = [no.module]
            else:
                continue
            violacoes.extend(f"{_relativo(caminho)}:{no.lineno} {modulo}" for modulo in modulos if modulo.startswith(MODULOS_PROIBIDOS))

    assert violacoes == []


def test_contexto_do_tenant_nao_le_estado_comercial():
    violacoes = []
    for caminho in _arquivos_python():
        arvore = ast.parse(caminho.read_text(), filename=str(caminho))
        for no in ast.walk(arvore):
            if isinstance(no, ast.Attribute) and no.attr in ATRIBUTOS_PROIBIDOS and isinstance(no.value, ast.Attribute) and no.value.attr == "tenant":
                violacoes.append(f"{_relativo(caminho)}:{no.lineno} tenant.{no.attr}")

    assert violacoes == []


def test_nenhuma_migration_depende_de_billing():
    padrao = re.compile(r"""\(\s*["'](?P<app>\w+)["']\s*,""")
    violacoes = []
    for caminho in (REPOSITORY_ROOT / "apps").rglob("migrations/*.py"):
        for match in padrao.finditer(caminho.read_text()):
            if match.group("app") in APPS_PROIBIDOS_EM_MIGRATIONS:
                violacoes.append(f"{_relativo(caminho)} -> {match.group('app')}")

    assert violacoes == []


def test_settings_nao_instalam_billing():
    from django.conf import settings

    assert not [app for app in settings.INSTALLED_APPS if app.startswith(MODULOS_PROIBIDOS)]
