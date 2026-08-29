from dataclasses import FrozenInstanceError, replace

from django.utils import timezone

import pytest

from apps.assinaturas.catalogs import PLANOS_BOOTSTRAP, CatalogoPlanos, DefinicaoPrecoPlano
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
    antiga = VersaoPlano.objects.create(plano=plano, numero=1, atual=False, recursos={})
    atual = VersaoPlano.objects.create(plano=plano, numero=2, atual=False, recursos={})
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
    antiga.publicada_em = timezone.now()
    antiga.save(update_fields=["publicada_em"])
    atual.publicada_em = timezone.now()
    atual.atual = True
    atual.save(update_fields=["publicada_em", "atual"])

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


@pytest.mark.parametrize("periodicidade", [10, "10", True])
def test_definicao_preco_exige_periodicidade_concreta(periodicidade):
    with pytest.raises(ValueError, match="Periodicidade"):
        DefinicaoPrecoPlano(
            periodicidade=periodicidade,
            moeda="BRL",
            valor_base_centavos=100,
            valor_seat_centavos=10,
        )


@pytest.mark.parametrize("valor", [True, 1.9, "100", -1, 2**63])
def test_definicao_preco_exige_centavos_inteiros_no_intervalo_do_banco(valor):
    with pytest.raises(ValueError, match="centavos"):
        DefinicaoPrecoPlano(
            periodicidade=Periodicidade.MENSAL,
            moeda="BRL",
            valor_base_centavos=valor,
            valor_seat_centavos=10,
        )


def test_definicoes_exigem_booleanos_concretos():
    plano = PLANOS_BOOTSTRAP[0]
    versao = plano.versoes[0]
    preco = versao.precos[0]

    with pytest.raises(ValueError, match="is_active.*booleano"):
        replace(preco, is_active=1)
    with pytest.raises(ValueError, match="Booleanos da versão"):
        replace(versao, atual=1)
    with pytest.raises(ValueError, match="Booleanos do plano"):
        replace(plano, visivel=1)


def test_definicoes_exigem_dtos_e_tuplas_concretos():
    plano = PLANOS_BOOTSTRAP[0]
    versao = plano.versoes[0]

    with pytest.raises(ValueError, match="ValoresRecursos"):
        replace(versao, recursos={})
    with pytest.raises(ValueError, match="tupla de DefinicaoPrecoPlano"):
        replace(versao, precos=list(versao.precos))
    with pytest.raises(ValueError, match="tupla de DefinicaoVersaoPlano"):
        replace(plano, versoes=list(plano.versoes))


def test_definicoes_rejeitam_texto_e_inteiro_fora_dos_limites_dos_models():
    plano = PLANOS_BOOTSTRAP[0]
    versao = plano.versoes[0]

    with pytest.raises(ValueError, match="código"):
        replace(plano, codigo="x" * 61)
    with pytest.raises(ValueError, match="nome"):
        replace(plano, nome="x" * 151)
    with pytest.raises(ValueError, match="smallint"):
        replace(versao, seats_inclusos=32768)
