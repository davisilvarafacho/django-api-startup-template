"""Criação tipada, preço e idempotência de assinaturas."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from threading import Barrier

from django.db import close_old_connections, connections
from django.utils import timezone

import pytest

from apps.assinaturas.catalogs import PLANOS_BOOTSTRAP, CatalogoPlanos, sincronizar_planos
from apps.assinaturas.features import CATALOGO_RECURSOS, ValoresRecursos
from apps.assinaturas.models import AssinaturaOrganizacao, Periodicidade, StatusAssinatura, StatusFinanceiro
from apps.assinaturas.subscriptions import (
    Assinaturas,
    ConflitoIdempotenciaAssinatura,
    ConflitoRevisaoAssinatura,
    CriacaoAssinatura,
    OrigemVersaoPlano,
    PoliticaTrial,
    TermosAssinatura,
)
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Organizacao

pytestmark = pytest.mark.django_db


def _catalogo(codigo: str, periodicidade: Periodicidade = Periodicidade.MENSAL):
    sincronizar_planos(PLANOS_BOOTSTRAP, aplicar=True)
    return CatalogoPlanos.obter_versao_inicial(codigo=codigo, periodicidade=periodicidade)


def _termos(versao, preco, *, seats_contratados: int | None = None) -> TermosAssinatura:
    return TermosAssinatura(
        periodicidade=Periodicidade(preco.periodicidade),
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


def _comando(organizacao, versao, preco, *, chave="assinatura-idem", seats_contratados=None):
    return CriacaoAssinatura(
        organizacao=organizacao,
        origem=OrigemVersaoPlano(versao_plano=versao),
        termos=_termos(versao, preco, seats_contratados=seats_contratados),
        status=StatusAssinatura.ATIVA,
        status_financeiro=StatusFinanceiro.REGULAR,
        politica_trial=None,
        trial_termina_em=None,
        chave_idempotencia=chave,
    )


def test_dtos_sao_imutaveis_e_rejeitam_tipos_coagidos():
    versao, preco = _catalogo("gratuito")
    organizacao = Organizacao.objects.create(nome="DTO", slug="dto")
    comando = _comando(organizacao, versao, preco)

    with pytest.raises(FrozenInstanceError):
        comando.chave_idempotencia = "outra"
    with pytest.raises(ValueError, match="Periodicidade"):
        replace(comando.termos, periodicidade="mensal")
    with pytest.raises(ValueError, match="inteiros não negativos"):
        replace(comando.termos, seats_contratados=True)
    with pytest.raises(ValueError, match="ValoresRecursos"):
        replace(comando.termos, recursos={})
    with pytest.raises(ValueError, match="Ativa"):
        replace(comando, status_financeiro=StatusFinanceiro.PENDENTE)
    with pytest.raises(ValueError, match="Encerrada"):
        replace(comando, status=StatusAssinatura.ENCERRADA)


@pytest.mark.parametrize(
    ("periodicidade", "base", "seat", "inclusos", "contratados", "seats_cobrados", "total"),
    [
        (Periodicidade.MENSAL, 1000, 250, 3, 0, 0, 1000),
        (Periodicidade.MENSAL, 1000, 250, 3, 3, 0, 1000),
        (Periodicidade.MENSAL, 1000, 250, 3, 5, 2, 1500),
        (Periodicidade.ANUAL, 10_000, 2_000, 3, 5, 2, 14_000),
        (Periodicidade.ANUAL, 0, 0, 0, 0, 0, 0),
    ],
)
def test_preco_usa_franquia_e_periodo_inteiro_sem_rateio(
    periodicidade,
    base,
    seat,
    inclusos,
    contratados,
    seats_cobrados,
    total,
):
    termos = TermosAssinatura(
        periodicidade=periodicidade,
        moeda="BRL",
        valor_base_centavos=base,
        valor_seat_centavos=seat,
        seats_inclusos=inclusos,
        seats_contratados=contratados,
        expansao_automatica_seats=False,
        recursos=ValoresRecursos(CATALOGO_RECURSOS, {}),
        carencia_pagamento_dias=0,
        carencia_excesso_seats_dias=0,
    )

    preco = Assinaturas.calcular_preco(termos)

    assert preco.seats_cobrados == seats_cobrados
    assert preco.total_centavos == total


def test_criar_gratuita_materializa_snapshot_ativo_e_isento():
    versao, preco = _catalogo("gratuito")
    organizacao = Organizacao.objects.create(nome="Gratuita", slug="gratuita")

    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura = Assinaturas.criar_gratuita(
            organizacao=organizacao,
            versao_plano=versao,
            preco_plano=preco,
        )

    assert assinatura.status == StatusAssinatura.ATIVA
    assert assinatura.status_financeiro == StatusFinanceiro.ISENTO
    assert assinatura.revisao == 1
    assert assinatura.versao_plano == versao
    assert assinatura.seats_contratados == 1
    assert assinatura.total_centavos == 0
    assert assinatura.recursos == {
        "papeis_isentos_seat": [],
        "quantidade_projetos": 1,
    }


def test_criar_trial_copia_limite_e_datas_da_versao_paga():
    versao, preco = _catalogo("profissional")
    organizacao = Organizacao.objects.create(nome="Trial", slug="trial")
    agora = timezone.now()

    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura = Assinaturas.criar_trial(
            organizacao=organizacao,
            versao_plano=versao,
            preco_plano=preco,
            agora=agora,
        )

    assert assinatura.status == StatusAssinatura.EM_TRIAL
    assert assinatura.status_financeiro == StatusFinanceiro.ISENTO
    assert assinatura.politica_trial == PoliticaTrial.SEM_FORMA_PAGAMENTO
    assert assinatura.trial_iniciado_em == agora
    assert assinatura.trial_termina_em == agora + timedelta(days=14)
    assert assinatura.seats_contratados == 10


def test_criar_trial_repetido_sem_relogio_injetado_retorna_o_mesmo_contrato():
    versao, preco = _catalogo("profissional")
    organizacao = Organizacao.objects.create(nome="Trial idempotente", slug="trial-idempotente")

    with organizacao_atual_privilegiada(organizacao.pk):
        primeira = Assinaturas.criar_trial(
            organizacao=organizacao,
            versao_plano=versao,
            preco_plano=preco,
            chave_idempotencia="trial-repetido",
        )
        repetida = Assinaturas.criar_trial(
            organizacao=organizacao,
            versao_plano=versao,
            preco_plano=preco,
            chave_idempotencia="trial-repetido",
        )

    assert repetida.pk == primeira.pk
    assert repetida.trial_iniciado_em == primeira.trial_iniciado_em
    assert repetida.trial_termina_em == primeira.trial_termina_em


def test_criar_trial_sem_relogio_inicia_no_primeiro_processamento_de_organizacao_antiga(monkeypatch):
    versao, preco = _catalogo("profissional")
    criada_em = timezone.now() - timedelta(days=365)
    primeiro_processamento = timezone.now()
    organizacao = Organizacao.objects.create(nome="Trial antigo", slug="trial-antigo")
    Organizacao.objects.filter(pk=organizacao.pk).update(created_at=criada_em)
    organizacao.created_at = criada_em
    monkeypatch.setattr("apps.assinaturas.subscriptions.timezone.now", lambda: primeiro_processamento)

    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura = Assinaturas.criar_trial(
            organizacao=organizacao,
            versao_plano=versao,
            preco_plano=preco,
            chave_idempotencia="trial-antigo",
        )

    assert assinatura.trial_iniciado_em == primeiro_processamento
    assert assinatura.trial_termina_em == primeiro_processamento + timedelta(days=14)


def test_criar_paga_nasce_pendente_com_seats_absolutos():
    versao, preco = _catalogo("profissional")
    organizacao = Organizacao.objects.create(nome="Paga", slug="paga")

    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura = Assinaturas.criar_paga(
            organizacao=organizacao,
            versao_plano=versao,
            preco_plano=preco,
            seats_contratados=7,
            chave_idempotencia="paga-7",
        )

    assert assinatura.status == StatusAssinatura.PENDENTE
    assert assinatura.status_financeiro == StatusFinanceiro.PENDENTE
    assert assinatura.seats_inclusos == 5
    assert assinatura.seats_contratados == 7
    assert assinatura.total_centavos == 13_900


def test_criar_paga_recusa_preco_total_igual_a_zero():
    versao, preco = _catalogo("gratuito")
    organizacao = Organizacao.objects.create(nome="Paga zero", slug="paga-zero")

    with organizacao_atual_privilegiada(organizacao.pk), pytest.raises(ValueError, match="valor positivo"):
        Assinaturas.criar_paga(
            organizacao=organizacao,
            versao_plano=versao,
            preco_plano=preco,
            seats_contratados=1,
        )


def test_criar_retorna_existente_para_mesma_chave_e_payload():
    versao, preco = _catalogo("profissional")
    organizacao = Organizacao.objects.create(nome="Idem", slug="idem")
    comando = _comando(organizacao, versao, preco, seats_contratados=6)

    with organizacao_atual_privilegiada(organizacao.pk):
        primeira = Assinaturas.criar(comando)
        repetida = Assinaturas.criar(comando)

    assert repetida.pk == primeira.pk
    with organizacao_atual_privilegiada(organizacao.pk):
        assert AssinaturaOrganizacao.objects.filter(organizacao=organizacao).count() == 1


def test_criar_recusa_mesma_chave_com_payload_divergente():
    versao, preco = _catalogo("profissional")
    organizacao = Organizacao.objects.create(nome="Divergente", slug="divergente")
    comando = _comando(organizacao, versao, preco, seats_contratados=6)

    with organizacao_atual_privilegiada(organizacao.pk):
        Assinaturas.criar(comando)
        with pytest.raises(ConflitoIdempotenciaAssinatura):
            Assinaturas.criar(replace(comando, termos=replace(comando.termos, seats_contratados=7)))


def test_criar_recusa_novo_contrato_corrente_com_outra_chave():
    versao, preco = _catalogo("profissional")
    organizacao = Organizacao.objects.create(nome="Corrente", slug="servico-corrente")

    with organizacao_atual_privilegiada(organizacao.pk):
        Assinaturas.criar(_comando(organizacao, versao, preco, chave="primeira"))
        with pytest.raises(ConflitoRevisaoAssinatura):
            Assinaturas.criar(_comando(organizacao, versao, preco, chave="segunda"))


@pytest.mark.django_db(transaction=True)
def test_criacao_concorrente_com_mesma_chave_converge_para_um_contrato():
    versao, preco = _catalogo("profissional")
    organizacao = Organizacao.objects.create(nome="Concorrente", slug="concorrente")
    barreira = Barrier(2)

    def criar():
        close_old_connections()
        try:
            org = Organizacao.objects.get(pk=organizacao.pk)
            versao_local, preco_local = CatalogoPlanos.obter_versao_inicial(
                codigo="profissional",
                periodicidade=Periodicidade.MENSAL,
            )
            comando = _comando(org, versao_local, preco_local, chave="concorrente", seats_contratados=6)
            barreira.wait(timeout=5)
            with organizacao_atual_privilegiada(org.pk):
                return Assinaturas.criar(comando).pk
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        ids = list(executor.map(lambda _: criar(), range(2)))

    assert ids[0] == ids[1]
    with organizacao_atual_privilegiada(organizacao.pk):
        assert AssinaturaOrganizacao.objects.filter(organizacao=organizacao).count() == 1


def test_preset_recusa_preco_de_outra_versao():
    versao_gratuita, _ = _catalogo("gratuito")
    _, preco_pago = CatalogoPlanos.obter_versao_inicial(
        codigo="profissional",
        periodicidade=Periodicidade.MENSAL,
    )
    organizacao = Organizacao.objects.create(nome="Origem", slug="origem-divergente")

    with organizacao_atual_privilegiada(organizacao.pk), pytest.raises(ValueError, match="versão informada"):
        Assinaturas.criar_gratuita(
            organizacao=organizacao,
            versao_plano=versao_gratuita,
            preco_plano=preco_pago,
        )
