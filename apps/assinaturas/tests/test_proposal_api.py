"""Contrato HTTP fino do aceite de proposta comercial."""

from datetime import timedelta

from django.utils import timezone

from rest_framework.test import APIClient

import pytest

from apps.api.autenticacao.models import AuthToken, TokenMetaData
from apps.assinaturas.features import CATALOGO_RECURSOS, ValoresRecursos
from apps.assinaturas.models import ModoAtivacaoProposta, Periodicidade, StatusPropostaComercial
from apps.assinaturas.proposals import CriacaoPropostaComercial, Propostas
from apps.assinaturas.subscriptions import TermosAssinatura
from apps.organizacoes.constants import META_HEADER_ORGANIZACAO
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Organizacao, Papel, Vinculo
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db


def _termos() -> TermosAssinatura:
    return TermosAssinatura(
        periodicidade=Periodicidade.ANUAL,
        moeda="BRL",
        valor_base_centavos=120_000,
        valor_seat_centavos=5_000,
        seats_inclusos=10,
        seats_contratados=25,
        expansao_automatica_seats=False,
        recursos=ValoresRecursos(CATALOGO_RECURSOS, {"quantidade_projetos": 100}),
        carencia_pagamento_dias=15,
        carencia_excesso_seats_dias=10,
    )


def _proposta_enviada(organizacao: Organizacao):
    proposta = Propostas.criar(
        CriacaoPropostaComercial(
            organizacao=organizacao,
            versao_plano_referencia=None,
            modo_ativacao=ModoAtivacaoProposta.PAGAMENTO,
            termos=_termos(),
            valida_ate=timezone.now() + timedelta(days=30),
        )
    )
    return Propostas.enviar(proposta, revisao_esperada=1)


def _client(usuario, organizacao: Organizacao, *, recente: bool = True) -> APIClient:
    token, plain_token = AuthToken.objects.create(user=usuario)
    TokenMetaData.objects.create(token=token, reauthenticated_at=timezone.now() if recente else None)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {plain_token}")
    client.defaults[META_HEADER_ORGANIZACAO] = organizacao.slug
    return client


def _organizacao_do(usuario, *, nome: str, slug: str, papel=Papel.PROPRIETARIO) -> Organizacao:
    organizacao = Organizacao.objects.create(nome=nome, slug=slug)
    Vinculo.objects.create(organizacao=organizacao, usuario=usuario, papel=papel)
    return organizacao


def test_proprietario_aceita_proposta_com_revisao_e_recebe_preparacao_tipificada():
    proprietario = criar_usuario(email="owner-proposta-api@example.com")
    organizacao = _organizacao_do(proprietario, nome="Proposta API", slug="proposta-api")
    proposta = _proposta_enviada(organizacao)

    response = _client(proprietario, organizacao).post(
        f"/assinatura/propostas/{proposta.pk}/aceitar/",
        {"revisao_esperada": proposta.revisao},
        format="json",
    )

    with organizacao_atual_privilegiada(organizacao.pk):
        proposta.refresh_from_db()
    assert response.status_code == 200
    assert response.json() == {
        "id": proposta.pk,
        "status": StatusPropostaComercial.ACEITA,
        "revisao": 3,
        "modo_ativacao": ModoAtivacaoProposta.PAGAMENTO,
        "preparacao_checkout": {
            "proposta_id": proposta.pk,
            "organizacao_id": organizacao.pk,
            "revisao": 3,
            "moeda": "BRL",
            "total_centavos": 195_000,
        },
    }


def test_aceite_exige_sessao_recente_e_papel_proprietario():
    proprietario = criar_usuario(email="owner-politicas-proposta@example.com")
    administrador = criar_usuario(email="admin-politicas-proposta@example.com")
    organizacao = _organizacao_do(proprietario, nome="Politicas", slug="proposta-politicas")
    Vinculo.objects.create(organizacao=organizacao, usuario=administrador, papel=Papel.ADMINISTRADOR)
    proposta = _proposta_enviada(organizacao)

    sem_recencia = _client(proprietario, organizacao, recente=False).post(
        f"/assinatura/propostas/{proposta.pk}/aceitar/",
        {"revisao_esperada": proposta.revisao},
        format="json",
    )
    sem_papel = _client(administrador, organizacao).post(
        f"/assinatura/propostas/{proposta.pk}/aceitar/",
        {"revisao_esperada": proposta.revisao},
        format="json",
    )

    assert sem_recencia.status_code == 401
    assert sem_recencia.json()["errors"][0]["code"] == "auth.reauthentication_required"
    assert sem_papel.status_code == 403
    assert sem_papel.json()["errors"][0]["code"] == "organizations.role_insufficient"


def test_proposta_alheia_e_inexistente_sao_indistinguiveis():
    proprietario = criar_usuario(email="owner-anti-enumeracao@example.com")
    outro = criar_usuario(email="owner-alheio-anti-enumeracao@example.com")
    organizacao = _organizacao_do(proprietario, nome="Atual", slug="proposta-atual")
    alheia = _organizacao_do(outro, nome="Alheia", slug="proposta-alheia")
    proposta_alheia = _proposta_enviada(alheia)
    client = _client(proprietario, organizacao)

    resposta_alheia = client.post(
        f"/assinatura/propostas/{proposta_alheia.pk}/aceitar/",
        {"revisao_esperada": proposta_alheia.revisao},
        format="json",
    )
    resposta_inexistente = client.post(
        "/assinatura/propostas/999999999/aceitar/",
        {"revisao_esperada": 1},
        format="json",
    )

    assert resposta_alheia.status_code == resposta_inexistente.status_code == 404
    assert resposta_alheia.json()["errors"][0]["code"] == resposta_inexistente.json()["errors"][0]["code"] == "core.not_found"


def test_revisao_invalida_retorna_conflito_publico_estavel():
    proprietario = criar_usuario(email="owner-conflito-proposta@example.com")
    organizacao = _organizacao_do(proprietario, nome="Conflito", slug="proposta-conflito")
    proposta = _proposta_enviada(organizacao)

    response = _client(proprietario, organizacao).post(
        f"/assinatura/propostas/{proposta.pk}/aceitar/",
        {"revisao_esperada": 99},
        format="json",
    )

    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "billing.proposal_invalid"
