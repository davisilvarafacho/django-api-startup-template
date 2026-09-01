"""Calculo puro da utilizacao e da politica de acesso contratual."""

from dataclasses import replace
from datetime import timedelta

from django.utils import timezone

import pytest

from apps.assinaturas.access_policies import (
    MotivoRestricao,
    PoliticaAcessoAssinatura,
    SituacaoAcesso,
    StatusAcesso,
)
from apps.assinaturas.models import AssinaturaOrganizacao, StatusAssinatura, StatusFinanceiro
from apps.assinaturas.subscriptions import Assinaturas, UtilizacaoSeats
from apps.organizacoes.memberships import OcupacaoSeats


def _assinatura(**mudancas) -> AssinaturaOrganizacao:
    agora = timezone.now()
    dados = {
        "status": StatusAssinatura.ATIVA,
        "status_financeiro": StatusFinanceiro.REGULAR,
        "seats_contratados": 5,
        "trial_iniciado_em": None,
        "trial_termina_em": None,
        "carencia_pagamento_iniciada_em": None,
        "carencia_pagamento_termina_em": None,
        "carencia_excesso_seats_iniciada_em": None,
        "carencia_excesso_seats_termina_em": None,
        "encerrada_em": None,
    }
    dados.update(mudancas)
    assinatura = AssinaturaOrganizacao(**dados)
    assinatura._agora_teste = agora
    return assinatura


def _utilizacao(*, contratados=5, consumidos=4, reservados=0) -> UtilizacaoSeats:
    comprometidos = consumidos + reservados
    return UtilizacaoSeats(
        contratados=contratados,
        consumidos=consumidos,
        reservados=reservados,
        comprometidos=comprometidos,
        disponiveis=max(contratados - comprometidos, 0),
        excesso_real=max(consumidos - contratados, 0),
        excesso_comprometido=max(comprometidos - contratados, 0),
    )


@pytest.mark.parametrize(
    ("ocupacao", "mensagem"),
    [
        (object(), "OcupacaoSeats"),
        (OcupacaoSeats(consumidos=True, reservados=0), "inteiros nao negativos"),
        (OcupacaoSeats(consumidos=0, reservados=-1), "inteiros nao negativos"),
    ],
)
def test_calcular_utilizacao_exige_ocupacao_tipificada_e_inteiros_estritos(ocupacao, mensagem):
    assinatura = _assinatura()

    with pytest.raises(ValueError, match=mensagem):
        Assinaturas.calcular_utilizacao(assinatura, ocupacao)


@pytest.mark.django_db
def test_calcular_utilizacao_rejeita_capacidade_coagida_sem_consultar_banco(django_assert_num_queries):
    assinatura = _assinatura(seats_contratados=True)

    with django_assert_num_queries(0), pytest.raises(ValueError, match="seats_contratados"):
        Assinaturas.calcular_utilizacao(assinatura, OcupacaoSeats(consumidos=0, reservados=0))


def test_politica_libera_contrato_ativo_regular_sem_excesso():
    agora = timezone.now()

    situacao = PoliticaAcessoAssinatura.avaliar(_assinatura(), _utilizacao(), agora)

    assert situacao == SituacaoAcesso(status=StatusAcesso.LIBERADO, motivos=(), regularizar_ate=None)


def test_politica_expoe_duas_carencias_e_o_menor_prazo_aplicavel():
    agora = timezone.now()
    assinatura = _assinatura(
        status_financeiro=StatusFinanceiro.INADIMPLENTE,
        carencia_pagamento_iniciada_em=agora - timedelta(days=1),
        carencia_pagamento_termina_em=agora + timedelta(days=6),
        carencia_excesso_seats_iniciada_em=agora - timedelta(days=2),
        carencia_excesso_seats_termina_em=agora + timedelta(days=3),
    )

    situacao = PoliticaAcessoAssinatura.avaliar(
        assinatura,
        _utilizacao(contratados=5, consumidos=7),
        agora,
    )

    assert situacao == SituacaoAcesso(
        status=StatusAcesso.EM_CARENCIA,
        motivos=(
            MotivoRestricao.PAYMENT_GRACE_PERIOD,
            MotivoRestricao.SEAT_OVERAGE_GRACE_PERIOD,
        ),
        regularizar_ate=agora + timedelta(days=3),
    )


