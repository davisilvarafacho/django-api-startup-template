"""Revisão otimista e histórico de alterações contratuais."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Barrier

from django.db import close_old_connections, connection, connections
from django.utils import timezone

import pytest

from apps.assinaturas.catalogs import PLANOS_BOOTSTRAP, CatalogoPlanos, sincronizar_planos
from apps.assinaturas.features import CATALOGO_RECURSOS, ValoresRecursos
from apps.assinaturas.models import (
    AlteracaoAssinatura,
    MomentoAplicacaoAlteracaoAssinatura,
    Periodicidade,
    StatusAlteracaoAssinatura,
    TipoAlteracaoAssinatura,
)
from apps.assinaturas.subscriptions import (
    Assinaturas,
    ConflitoIdempotenciaAssinatura,
    ConflitoRevisaoAssinatura,
    CriacaoAlteracaoAssinatura,
    OrigemVersaoPlano,
    TermosAssinatura,
)
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Organizacao

pytestmark = pytest.mark.django_db


def _catalogos():
    sincronizar_planos(PLANOS_BOOTSTRAP, aplicar=True)
    gratuito = CatalogoPlanos.obter_versao_inicial(codigo="gratuito", periodicidade=Periodicidade.MENSAL)
    profissional = CatalogoPlanos.obter_versao_inicial(codigo="profissional", periodicidade=Periodicidade.MENSAL)
    return gratuito, profissional


def _termos(versao, preco, *, seats_contratados=None, periodicidade=None):
    return TermosAssinatura(
        periodicidade=periodicidade or Periodicidade(preco.periodicidade),
        moeda=preco.moeda,
        valor_base_centavos=preco.valor_base_centavos,
        valor_seat_centavos=preco.valor_seat_centavos,
        seats_inclusos=versao.seats_inclusos,
        seats_contratados=versao.seats_inclusos if seats_contratados is None else seats_contratados,
        expansao_automatica_seats=versao.expansao_automatica_seats,
        recursos=ValoresRecursos(CATALOGO_RECURSOS, versao.recursos),
        carencia_pagamento_dias=versao.carencia_pagamento_dias,
        carencia_excesso_seats_dias=versao.carencia_excesso_seats_dias,
    )


def _assinatura_profissional(*, seats=6, periodo=False):
    (_, _), (versao, preco) = _catalogos()
    organizacao = Organizacao.objects.create(nome="Alterações", slug=f"alteracoes-{Organizacao.objects.count()}")
    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura = Assinaturas.criar_trial(
            organizacao=organizacao,
            versao_plano=versao,
            preco_plano=preco,
            chave_idempotencia=f"inicial-{organizacao.pk}",
        )
        assinatura.seats_contratados = seats
        if periodo:
            assinatura.periodo_atual_iniciado_em = timezone.now() - timedelta(days=10)
            assinatura.periodo_atual_termina_em = timezone.now() + timedelta(days=20)
        assinatura.save()
    return organizacao, assinatura, versao, preco


def _comando(assinatura, versao, preco, *, seats, chave, tipo=TipoAlteracaoAssinatura.AUMENTO_SEATS, aplicar_em=None):
    return CriacaoAlteracaoAssinatura(
        assinatura=assinatura,
        tipo=tipo,
        origem_pretendida=OrigemVersaoPlano(versao),
        termos_pretendidos=_termos(versao, preco, seats_contratados=seats),
        revisao_esperada=assinatura.revisao,
        chave_idempotencia=chave,
        aplicar_em=aplicar_em,
    )


def test_solicitar_alteracao_persiste_pedido_e_snapshots_completos_e_imutaveis():
    organizacao, assinatura, versao, preco = _assinatura_profissional()
    comando = _comando(assinatura, versao, preco, seats=8, chave="snapshot")

    with organizacao_atual_privilegiada(organizacao.pk):
        alteracao = Assinaturas.solicitar_alteracao(comando)

    assert alteracao.status == StatusAlteracaoAssinatura.SOLICITADA
    assert alteracao.momento_aplicacao == MomentoAplicacaoAlteracaoAssinatura.IMEDIATA
    assert alteracao.snapshot_anterior["revisao"] == 1
    assert alteracao.snapshot_anterior["seats_contratados"] == 6
    assert alteracao.snapshot_pretendido["revisao"] == 2
    assert alteracao.snapshot_pretendido["seats_contratados"] == 8
    assert set(alteracao.snapshot_anterior) == set(alteracao.snapshot_pretendido)
    assert {
        "versao_plano_id",
        "status",
        "status_financeiro",
        "periodicidade",
        "recursos",
        "trial_iniciado_em",
        "periodo_atual_termina_em",
        "carencia_pagamento_termina_em",
        "carencia_excesso_seats_termina_em",
        "cancelamento_agendado_para",
        "encerrada_em",
        "motivo_encerramento",
    } <= set(alteracao.snapshot_anterior)
    alteracao.pedido["seats_contratados"] = 99
    with pytest.raises(ValueError, match="imutáveis"):
        alteracao.save()


def test_solicitacao_idempotente_retorna_existente_e_recusa_payload_divergente():
    organizacao, assinatura, versao, preco = _assinatura_profissional()
    comando = _comando(assinatura, versao, preco, seats=8, chave="alteracao-idem")

    with organizacao_atual_privilegiada(organizacao.pk):
        primeira = Assinaturas.solicitar_alteracao(comando)
        repetida = Assinaturas.solicitar_alteracao(comando)
        with pytest.raises(ConflitoIdempotenciaAssinatura):
            Assinaturas.solicitar_alteracao(replace(comando, termos_pretendidos=replace(comando.termos_pretendidos, seats_contratados=9)))

    assert repetida.pk == primeira.pk


def test_confirmar_imediata_aplica_snapshot_e_incrementa_revisao():
    organizacao, assinatura, versao, preco = _assinatura_profissional()
    agora = timezone.now()

    with organizacao_atual_privilegiada(organizacao.pk):
        alteracao = Assinaturas.solicitar_alteracao(_comando(assinatura, versao, preco, seats=8, chave="imediata"))
        Assinaturas.marcar_aguardando_gateway(alteracao)
        confirmada = Assinaturas.confirmar_alteracao(alteracao, evento_gateway="evt_1", agora=agora)

    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura.refresh_from_db()
    assert assinatura.seats_contratados == 8
    assert assinatura.revisao == 2
    assert confirmada.status == StatusAlteracaoAssinatura.CONFIRMADA
    assert confirmada.processada_em == agora
    assert confirmada.aplicada_em == agora
    assert confirmada.revisao_aplicada == 2
    assert confirmada.evento_gateway == "evt_1"


def test_revisao_otimista_recusa_confirmacao_obsoleta():
    organizacao, assinatura, versao, preco = _assinatura_profissional()

    with organizacao_atual_privilegiada(organizacao.pk):
        primeira = Assinaturas.solicitar_alteracao(_comando(assinatura, versao, preco, seats=7, chave="primeira"))
        segunda = Assinaturas.solicitar_alteracao(_comando(assinatura, versao, preco, seats=8, chave="segunda"))
        Assinaturas.confirmar_alteracao(primeira)
        with pytest.raises(ConflitoRevisaoAssinatura):
            Assinaturas.confirmar_alteracao(segunda)

    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura.refresh_from_db()
    assert assinatura.revisao == 2
    assert assinatura.seats_contratados == 7


def test_evento_atrasado_completa_historico_sem_regredir_assinatura():
    organizacao, assinatura, versao, preco = _assinatura_profissional()
    agora = timezone.now()

    with organizacao_atual_privilegiada(organizacao.pk):
        antiga = Assinaturas.solicitar_alteracao(_comando(assinatura, versao, preco, seats=7, chave="antiga"))
        nova = Assinaturas.solicitar_alteracao(_comando(assinatura, versao, preco, seats=9, chave="nova"))
        Assinaturas.confirmar_alteracao(nova)
        historico = Assinaturas.confirmar_alteracao(
            antiga,
            evento_gateway="evt_atrasado",
            agora=agora,
            permitir_evento_atrasado=True,
        )

    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura.refresh_from_db()
    assert assinatura.revisao == 2
    assert assinatura.seats_contratados == 9
    assert historico.status == StatusAlteracaoAssinatura.CONFIRMADA
    assert historico.aplicada_em is None
    assert historico.revisao_aplicada is None
    assert historico.revisao_observada == 2
    assert historico.ignorada_em == agora


def test_reducao_agendada_so_aplica_no_proximo_ciclo_e_valida_consumo():
    organizacao, assinatura, versao, preco = _assinatura_profissional(seats=8, periodo=True)
    aplicar_em = assinatura.periodo_atual_termina_em
    comando = _comando(
        assinatura,
        versao,
        preco,
        seats=6,
        chave="reducao",
        tipo=TipoAlteracaoAssinatura.REDUCAO_SEATS,
        aplicar_em=aplicar_em,
    )

    with organizacao_atual_privilegiada(organizacao.pk):
        with pytest.raises(ValueError, match="consumo"):
            Assinaturas.solicitar_alteracao(replace(comando, seats_consumidos=7))
        alteracao = Assinaturas.solicitar_alteracao(replace(comando, seats_consumidos=6))
        confirmada = Assinaturas.confirmar_alteracao(alteracao, agora=aplicar_em - timedelta(seconds=1))

    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura.refresh_from_db()
    assert confirmada.status == StatusAlteracaoAssinatura.CONFIRMADA
    assert confirmada.aplicada_em is None
    assert assinatura.seats_contratados == 8

    with organizacao_atual_privilegiada(organizacao.pk):
        aplicada = Assinaturas.aplicar_alteracao_agendada(confirmada, agora=aplicar_em)

    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura.refresh_from_db()
    assert assinatura.seats_contratados == 6
    assert assinatura.revisao == 2
    assert aplicada.aplicada_em == aplicar_em


@pytest.mark.django_db(transaction=True)
def test_confirmacoes_concorrentes_da_mesma_revisao_aplicam_somente_uma():
    organizacao, assinatura, versao, preco = _assinatura_profissional()
    with organizacao_atual_privilegiada(organizacao.pk):
        ids = [
            Assinaturas.solicitar_alteracao(_comando(assinatura, versao, preco, seats=seats, chave=chave)).pk
            for seats, chave in ((7, "corrente-1"), (8, "corrente-2"))
        ]
    barreira = Barrier(2)

    def confirmar(alteracao_id):
        close_old_connections()
        try:
            barreira.wait(timeout=5)
            with organizacao_atual_privilegiada(organizacao.pk):
                alteracao = AlteracaoAssinatura.objects.get(pk=alteracao_id)
                try:
                    Assinaturas.confirmar_alteracao(alteracao)
                except ConflitoRevisaoAssinatura:
                    return "conflito"
                return "aplicada"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        resultados = list(executor.map(confirmar, ids))

    assert sorted(resultados) == ["aplicada", "conflito"]
    with organizacao_atual_privilegiada(organizacao.pk):
        assert AlteracaoAssinatura.objects.filter(status=StatusAlteracaoAssinatura.CONFIRMADA).count() == 1


@pytest.mark.django_db(transaction=True)
def test_falha_durante_aquisicao_dos_locks_nao_deixa_transacao_aberta():
    organizacao, assinatura, versao, preco = _assinatura_profissional()
    with organizacao_atual_privilegiada(organizacao.pk):
        alteracao = Assinaturas.solicitar_alteracao(_comando(assinatura, versao, preco, seats=7, chave="lock-falhou"))
    alteracao.pk += 1_000_000

    assert connection.in_atomic_block is False
    with organizacao_atual_privilegiada(organizacao.pk), pytest.raises(AlteracaoAssinatura.DoesNotExist):
        Assinaturas.confirmar_alteracao(alteracao)

    assert connection.in_atomic_block is False
