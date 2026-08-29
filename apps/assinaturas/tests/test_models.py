from concurrent.futures import ThreadPoolExecutor
from threading import Event

from django.db import DatabaseError, IntegrityError, close_old_connections, connection, connections, transaction
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


def criar_versao(plano, *, numero=1, atual=None, publicada=True):
    if atual is None:
        atual = publicada
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


def criar_preco_publicado(*, codigo="publicado"):
    versao = criar_versao(criar_plano(codigo=codigo), publicada=False)
    preco = criar_preco(versao)
    versao.publicada_em = timezone.now()
    versao.save(update_fields=["publicada_em"])
    return versao, preco


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


@pytest.mark.parametrize(
    "campos",
    [
        {"publicada_em": None},
        {"publicada_em": timezone.now(), "is_deleted": True},
    ],
)
def test_versao_atual_deve_estar_publicada_e_viva(campos):
    plano = criar_plano()

    with pytest.raises(IntegrityError), transaction.atomic():
        VersaoPlano.objects.create(
            plano=plano,
            numero=1,
            atual=True,
            recursos={},
            **campos,
        )

    assert VersaoPlano.all_objects.filter(plano=plano).exists() is False


def test_preco_e_unico_por_versao_periodicidade_e_moeda():
    versao = criar_versao(criar_plano(), publicada=False)
    criar_preco(versao)

    with pytest.raises(IntegrityError), transaction.atomic():
        criar_preco(versao)


@pytest.mark.parametrize("moeda", ["brl", "BR", "BRLL", "B1L"])
def test_moeda_exige_tres_letras_maiusculas(moeda):
    versao = criar_versao(criar_plano(), publicada=False)

    with pytest.raises(DatabaseError), transaction.atomic():
        criar_preco(versao, moeda=moeda)


@pytest.mark.parametrize("campo", ["valor_base_centavos", "valor_seat_centavos"])
def test_preco_nao_aceita_valor_negativo(campo):
    versao = criar_versao(criar_plano(), publicada=False)
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
    _, preco = criar_preco_publicado(codigo="preco-save")
    preco.valor_base_centavos = 100

    with pytest.raises(ValueError, match="Preço de versão publicada é imutável"):
        preco.save()

    preco.refresh_from_db()
    assert preco.valor_base_centavos == 0


def test_preco_de_versao_publicada_e_imutavel_por_queryset_update():
    _, preco = criar_preco_publicado(codigo="preco-update")

    with pytest.raises(ValueError, match="Preço de versão publicada é imutável"):
        PrecoPlano.objects.filter(pk=preco.pk).update(valor_base_centavos=100)


def test_preco_de_versao_publicada_e_imutavel_por_bulk_update():
    _, preco = criar_preco_publicado(codigo="preco-bulk-update")
    preco.valor_seat_centavos = 100

    with pytest.raises(ValueError, match="Preço de versão publicada é imutável"):
        PrecoPlano.objects.bulk_update([preco], ["valor_seat_centavos"])


def test_flag_operacional_de_preco_publicado_pode_mudar():
    _, preco = criar_preco_publicado(codigo="preco-operacional")
    preco.is_active = False
    preco.save(update_fields=["is_active"])

    preco.refresh_from_db()
    assert preco.is_active is False


def test_preco_de_versao_publicada_nao_pode_ser_removido_logicamente():
    _, preco = criar_preco_publicado(codigo="preco-delete")

    with pytest.raises(ValueError, match="Preço de versão publicada é imutável"):
        preco.delete()

    preco.refresh_from_db()
    assert preco.is_deleted is False


def test_banco_recusa_anexar_preco_a_versao_publicada():
    versao = criar_versao(criar_plano())

    with pytest.raises(DatabaseError, match="Preço de versão publicada é imutável"), transaction.atomic():
        criar_preco(versao, periodicidade=Periodicidade.ANUAL)

    assert PrecoPlano.all_objects.filter(versao_plano=versao).exists() is False


def test_banco_recusa_upsert_que_muda_termos_de_versao_publicada():
    plano = criar_plano()
    versao = criar_versao(plano)
    conflito = VersaoPlano(
        plano=plano,
        numero=versao.numero,
        atual=False,
        seats_inclusos=99,
        recursos=versao.recursos,
        publicada_em=versao.publicada_em,
    )

    with pytest.raises(DatabaseError, match="Versão de plano publicada é imutável"), transaction.atomic():
        VersaoPlano.objects.bulk_create(
            [conflito],
            update_conflicts=True,
            update_fields=["seats_inclusos"],
            unique_fields=["plano", "numero"],
        )

    versao.refresh_from_db()
    assert versao.seats_inclusos == 1