def test_uma_carencia_expirada_restringe_sem_ocultar_a_outra_causa():
    agora = timezone.now()
    assinatura = _assinatura(
        status_financeiro=StatusFinanceiro.INADIMPLENTE,
        carencia_pagamento_iniciada_em=agora - timedelta(days=8),
        carencia_pagamento_termina_em=agora - timedelta(days=1),
        carencia_excesso_seats_iniciada_em=agora - timedelta(days=1),
        carencia_excesso_seats_termina_em=agora + timedelta(days=6),
    )

    situacao = PoliticaAcessoAssinatura.avaliar(
        assinatura,
        _utilizacao(contratados=5, consumidos=6),
        agora,
    )

    assert situacao == SituacaoAcesso(
        status=StatusAcesso.RESTRITO,
        motivos=(
            MotivoRestricao.PAYMENT_GRACE_PERIOD_EXPIRED,
            MotivoRestricao.SEAT_OVERAGE_GRACE_PERIOD,
        ),
        regularizar_ate=agora - timedelta(days=1),
    )


@pytest.mark.parametrize(
    ("assinatura", "utilizacao", "motivo"),
    [
        (
            _assinatura(status=StatusAssinatura.PENDENTE, status_financeiro=StatusFinanceiro.PENDENTE),
            _utilizacao(),
            MotivoRestricao.SUBSCRIPTION_PENDING,
        ),
        (
            _assinatura(
                status=StatusAssinatura.EM_TRIAL,
                status_financeiro=StatusFinanceiro.ISENTO,
                trial_termina_em=timezone.now() - timedelta(seconds=1),
            ),
            _utilizacao(),
            MotivoRestricao.TRIAL_EXPIRED,
        ),
        (
            _assinatura(
                status=StatusAssinatura.ENCERRADA,
                status_financeiro=StatusFinanceiro.ISENTO,
                encerrada_em=timezone.now(),
            ),
            _utilizacao(),
            MotivoRestricao.SUBSCRIPTION_ENDED,
        ),
        (
            _assinatura(),
            _utilizacao(contratados=5, consumidos=6),
            MotivoRestricao.SEAT_OVERAGE_GRACE_PERIOD_EXPIRED,
        ),
    ],
)
def test_politica_falha_fechada_para_estado_contratual_ou_carencia_ausente(assinatura, utilizacao, motivo):
    agora = timezone.now()

    situacao = PoliticaAcessoAssinatura.avaliar(assinatura, utilizacao, agora)

    assert situacao.status == StatusAcesso.RESTRITO
    assert situacao.motivos == (motivo,)


def test_trial_ainda_vigente_permanece_liberado():
    agora = timezone.now()
    assinatura = _assinatura(
        status=StatusAssinatura.EM_TRIAL,
        status_financeiro=StatusFinanceiro.ISENTO,
        trial_termina_em=agora + timedelta(seconds=1),
    )

    situacao = PoliticaAcessoAssinatura.avaliar(assinatura, _utilizacao(), agora)

    assert situacao.status == StatusAcesso.LIBERADO
    assert situacao.motivos == ()


def test_politica_rejeita_utilizacao_incoerente_em_vez_de_recalcular_formula():
    utilizacao = replace(_utilizacao(), excesso_real=99)

    with pytest.raises(ValueError, match="UtilizacaoSeats incoerente"):
        PoliticaAcessoAssinatura.avaliar(_assinatura(), utilizacao, timezone.now())


def test_politica_rejeita_utilizacao_de_outro_snapshot_contratual():
    with pytest.raises(ValueError, match="snapshot contratual"):
        PoliticaAcessoAssinatura.avaliar(
            _assinatura(seats_contratados=3),
            _utilizacao(contratados=5),
            timezone.now(),
        )


@pytest.mark.django_db
def test_calculos_puros_rejeitam_assinatura_deferred_sem_consultar_banco(django_assert_num_queries):
    from apps.assinaturas.tests.test_subscription_access_transitions import _assinatura_ativa
    from apps.organizacoes.context import organizacao_atual_privilegiada

    organizacao, assinatura = _assinatura_ativa(slug="politica-deferred")
    with organizacao_atual_privilegiada(organizacao.pk):
        deferred = AssinaturaOrganizacao.objects.only("id").get(pk=assinatura.pk)

    with django_assert_num_queries(0), pytest.raises(ValueError, match="deferred"):
        Assinaturas.calcular_utilizacao(deferred, OcupacaoSeats(consumidos=1, reservados=0))
    with django_assert_num_queries(0), pytest.raises(ValueError, match="deferred"):
        PoliticaAcessoAssinatura.avaliar(deferred, _utilizacao(), timezone.now())
