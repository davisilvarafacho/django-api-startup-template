"""Rollout seguro das assinaturas gratuitas para organizações existentes."""

from io import StringIO

from django.core.management import CommandError, call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext

import pytest

from apps.assinaturas.catalogs import PLANOS_BOOTSTRAP, sincronizar_planos
from apps.assinaturas.checks import organizacoes_ativas_sem_assinatura_check
from apps.assinaturas.models import AssinaturaOrganizacao
from apps.assinaturas.subscriptions import Assinaturas
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Organizacao

from .test_subscription_access_transitions import _assinatura_ativa

pytestmark = pytest.mark.django_db


def _command(*args):
    stdout = StringIO()
    call_command("initialize_subscriptions", *args, stdout=stdout)
    return stdout.getvalue()


def test_dry_run_e_padrao_e_nao_persiste_contratos():
    sincronizar_planos(PLANOS_BOOTSTRAP, aplicar=True)
    organizacoes = [Organizacao.objects.create(nome=f"Legada {indice}", slug=f"legada-dry-{indice}") for indice in range(2)]

    output = _command("--batch-size", "1")

    assert "DRY-RUN" in output
    assert "2 organização(ões)" in output
    for organizacao in organizacoes:
        with organizacao_atual_privilegiada(organizacao.pk):
            assert AssinaturaOrganizacao.all_objects.filter(organizacao=organizacao).exists() is False


def test_apply_inicializa_todas_em_lotes_e_repeticao_e_idempotente():
    sincronizar_planos(PLANOS_BOOTSTRAP, aplicar=True)
    organizacoes = [Organizacao.objects.create(nome=f"Legada {indice}", slug=f"legada-apply-{indice}") for indice in range(3)]

    primeira = _command("--apply", "--batch-size", "1")
    repetida = _command("--apply", "--batch-size", "1")

    assert "APLICADO" in primeira
    assert "3 organização(ões)" in primeira
    assert "0 organização(ões)" in repetida
    for organizacao in organizacoes:
        with organizacao_atual_privilegiada(organizacao.pk):
            assinatura = Assinaturas.obter_corrente(organizacao)
        assert assinatura is not None
        assert assinatura.versao_plano.plano.codigo == "gratuito"


def test_apply_nao_sobrescreve_contrato_corrente_nem_inicializa_organizacao_inativa():
    sincronizar_planos(PLANOS_BOOTSTRAP, aplicar=True)
    organizacao_paga, assinatura_paga = _assinatura_ativa(slug="rollout-paga")
    inativa = Organizacao.objects.create(nome="Inativa", slug="rollout-inativa", is_active=False)

    output = _command("--apply")

    assert "0 organização(ões)" in output
    with organizacao_atual_privilegiada(organizacao_paga.pk):
        assinatura_paga.refresh_from_db()
        assert AssinaturaOrganizacao.all_objects.filter(organizacao=organizacao_paga).count() == 1
    with organizacao_atual_privilegiada(inativa.pk):
        assert AssinaturaOrganizacao.all_objects.filter(organizacao=inativa).exists() is False


def test_comando_exige_catalogo_gratuito_e_lote_positivo():
    Organizacao.objects.create(nome="Sem catálogo", slug="rollout-sem-catalogo")

    with pytest.raises(CommandError, match="sync_plans"):
        _command("--apply")

    sincronizar_planos(PLANOS_BOOTSTRAP, aplicar=True)
    with pytest.raises(CommandError, match="positivo"):
        _command("--apply", "--batch-size", "0")


def test_system_check_deploy_detecta_lacuna_em_uma_unica_query_e_ignora_inativas():
    sincronizar_planos(PLANOS_BOOTSTRAP, aplicar=True)
    ativa = Organizacao.objects.create(nome="Ativa sem assinatura", slug="check-rollout-ativa")
    Organizacao.objects.create(nome="Inativa sem assinatura", slug="check-rollout-inativa", is_active=False)

    with CaptureQueriesContext(connection) as queries:
        erros = organizacoes_ativas_sem_assinatura_check(None)

    assert len(queries) == 1
    assert [erro.id for erro in erros] == ["assinaturas.E003"]

    _command("--apply")
    assert organizacoes_ativas_sem_assinatura_check(None) == []
    with organizacao_atual_privilegiada(ativa.pk):
        assert Assinaturas.obter_corrente(ativa) is not None