def test_banco_recusa_reparentear_preco_de_draft_para_versao_publicada():
    plano = criar_plano()
    publicada = criar_versao(plano, numero=1, atual=True)
    draft = criar_versao(plano, numero=2, atual=False, publicada=False)
    preco = criar_preco(draft)

    with pytest.raises(DatabaseError, match="Preço de versão publicada é imutável"), transaction.atomic():
        PrecoPlano.all_objects.filter(pk=preco.pk).update(versao_plano=publicada)

    preco.refresh_from_db()
    assert preco.versao_plano_id == draft.pk


def test_banco_recusa_delete_fisico_de_versao_publicada():
    versao = criar_versao(criar_plano())

    with pytest.raises(DatabaseError, match="Versão de plano publicada é imutável"), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("DELETE FROM versao_plano WHERE id = %s", [versao.pk])

    assert VersaoPlano.all_objects.filter(pk=versao.pk).exists()


def test_banco_recusa_delete_fisico_de_preco_publicado():
    _, preco = criar_preco_publicado()

    with pytest.raises(DatabaseError, match="Preço de versão publicada é imutável"), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("DELETE FROM preco_plano WHERE id = %s", [preco.pk])

    assert PrecoPlano.all_objects.filter(pk=preco.pk).exists()


@pytest.mark.django_db(transaction=True)
def test_banco_serializa_publicacao_contra_atualizacao_concorrente():
    versao = criar_versao(criar_plano(), publicada=False)
    publicacao_escrita = Event()
    atualizacao_iniciada = Event()

    def publicar():
        close_old_connections()
        try:
            with transaction.atomic():
                concorrente = VersaoPlano.objects.get(pk=versao.pk)
                concorrente.publicada_em = timezone.now()
                concorrente.save(update_fields=["publicada_em"])
                publicacao_escrita.set()
                assert atualizacao_iniciada.wait(timeout=5)
            return "publicada"
        finally:
            connections.close_all()

    def atualizar_termos():
        close_old_connections()
        try:
            assert publicacao_escrita.wait(timeout=5)
            atualizacao_iniciada.set()
            try:
                with transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute("UPDATE versao_plano SET seats_inclusos = 99 WHERE id = %s", [versao.pk])
            except DatabaseError:
                return "bloqueada"
            return "alterada"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        resultados = [executor.submit(publicar), executor.submit(atualizar_termos)]
        assert [futuro.result(timeout=10) for futuro in resultados] == ["publicada", "bloqueada"]

    versao.refresh_from_db()
    assert versao.publicada_em is not None
    assert versao.seats_inclusos == 1


def test_create_rejeita_snapshot_de_recursos_desconhecido_antes_de_publicar():
    plano = criar_plano()

    with pytest.raises(ValueError, match="recurso desconhecido.*inexistente"):
        VersaoPlano.objects.create(
            plano=plano,
            numero=1,
            atual=False,
            recursos={"inexistente": True},
            publicada_em=timezone.now(),
        )

    assert VersaoPlano.all_objects.filter(plano=plano).exists() is False


def test_save_materializa_defaults_no_snapshot_de_recursos_do_draft():
    versao = criar_versao(criar_plano(), publicada=False)
    versao.recursos = {"quantidade_projetos": 5}

    versao.save(update_fields=["recursos"])

    versao.refresh_from_db()
    assert versao.recursos == {"papeis_isentos_seat": [], "quantidade_projetos": 5}


def test_save_valida_snapshot_de_recursos_ao_publicar_draft():
    versao = criar_versao(criar_plano(), publicada=False)
    versao.recursos = {"inexistente": True}
    versao.publicada_em = timezone.now()

    with pytest.raises(ValueError, match="recurso desconhecido.*inexistente"):
        versao.save(update_fields=["publicada_em"])

    versao.refresh_from_db()
    assert versao.publicada_em is None


def test_queryset_update_rejeita_snapshot_de_recursos_invalido():
    versao = criar_versao(criar_plano(), publicada=False)

    with pytest.raises(ValueError, match="quantidade_projetos.*inteiro"):
        VersaoPlano.objects.filter(pk=versao.pk).update(recursos={"quantidade_projetos": True})

    versao.refresh_from_db()
    assert versao.recursos["quantidade_projetos"] == 1


def test_bulk_create_materializa_defaults_do_snapshot_de_recursos():
    plano = criar_plano()

    [versao] = VersaoPlano.objects.bulk_create([VersaoPlano(plano=plano, numero=1, atual=False, recursos={"quantidade_projetos": 8})])

    versao.refresh_from_db()
    assert versao.recursos == {"papeis_isentos_seat": [], "quantidade_projetos": 8}


def test_bulk_update_rejeita_snapshot_de_recursos_desconhecido():
    versao = criar_versao(criar_plano(), publicada=False)
    versao.recursos = {"inexistente": 1}

    with pytest.raises(ValueError, match="recurso desconhecido.*inexistente"):
        VersaoPlano.objects.bulk_update([versao], ["recursos"])

    versao.refresh_from_db()
    assert versao.recursos["quantidade_projetos"] == 1
