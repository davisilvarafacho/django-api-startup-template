from dataclasses import replace
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError

import pytest

from apps.assinaturas.catalogs import PLANOS_BOOTSTRAP
from apps.assinaturas.models import Plano, PrecoPlano, VersaoPlano

pytestmark = pytest.mark.django_db


def executar_sync(*args):
    stdout = StringIO()
    call_command("sync_plans", *args, stdout=stdout)
    return stdout.getvalue()


def test_sync_plans_e_dry_run_por_padrao():
    saida = executar_sync()

    assert Plano.objects.count() == 0
    assert "DRY-RUN" in saida
    assert "criaria plano 'gratuito'" in saida
    assert "criaria plano 'profissional'" in saida


def test_sync_plans_apply_cria_catalogo_declarado():
    saida = executar_sync("--apply")

    assert list(Plano.objects.order_by("codigo").values_list("codigo", flat=True)) == ["gratuito", "profissional"]
    assert VersaoPlano.objects.count() == 2
    assert PrecoPlano.objects.count() == 4
    assert VersaoPlano.objects.filter(atual=True, publicada_em__isnull=False).count() == 2
    assert "APLICADO" in saida


def test_sync_plans_apply_repetido_e_idempotente():
    executar_sync("--apply")
    ids_antes = (
        tuple(Plano.objects.order_by("pk").values_list("pk", flat=True)),
        tuple(VersaoPlano.objects.order_by("pk").values_list("pk", flat=True)),
        tuple(PrecoPlano.objects.order_by("pk").values_list("pk", flat=True)),
    )

    saida = executar_sync("--apply")

    ids_depois = (
        tuple(Plano.objects.order_by("pk").values_list("pk", flat=True)),
        tuple(VersaoPlano.objects.order_by("pk").values_list("pk", flat=True)),
        tuple(PrecoPlano.objects.order_by("pk").values_list("pk", flat=True)),
    )
    assert ids_depois == ids_antes
    assert "sem alterações" in saida


def test_sync_plans_recusa_mudar_conteudo_da_mesma_versao(monkeypatch):
    executar_sync("--apply")
    gratuito = PLANOS_BOOTSTRAP[0]
    versao_alterada = replace(gratuito.versoes[0], seats_inclusos=999)
    catalogo_alterado = (replace(gratuito, versoes=(versao_alterada,)), PLANOS_BOOTSTRAP[1])
    monkeypatch.setattr("apps.assinaturas.management.commands.sync_plans.PLANOS_BOOTSTRAP", catalogo_alterado)

    with pytest.raises(CommandError, match="declare o próximo número"):
        executar_sync("--apply")

    assert VersaoPlano.objects.get(plano__codigo="gratuito", numero=1).seats_inclusos != 999


def test_sync_plans_muda_versao_atual_sem_mutar_a_publicada(monkeypatch):
    executar_sync("--apply")
    gratuito = PLANOS_BOOTSTRAP[0]
    versao_1 = replace(gratuito.versoes[0], atual=False)
    versao_2 = replace(gratuito.versoes[0], numero=2, atual=True, seats_inclusos=2)
    catalogo_v2 = (replace(gratuito, versoes=(versao_1, versao_2)), PLANOS_BOOTSTRAP[1])
    monkeypatch.setattr("apps.assinaturas.management.commands.sync_plans.PLANOS_BOOTSTRAP", catalogo_v2)

    executar_sync("--apply")

    versoes = VersaoPlano.objects.filter(plano__codigo="gratuito").order_by("numero")
    assert list(versoes.values_list("numero", "atual", "seats_inclusos")) == [
        (1, False, 1),
        (2, True, 2),
    ]
