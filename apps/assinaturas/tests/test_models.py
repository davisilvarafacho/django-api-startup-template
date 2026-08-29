from django.db import DatabaseError, IntegrityError, transaction
from django.utils import timezone

import pytest

from apps.assinaturas.features import CATALOGO_RECURSOS, ValoresRecursos
from apps.assinaturas.models import Periodicidade, Plano, PrecoPlano, VersaoPlano

pytestmark = pytest.mark.django_db


def criar_plano(*, codigo="gratuito"):
    return Plano.objects.create(
        codigo=codigo,
        nome=codigo.title(),
        descricao=f"Plano {codigo}.",
        visivel=True,
    )


def criar_versao(plano, *, numero=1, atual=True, publicada=True):
    return VersaoPlano.objects.create(
        plano=plano,
        numero=numero,
        atual=atual,
        seats_inclusos=1,
        limite_seats_trial=None,
        duracao_trial_dias=0,
        carencia_pagamento_dias=7,
        carencia_excesso_seats_dias=7,
        expansao_automatica_seats=False,
        recursos=ValoresRecursos(CATALOGO_RECURSOS, {"quantidade_projetos": 1}).materializar(),
        publicada_em=timezone.now() if publicada else None,
    )


def criar_preco(versao, *, periodicidade=Periodicidade.MENSAL, moeda="BRL"):
    return PrecoPlano.objects.create(
        versao_plano=versao,
        periodicidade=periodicidade,
        moeda=moeda,
        valor_base_centavos=0,
        valor_seat_centavos=0,
    )


def test_codigo_de_plano_e_unico_entre_registros_vivos():
    plano = criar_plano()

    with pytest.raises(IntegrityError), transaction.atomic():
        criar_plano()

    plano.delete()
    recriado = criar_plano()

    assert recriado.is_deleted is False


def test_numero_de_versao_e_unico_por_plano():
    plano = criar_plano()
    criar_versao(plano, atual=False)

    with pytest.raises(IntegrityError), transaction.atomic():
        criar_versao(plano, atual=False)


def test_plano_tem_no_maximo_uma_versao_atual_viva():
    plano = criar_plano()
    criar_versao(plano)

    with pytest.raises(IntegrityError), transaction.atomic():
        criar_versao(plano, numero=2)


def test_preco_e_unico_por_versao_periodicidade_e_moeda():
    versao = criar_versao(criar_plano())
    criar_preco(versao)

    with pytest.raises(IntegrityError), transaction.atomic():
        criar_preco(versao)


@pytest.mark.parametrize("moeda", ["brl", "BR", "BRLL", "B1L"])
def test_moeda_exige_tres_letras_maiusculas(moeda):
    versao = criar_versao(criar_plano())

    with pytest.raises(DatabaseError), transaction.atomic():
        criar_preco(versao, moeda=moeda)


@pytest.mark.parametrize("campo", ["valor_base_centavos", "valor_seat_centavos"])
def test_preco_nao_aceita_valor_negativo(campo):
    versao = criar_versao(criar_plano())
    dados = {
        "versao_plano": versao,
        "periodicidade": Periodicidade.MENSAL,
        "moeda": "BRL",
        "valor_base_centavos": 100,
        "valor_seat_centavos": 25,
        campo: -1,
    }

    with pytest.raises(IntegrityError), transaction.atomic():
        PrecoPlano.objects.create(**dados)


def test_termos_de_versao_publicada_sao_imutaveis_por_save():
    versao = criar_versao(criar_plano())
    versao.seats_inclusos = 2

    with pytest.raises(ValueError, match="Versão de plano publicada é imutável"):
        versao.save()

    versao.refresh_from_db()
    assert versao.seats_inclusos == 1


def test_termos_de_versao_publicada_sao_imutaveis_por_queryset_update():
    versao = criar_versao(criar_plano())

    with pytest.raises(ValueError, match="Versão de plano publicada é imutável"):
        VersaoPlano.objects.filter(pk=versao.pk).update(seats_inclusos=2)


def test_termos_de_versao_publicada_sao_imutaveis_por_bulk_update():
    versao = criar_versao(criar_plano())
    versao.seats_inclusos = 2

    with pytest.raises(ValueError, match="Versão de plano publicada é imutável"):
        VersaoPlano.objects.bulk_update([versao], ["seats_inclusos"])


def test_flags_operacionais_de_versao_publicada_podem_mudar():
    versao = criar_versao(criar_plano())
    versao.atual = False
    versao.is_active = False
    versao.save(update_fields=["atual", "is_active"])

    versao.refresh_from_db()
    assert versao.atual is False
    assert versao.is_active is False


def test_versao_publicada_nao_pode_ser_removida_logicamente():
    versao = criar_versao(criar_plano())

    with pytest.raises(ValueError, match="Versão de plano publicada é imutável"):
        versao.delete()

    versao.refresh_from_db()
    assert versao.is_deleted is False


def test_preco_de_versao_publicada_e_imutavel_por_save():
    preco = criar_preco(criar_versao(criar_plano()))
    preco.valor_base_centavos = 100

    with pytest.raises(ValueError, match="Preço de versão publicada é imutável"):
        preco.save()

    preco.refresh_from_db()
    assert preco.valor_base_centavos == 0


def test_preco_de_versao_publicada_e_imutavel_por_queryset_update():
    preco = criar_preco(criar_versao(criar_plano()))

    with pytest.raises(ValueError, match="Preço de versão publicada é imutável"):
        PrecoPlano.objects.filter(pk=preco.pk).update(valor_base_centavos=100)


def test_preco_de_versao_publicada_e_imutavel_por_bulk_update():
    preco = criar_preco(criar_versao(criar_plano()))
    preco.valor_seat_centavos = 100

    with pytest.raises(ValueError, match="Preço de versão publicada é imutável"):
        PrecoPlano.objects.bulk_update([preco], ["valor_seat_centavos"])


def test_flag_operacional_de_preco_publicado_pode_mudar():
    preco = criar_preco(criar_versao(criar_plano()))
    preco.is_active = False
    preco.save(update_fields=["is_active"])

    preco.refresh_from_db()
    assert preco.is_active is False


def test_preco_de_versao_publicada_nao_pode_ser_removido_logicamente():
    preco = criar_preco(criar_versao(criar_plano()))

    with pytest.raises(ValueError, match="Preço de versão publicada é imutável"):
        preco.delete()

    preco.refresh_from_db()
    assert preco.is_deleted is False
