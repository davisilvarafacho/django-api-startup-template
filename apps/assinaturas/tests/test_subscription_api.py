"""Contrato HTTP das consultas e mutacoes de assinatura."""

from datetime import timedelta

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from rest_framework.test import APIClient

import pytest

from apps.api.autenticacao.models import AuthToken, TokenMetaData, TokenType
from apps.api.core.errors import APIError
from apps.assinaturas.access_policies import MotivoRestricao, StatusAcesso
from apps.assinaturas.catalogs import CatalogoPlanos
from apps.assinaturas.features import CATALOGO_RECURSOS, ValoresRecursos
from apps.assinaturas.models import (
    AlteracaoAssinatura,
    Periodicidade,
    StatusAlteracaoAssinatura,
    StatusAssinatura,
    TipoAlteracaoAssinatura,
)
from apps.assinaturas.subscriptions import Assinaturas, PagamentoTrialConfirmado
from apps.organizacoes.constants import META_HEADER_ORGANIZACAO
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Papel, Vinculo
from tests.support.usuarios import criar_usuario

from .test_subscription_access_transitions import _assinatura_ativa, _trial

pytestmark = pytest.mark.django_db


def _client(usuario, organizacao, *, recente=True):
    token, plain_token = AuthToken.objects.create(user=usuario)
    TokenMetaData.objects.create(token=token, reauthenticated_at=timezone.now() if recente else None)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {plain_token}")
    client.defaults[META_HEADER_ORGANIZACAO] = organizacao.slug
    return client


def _usuario_na(organizacao, *, email, papel, email_verificado=True):
    usuario = criar_usuario(email=email, email_verificado_em=timezone.now() if email_verificado else None)
    Vinculo.objects.create(organizacao=organizacao, usuario=usuario, papel=papel)
    return usuario


def _assinatura_paga(*, slug):
    organizacao, assinatura = _trial(slug=slug)
    agora = assinatura.trial_termina_em + timedelta(seconds=1)
    with organizacao_atual_privilegiada(organizacao.pk):
        resultado = Assinaturas.encerrar_trial(
            assinatura,
            resultado=PagamentoTrialConfirmado(
                seats_contratados=5,
                periodo_iniciado_em=agora,
                periodo_termina_em=agora + timedelta(days=30),
            ),
            agora=agora,
        )
    return organizacao, resultado.assinatura


def test_admin_consulta_assinatura_e_utilizacao_sem_recalculo_na_view():
    organizacao, assinatura = _assinatura_ativa(slug="api-consulta", seats=3)
    administrador = _usuario_na(
        organizacao,
        email="admin-assinatura@example.com",
        papel=Papel.ADMINISTRADOR,
    )

    contrato = _client(administrador, organizacao).get("/assinatura/")
    utilizacao = _client(administrador, organizacao).get("/assinatura/utilizacao-seats/")

    assert contrato.status_code == 200
    assert contrato.json() == {
        "id": assinatura.pk,
        "status": assinatura.status,
        "status_financeiro": assinatura.status_financeiro,
        "revisao": 1,
        "versao_plano_id": assinatura.versao_plano_id,
        "periodicidade": assinatura.periodicidade,
        "moeda": "BRL",
        "seats_inclusos": assinatura.seats_inclusos,
        "seats_contratados": 3,
        "total_centavos": assinatura.total_centavos,
        "trial_termina_em": None,
        "periodo_atual_termina_em": None,
        "cancelamento_agendado_para": None,
        "situacao_acesso": {
            "status": StatusAcesso.LIBERADO,
            "motivos": [],
            "regularizar_ate": None,
        },
    }
    assert utilizacao.status_code == 200
    assert utilizacao.json() == {
        "contratados": 3,
        "consumidos": 1,
        "reservados": 0,
        "comprometidos": 1,
        "disponiveis": 2,
        "excesso_real": 0,
        "excesso_comprometido": 0,
    }


