from dataclasses import FrozenInstanceError

from django.utils import timezone

import pytest

from apps.assinaturas.catalogs import PLANOS_BOOTSTRAP, CatalogoPlanos
from apps.assinaturas.models import Periodicidade, Plano, PrecoPlano, VersaoPlano

pytestmark = pytest.mark.django_db


def test_bootstrap_declara_plano_gratuito_v1_e_pago_sem_referencia_remota():
    assert [plano.codigo for plano in PLANOS_BOOTSTRAP] == ["gratuito", "profissional"]

    gratuito, profissional = PLANOS_BOOTSTRAP
    assert gratuito.versoes[0].numero == 1
    assert {preco.valor_base_centavos for preco in gratuito.versoes[0].precos} == {0}
    assert profissional.versoes[0].precos[0].valor_base_centavos > 0
    assert not hasattr(profissional.versoes[0].precos[0], "identificador_externo")
    assert not hasattr(profissional.versoes[0].precos[0], "stripe_price_id")


def test_definicoes_do_bootstrap_sao_imutaveis():
    with pytest.raises(FrozenInstanceError):
        PLANOS_BOOTSTRAP[0].nome = "Outro nome"


def test_catalogo_planos_resolve_versao_atual_e_preco_ativos():
    plano = Plano.objects.create(codigo="pago", nome="Pago", descricao="", visivel=True)
    antiga = VersaoPlano.objects.create(plano=plano, numero=1, atual=False, recursos={}, publicada_em=timezone.now())
    atual = VersaoPlano.objects.create(plano=plano, numero=2, atual=True, recursos={}, publicada_em=timezone.now())
    PrecoPlano.objects.create(
        versao_plano=antiga,
        periodicidade=Periodicidade.MENSAL,
        moeda="BRL",
        valor_base_centavos=100,
        valor_seat_centavos=0,
    )
    preco_atual = PrecoPlano.objects.create(
        versao_plano=atual,
        periodicidade=Periodicidade.MENSAL,
        moeda="BRL",
        valor_base_centavos=200,
        valor_seat_centavos=0,
    )

    versao_resolvida, preco_resolvido = CatalogoPlanos.obter_versao_inicial(
        codigo="pago",
        periodicidade=Periodicidade.MENSAL,
    )

    assert versao_resolvida == atual
    assert preco_resolvido == preco_atual


def test_catalogo_planos_nao_resolve_plano_inativo():
    Plano.objects.create(codigo="inativo", nome="Inativo", descricao="", visivel=True, is_active=False)

    with pytest.raises(Plano.DoesNotExist):
        CatalogoPlanos.obter_versao_inicial(codigo="inativo", periodicidade=Periodicidade.MENSAL)
