"""Serialização PostgreSQL das disputas do ciclo de propostas."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

from django.db import close_old_connections, connections

import pytest

from apps.assinaturas.features import CATALOGO_RECURSOS, ValoresRecursos
from apps.assinaturas.models import (
    AlteracaoAssinatura,
    AssinaturaOrganizacao,
    ModoAtivacaoProposta,
    PropostaComercial,
    StatusAlteracaoAssinatura,
    StatusAssinatura,
    StatusPropostaComercial,
    TipoAlteracaoAssinatura,
)
from apps.assinaturas.proposals import ConflitoPropostaComercial, Propostas
from apps.assinaturas.subscriptions import (
    Assinaturas,
    ConflitoRevisaoAssinatura,
    CriacaoAlteracaoAssinatura,
    OrigemVersaoPlano,
    TermosAssinatura,
)
from apps.assinaturas.tests.test_proposals import _contrato_atual, _criar_rascunho, _enviar
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Organizacao, Papel, Vinculo
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db(transaction=True)


def _executar_em_thread(funcao):
    close_old_connections()
    try:
        return funcao()
    finally:
        connections.close_all()


def test_dois_aceites_da_mesma_revisao_convergem_idempotentemente():
    organizacao = Organizacao.objects.create(nome="Dois aceites", slug="proposta-dois-aceites")
    proprietario = criar_usuario(email="owner-dois-aceites@example.com")
    Vinculo.objects.create(organizacao=organizacao, usuario=proprietario, papel=Papel.PROPRIETARIO)
    proposta = _enviar(_criar_rascunho(organizacao))
    barreira = Barrier(2)

    def aceitar():
        barreira.wait(timeout=5)
        try:
            resultado = Propostas.aceitar(proposta, ator=proprietario, revisao_esperada=2)
        except ConflitoPropostaComercial:
            return "conflito"
        return resultado.proposta.revisao

    with ThreadPoolExecutor(max_workers=2) as executor:
        resultados = list(executor.map(lambda _: _executar_em_thread(aceitar), (1, 2)))

    assert resultados == [3, 3]
    with organizacao_atual_privilegiada(organizacao.pk):
        atual = PropostaComercial.objects.get(pk=proposta.pk)
    assert atual.status == StatusPropostaComercial.ACEITA
    assert atual.revisao == 3


def test_aceite_concorrente_com_cancelamento_termina_cancelado_sem_deadlock():
    organizacao = Organizacao.objects.create(nome="Aceite e cancelamento", slug="proposta-aceite-cancelamento")
    proprietario = criar_usuario(email="owner-aceite-cancelamento@example.com")
    operador = criar_usuario(email="operador-aceite-cancelamento@example.com", is_staff=True, is_superuser=True)
    Vinculo.objects.create(organizacao=organizacao, usuario=proprietario, papel=Papel.PROPRIETARIO)
    proposta = _enviar(_criar_rascunho(organizacao))
    barreira = Barrier(2)

    def aceitar():
        barreira.wait(timeout=5)
        try:
            Propostas.aceitar(proposta, ator=proprietario, revisao_esperada=2)
        except ConflitoPropostaComercial:
            return "conflito"
        return "aceita"

    def cancelar():
        barreira.wait(timeout=5)
        try:
            Propostas.cancelar(proposta, ator=operador, revisao_esperada=2)
        except ConflitoPropostaComercial:
            # Se o aceite venceu a corrida, o cancelamento revalida a revisão
            # nova numa segunda tentativa nominal.
            Propostas.cancelar(proposta, ator=operador, revisao_esperada=3)
        return "cancelada"

    with ThreadPoolExecutor(max_workers=2) as executor:
        resultados = [
            executor.submit(_executar_em_thread, aceitar),
            executor.submit(_executar_em_thread, cancelar),
        ]
        assert {resultado.result(timeout=10) for resultado in resultados} <= {"aceita", "cancelada", "conflito"}

    with organizacao_atual_privilegiada(organizacao.pk):
        atual = PropostaComercial.objects.get(pk=proposta.pk)
    assert atual.status == StatusPropostaComercial.CANCELADA


def test_aceite_concorrente_com_expiracao_nao_aceita_proposta_fora_da_validade():
    organizacao = Organizacao.objects.create(nome="Aceite e expiração", slug="proposta-aceite-expiracao")
    proprietario = criar_usuario(email="owner-aceite-expiracao@example.com")
    Vinculo.objects.create(organizacao=organizacao, usuario=proprietario, papel=Papel.PROPRIETARIO)
    proposta = _enviar(_criar_rascunho(organizacao))
    depois_da_validade = proposta.valida_ate + timedelta(seconds=1)
    barreira = Barrier(2)

    def aceitar():
        barreira.wait(timeout=5)
        with pytest.raises(ConflitoPropostaComercial):
            Propostas.aceitar(
                proposta,
                ator=proprietario,
                revisao_esperada=2,
                agora=depois_da_validade,
            )

    def expirar():
        barreira.wait(timeout=5)
        return Propostas.expirar(
            proposta,
            revisao_esperada=2,
            agora=depois_da_validade,
        ).status

    with ThreadPoolExecutor(max_workers=2) as executor:
        aceite = executor.submit(_executar_em_thread, aceitar)
        expiracao = executor.submit(_executar_em_thread, expirar)
        aceite.result(timeout=10)
        assert expiracao.result(timeout=10) == StatusPropostaComercial.EXPIRADA

    with organizacao_atual_privilegiada(organizacao.pk):
        atual = PropostaComercial.objects.get(pk=proposta.pk)
    assert atual.status == StatusPropostaComercial.EXPIRADA


def test_criacao_enterprise_concorrente_com_mudanca_do_contrato_converge_para_novo_ciclo():
    organizacao = Organizacao.objects.create(nome="Ativação e mudança", slug="proposta-ativacao-mudanca")
    proprietario = criar_usuario(email="owner-ativacao-mudanca@example.com")
    Vinculo.objects.create(organizacao=organizacao, usuario=proprietario, papel=Papel.PROPRIETARIO)
    anterior = _contrato_atual(organizacao)
    proposta = _enviar(_criar_rascunho(organizacao, modo=ModoAtivacaoProposta.CONTRATUAL))
    proposta = Propostas.aceitar(proposta, ator=proprietario, revisao_esperada=2).proposta
    termos_aumentados = TermosAssinatura(
        periodicidade=anterior.periodicidade,
        moeda=anterior.moeda,
        valor_base_centavos=anterior.valor_base_centavos,
        valor_seat_centavos=anterior.valor_seat_centavos,
        seats_inclusos=anterior.seats_inclusos,
        seats_contratados=anterior.seats_contratados + 1,
        expansao_automatica_seats=anterior.expansao_automatica_seats,
        recursos=ValoresRecursos(CATALOGO_RECURSOS, anterior.recursos),
        carencia_pagamento_dias=anterior.carencia_pagamento_dias,
        carencia_excesso_seats_dias=anterior.carencia_excesso_seats_dias,
    )
    comando = CriacaoAlteracaoAssinatura(
        assinatura=anterior,
        tipo=TipoAlteracaoAssinatura.AUMENTO_SEATS,
        origem_pretendida=OrigemVersaoPlano(anterior.versao_plano),
        termos_pretendidos=termos_aumentados,
        revisao_esperada=anterior.revisao,
        chave_idempotencia="mudanca-concorrente-proposta",
    )
    barreira = Barrier(2)

    def ativar():
        barreira.wait(timeout=5)
        return Assinaturas.criar_enterprise(proposta_comercial=proposta).pk

    def alterar():
        barreira.wait(timeout=5)
        try:
            with organizacao_atual_privilegiada(organizacao.pk):
                return Assinaturas.solicitar_alteracao(comando).pk
        except ConflitoRevisaoAssinatura:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        ativacao = executor.submit(_executar_em_thread, ativar)
        alteracao = executor.submit(_executar_em_thread, alterar)
        nova_id = ativacao.result(timeout=10)
        alteracao_id = alteracao.result(timeout=10)

    with organizacao_atual_privilegiada(organizacao.pk):
        anterior.refresh_from_db()
        nova = AssinaturaOrganizacao.objects.get(pk=nova_id)
        alteracao_status = AlteracaoAssinatura.objects.get(pk=alteracao_id).status if alteracao_id is not None else None
    assert anterior.status == StatusAssinatura.ENCERRADA
    assert nova.proposta_comercial_id == proposta.pk
    if alteracao_id is not None:
        assert alteracao_status == StatusAlteracaoAssinatura.CANCELADA