def test_membro_consulta_recursos_efetivos_mas_nao_dados_financeiros():
    organizacao, assinatura = _assinatura_ativa(slug="api-recursos")
    membro = _usuario_na(organizacao, email="membro-recursos@example.com", papel=Papel.MEMBRO)
    client = _client(membro, organizacao)

    recursos = client.get("/assinatura/recursos/")
    contrato = client.get("/assinatura/")

    assert recursos.status_code == 200
    assert recursos.json() == ValoresRecursos(CATALOGO_RECURSOS, assinatura.recursos).materializar()
    assert contrato.status_code == 403
    assert contrato.json()["errors"][0]["code"] == "organizations.role_insufficient"


def test_consulta_assinatura_expoe_duas_carencias_e_o_menor_prazo():
    organizacao, assinatura = _assinatura_ativa(slug="api-situacao-acesso", seats=1)
    proprietario = _usuario_na(organizacao, email="owner-situacao-acesso@example.com", papel=Papel.PROPRIETARIO)
    _usuario_na(organizacao, email="membro-situacao-acesso@example.com", papel=Papel.MEMBRO)
    agora = timezone.now()
    inicio_pagamento = agora - timedelta(days=2)
    inicio_seats = agora - timedelta(days=1)
    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura = Assinaturas.registrar_falha_renovacao(assinatura, agora=inicio_pagamento)
        Assinaturas.reconciliar_carencia_seats(assinatura, agora=inicio_seats)

    resposta = _client(proprietario, organizacao).get("/assinatura/")

    assert resposta.status_code == 200
    situacao = resposta.json()["situacao_acesso"]
    assert situacao["status"] == StatusAcesso.EM_CARENCIA
    assert situacao["motivos"] == [
        MotivoRestricao.PAYMENT_GRACE_PERIOD,
        MotivoRestricao.SEAT_OVERAGE_GRACE_PERIOD,
    ]
    assert parse_datetime(situacao["regularizar_ate"]) == inicio_pagamento + timedelta(days=7)


def test_consultas_financeiras_recusam_api_key_mesmo_com_scope():
    organizacao, _ = _assinatura_ativa(slug="api-key-financeiro")
    proprietario = _usuario_na(organizacao, email="owner-api-key-billing@example.com", papel=Papel.PROPRIETARIO)
    token, plain_token = AuthToken.objects.create(
        user=proprietario,
        type=TokenType.API_KEY,
        organization=organizacao,
        name="Billing indevido",
        scopes=[],
        created_by=proprietario,
    )
    TokenMetaData.objects.create(token=token)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {plain_token}")

    response = client.get("/assinatura/")

    assert response.status_code == 403


def test_proprietario_solicita_alteracao_idempotente_com_revisao_e_recencia():
    organizacao, assinatura = _assinatura_ativa(slug="api-alteracao", seats=2)
    proprietario = _usuario_na(organizacao, email="owner-alteracao@example.com", papel=Papel.PROPRIETARIO)
    payload = {
        "tipo": TipoAlteracaoAssinatura.AUMENTO_SEATS,
        "seats_contratados": 4,
        "revisao_esperada": assinatura.revisao,
        "chave_idempotencia": "http:aumento:1",
    }
    client = _client(proprietario, organizacao)

    primeira = client.post("/assinatura/alteracoes/", payload, format="json")
    repetida = client.post("/assinatura/alteracoes/", payload, format="json")

    assert primeira.status_code == repetida.status_code == 201
    assert primeira.json() == repetida.json()
    assert primeira.json()["status"] == StatusAlteracaoAssinatura.SOLICITADA
    with organizacao_atual_privilegiada(organizacao.pk):
        alteracao = AlteracaoAssinatura.objects.get(pk=primeira.json()["id"])
    assert alteracao.solicitada_por_id == proprietario.pk
    assert alteracao.snapshot_pretendido["seats_contratados"] == 4


def test_servico_de_encerramento_revalida_proprietario_sob_lock():
    organizacao, assinatura = _assinatura_paga(slug="encerramento-ator-rebaixado")
    ator = _usuario_na(
        organizacao,
        email="ator-rebaixado-encerramento@example.com",
        papel=Papel.ADMINISTRADOR,
    )

    with organizacao_atual_privilegiada(organizacao.pk), pytest.raises(APIError) as erro:
        Assinaturas.solicitar_encerramento(
            organizacao,
            agora=timezone.now(),
            revisao_esperada=assinatura.revisao,
            ator=ator,
        )

    assert erro.value.code == "organizations.role_insufficient"
    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura.refresh_from_db()
        assert assinatura.cancelamento_agendado_para is None


def test_mutacao_contratual_exige_email_verificado():
    organizacao, assinatura = _assinatura_ativa(slug="api-alteracao-email-nao-verificado", seats=2)
    proprietario = _usuario_na(
        organizacao,
        email="owner-alteracao-email-nao-verificado@example.com",
        papel=Papel.PROPRIETARIO,
        email_verificado=False,
    )

    response = _client(proprietario, organizacao).post(
        "/assinatura/alteracoes/",
        {
            "tipo": TipoAlteracaoAssinatura.AUMENTO_SEATS,
            "seats_contratados": assinatura.seats_contratados + 1,
            "revisao_esperada": assinatura.revisao,
            "chave_idempotencia": "email-nao-verificado",
        },
        format="json",
    )

    assert response.status_code == 403
    assert response.json()["errors"][0]["code"] == "account.email_not_verified"
    with organizacao_atual_privilegiada(organizacao.pk):
        assert not AlteracaoAssinatura.objects.filter(organizacao=organizacao).exists()


def test_proprietario_solicita_downgrade_e_mudanca_de_periodicidade_com_termos_publicados():
    organizacao_plano, assinatura_plano = _assinatura_paga(slug="api-downgrade")
    owner_plano = _usuario_na(organizacao_plano, email="owner-downgrade@example.com", papel=Papel.PROPRIETARIO)
    versao_gratuita, _ = CatalogoPlanos.obter_versao_inicial(codigo="gratuito", periodicidade=Periodicidade.MENSAL)
    downgrade = _client(owner_plano, organizacao_plano).post(
        "/assinatura/alteracoes/",
        {
            "tipo": TipoAlteracaoAssinatura.DOWNGRADE_PLANO,
            "versao_plano_id": versao_gratuita.pk,
            "revisao_esperada": assinatura_plano.revisao,
            "chave_idempotencia": "http:downgrade:1",
        },
        format="json",
    )

    organizacao_ciclo, assinatura_ciclo = _assinatura_paga(slug="api-periodicidade")
    owner_ciclo = _usuario_na(organizacao_ciclo, email="owner-periodicidade@example.com", papel=Papel.PROPRIETARIO)
    ciclo = _client(owner_ciclo, organizacao_ciclo).post(
        "/assinatura/alteracoes/",
        {
            "tipo": TipoAlteracaoAssinatura.MUDANCA_PERIODICIDADE,
            "periodicidade": Periodicidade.ANUAL,
            "revisao_esperada": assinatura_ciclo.revisao,
            "chave_idempotencia": "http:ciclo:1",
        },
        format="json",
    )

    assert downgrade.status_code == ciclo.status_code == 201
    with organizacao_atual_privilegiada(organizacao_plano.pk):
        alteracao_plano = AlteracaoAssinatura.objects.get(pk=downgrade.json()["id"])
    with organizacao_atual_privilegiada(organizacao_ciclo.pk):
        alteracao_ciclo = AlteracaoAssinatura.objects.get(pk=ciclo.json()["id"])
    assert alteracao_plano.snapshot_pretendido["versao_plano_id"] == versao_gratuita.pk
    assert alteracao_ciclo.snapshot_pretendido["periodicidade"] == Periodicidade.ANUAL


@pytest.mark.parametrize("papel", [Papel.ADMINISTRADOR, Papel.GESTOR, Papel.MEMBRO])
def test_alteracao_exige_proprietario(papel):
    organizacao, assinatura = _assinatura_ativa(slug=f"api-alteracao-papel-{papel}")
    usuario = _usuario_na(organizacao, email=f"alteracao-{papel}@example.com", papel=papel)

    response = _client(usuario, organizacao).post(
        "/assinatura/alteracoes/",
        {
            "tipo": TipoAlteracaoAssinatura.AUMENTO_SEATS,
            "seats_contratados": assinatura.seats_contratados + 1,
            "revisao_esperada": assinatura.revisao,
            "chave_idempotencia": f"sem-papel:{papel}",
        },
        format="json",
    )

    assert response.status_code == 403
    assert response.json()["errors"][0]["code"] == "organizations.role_insufficient"


def test_alteracao_exige_reautenticacao_recente():
    organizacao, assinatura = _assinatura_ativa(slug="api-alteracao-recencia")
    proprietario = _usuario_na(organizacao, email="owner-sem-recencia@example.com", papel=Papel.PROPRIETARIO)

    response = _client(proprietario, organizacao, recente=False).post(
        "/assinatura/alteracoes/",
        {
            "tipo": TipoAlteracaoAssinatura.AUMENTO_SEATS,
            "seats_contratados": assinatura.seats_contratados + 1,
            "revisao_esperada": assinatura.revisao,
            "chave_idempotencia": "sem-recencia",
        },
        format="json",
    )

    assert response.status_code == 401
    assert response.json()["errors"][0]["code"] == "auth.reauthentication_required"


def test_proprietario_agenda_e_desfaz_cancelamento_com_revisao():
    organizacao, assinatura = _assinatura_paga(slug="api-cancelamento")
    proprietario = _usuario_na(organizacao, email="owner-cancelamento@example.com", papel=Papel.PROPRIETARIO)
    client = _client(proprietario, organizacao)

    agendada = client.post(
        "/assinatura/cancelamento/",
        {"revisao_esperada": assinatura.revisao},
        format="json",
    )
    cancelada = client.delete(
        "/assinatura/cancelamento/",
        {"revisao_esperada": assinatura.revisao + 1},
        format="json",
    )

    assert agendada.status_code == 202
    assert agendada.json()["revisao"] == assinatura.revisao + 1
    assert parse_datetime(agendada.json()["cancelamento_agendado_para"]) == assinatura.periodo_atual_termina_em
    assert cancelada.status_code == 204
    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura.refresh_from_db()
    assert assinatura.cancelamento_agendado_para is None
    assert assinatura.revisao == 4


@pytest.mark.parametrize("method", ["post", "delete"])
def test_cancelamento_exige_email_verificado(method):
    organizacao, assinatura = _assinatura_paga(slug=f"api-cancelamento-email-nao-verificado-{method}")
    proprietario = _usuario_na(
        organizacao,
        email=f"owner-cancelamento-email-nao-verificado-{method}@example.com",
        papel=Papel.PROPRIETARIO,
        email_verificado=False,
    )

    response = getattr(_client(proprietario, organizacao), method)(
        "/assinatura/cancelamento/",
        {"revisao_esperada": assinatura.revisao},
        format="json",
    )

    assert response.status_code == 403
    assert response.json()["errors"][0]["code"] == "account.email_not_verified"
    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura.refresh_from_db()
    assert assinatura.cancelamento_agendado_para is None
    assert assinatura.revisao == 2


def test_cancelamento_imediato_de_trial_encerra_contrato_e_retorna_estado_terminal():
    organizacao, assinatura = _trial(slug="api-cancelamento-imediato")
    proprietario = _usuario_na(organizacao, email="owner-cancelamento-imediato@example.com", papel=Papel.PROPRIETARIO)

    resposta = _client(proprietario, organizacao).post(
        "/assinatura/cancelamento/",
        {"revisao_esperada": assinatura.revisao},
        format="json",
    )

    assert resposta.status_code == 200
    assert resposta.json()["status"] == StatusAssinatura.ENCERRADA
    assert resposta.json()["revisao"] == assinatura.revisao + 1
    assert resposta.json()["cancelamento_agendado_para"] is None
    assert parse_datetime(resposta.json()["encerrada_em"]) is not None
    assert resposta.json()["motivo_encerramento"] == "subscription_cancelled"
    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura.refresh_from_db()
    assert assinatura.status == StatusAssinatura.ENCERRADA
    assert assinatura.motivo_encerramento == "subscription_cancelled"


def test_cancelamento_com_revisao_obsoleta_retorna_conflito():
    organizacao, assinatura = _assinatura_paga(slug="api-cancelamento-revisao")
    proprietario = _usuario_na(organizacao, email="owner-conflito-cancelamento@example.com", papel=Papel.PROPRIETARIO)

    response = _client(proprietario, organizacao).post(
        "/assinatura/cancelamento/",
        {"revisao_esperada": assinatura.revisao + 1},
        format="json",
    )

    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "billing.subscription_conflict"
