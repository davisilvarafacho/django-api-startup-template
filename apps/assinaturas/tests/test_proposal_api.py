"""Contrato HTTP fino do aceite de proposta comercial."""

from datetime import timedelta
from types import SimpleNamespace

from django.utils import timezone

from rest_framework.test import APIClient

import pytest
from drf_spectacular.generators import SchemaGenerator

from apps.api.autenticacao.models import AuthToken, TokenMetaData, TokenType
from apps.api.core.errors import discover_error_codes
from apps.assinaturas import urls as assinaturas_urls
from apps.assinaturas.catalogs import PLANOS_BOOTSTRAP, CatalogoPlanos, sincronizar_planos
from apps.assinaturas.features import CATALOGO_RECURSOS, ValoresRecursos
from apps.assinaturas.models import ModoAtivacaoProposta, Periodicidade, StatusPropostaComercial
from apps.assinaturas.proposals import CriacaoPropostaComercial, Propostas
from apps.assinaturas.subscriptions import Assinaturas, TermosAssinatura
from apps.assinaturas.views import AceitarPropostaView
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
    sincronizar_planos(PLANOS_BOOTSTRAP, aplicar=True)
    organizacao = Organizacao.objects.create(nome=nome, slug=slug)
    Vinculo.objects.create(organizacao=organizacao, usuario=usuario, papel=papel)
    versao, preco = CatalogoPlanos.obter_versao_inicial(codigo="gratuito", periodicidade=Periodicidade.MENSAL)
    with organizacao_atual_privilegiada(organizacao.pk):
        Assinaturas.criar_gratuita(organizacao=organizacao, versao_plano=versao, preco_plano=preco)
    return organizacao


def test_proprietario_aceita_proposta_com_revisao_e_recebe_checkout_autoritativo(monkeypatch):
    proprietario = criar_usuario(email="owner-proposta-api@example.com")
    organizacao = _organizacao_do(proprietario, nome="Proposta API", slug="proposta-api")
    proposta = _proposta_enviada(organizacao)
    from apps.assinaturas.subapps.faturamento import checkouts as billing_checkouts

    monkeypatch.setattr(
        billing_checkouts,
        "criar_checkout",
        lambda criacao: SimpleNamespace(checkout=SimpleNamespace(pk=91, url="https://checkout.example/proposta")),
    )

    response = _client(proprietario, organizacao).post(
        f"/assinatura/propostas/{proposta.pk}/aceitar/",
        {"revisao_esperada": proposta.revisao, "chave_idempotencia": "aceite-proposta-1"},
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
            "checkout_id": 91,
            "checkout_url": "https://checkout.example/proposta",
        },
    }


def test_proposta_pagamento_sem_chave_nao_e_aceita():
    proprietario = criar_usuario(email="owner-proposta-sem-chave@example.com")
    organizacao = _organizacao_do(proprietario, nome="Sem chave", slug="proposta-sem-chave")
    proposta = _proposta_enviada(organizacao)

    response = _client(proprietario, organizacao).post(
        f"/assinatura/propostas/{proposta.pk}/aceitar/", {"revisao_esperada": proposta.revisao}, format="json"
    )

    assert response.status_code == 400
    with organizacao_atual_privilegiada(organizacao.pk):
        proposta.refresh_from_db()
    assert proposta.status == StatusPropostaComercial.ENVIADA


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
        {"revisao_esperada": 99, "chave_idempotencia": "revisao-invalida"},
        format="json",
    )

    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "billing.proposal_invalid"


def test_erros_reais_de_auth_e_tenant_estao_documentados_no_status_runtime(monkeypatch):
    proprietario = criar_usuario(email="owner-schema-runtime@example.com")
    organizacao = _organizacao_do(proprietario, nome="Schema runtime", slug="proposta-schema-runtime")
    url = "/assinatura/propostas/999999999/aceitar/"
    payload = {"revisao_esperada": 1}
    respostas = []

    respostas.append(APIClient().post(url, payload, format="json", HTTP_X_ORGANIZATION=organizacao.slug))
    respostas.append(
        APIClient().post(
            url,
            payload,
            format="json",
            HTTP_AUTHORIZATION="Bearer token-invalido",
            HTTP_X_ORGANIZATION=organizacao.slug,
        )
    )

    expirado = _client(proprietario, organizacao)
    AuthToken.objects.filter(metadata__isnull=False, responsavel=proprietario).update(expiry=timezone.now() - timedelta(seconds=1))
    respostas.append(expirado.post(url, payload, format="json"))

    revogado = _client(proprietario, organizacao)
    AuthToken.objects.filter(metadata__isnull=False, responsavel=proprietario, revoked_at__isnull=True).update(revoked_at=timezone.now())
    respostas.append(revogado.post(url, payload, format="json"))

    sem_header = _client(proprietario, organizacao)
    sem_header.defaults.pop(META_HEADER_ORGANIZACAO)
    respostas.append(sem_header.post(url, payload, format="json"))

    sem_vinculo = criar_usuario(email="sem-vinculo-schema-runtime@example.com")
    respostas.append(_client(sem_vinculo, organizacao).post(url, payload, format="json"))

    vinculo_inativo = criar_usuario(email="vinculo-inativo-schema-runtime@example.com")
    org_vinculo_inativo = _organizacao_do(vinculo_inativo, nome="Vínculo inativo schema", slug="vinculo-inativo-schema")
    Vinculo.objects.filter(organizacao=org_vinculo_inativo, usuario=vinculo_inativo).update(is_active=False)
    respostas.append(_client(vinculo_inativo, org_vinculo_inativo).post(url, payload, format="json"))

    org_inativa_usuario = criar_usuario(email="org-inativa-schema-runtime@example.com")
    org_inativa = _organizacao_do(org_inativa_usuario, nome="Org inativa schema", slug="org-inativa-schema")
    Organizacao.objects.filter(pk=org_inativa.pk).update(is_active=False)
    respostas.append(_client(org_inativa_usuario, org_inativa).post(url, payload, format="json"))

    api_key, plain_api_key = AuthToken.objects.create(
        user=proprietario,
        type=TokenType.API_KEY,
        organization=organizacao,
        name="Schema runtime",
        scopes=[],
        created_by=proprietario,
    )
    TokenMetaData.objects.create(token=api_key)
    tenant_divergente = Organizacao.objects.create(nome="Tenant divergente", slug="tenant-divergente-schema")
    client_api_key = APIClient()
    client_api_key.credentials(HTTP_AUTHORIZATION=f"Bearer {plain_api_key}")
    respostas.append(client_api_key.post(url, payload, format="json", HTTP_X_ORGANIZATION=tenant_divergente.slug))

    monkeypatch.setattr(AceitarPropostaView, "versioning_class", None)
    discover_error_codes(force=True)
    schema = SchemaGenerator(patterns=assinaturas_urls.urlpatterns).get_schema(request=None, public=True)
    documentadas = schema["paths"]["/assinatura/propostas/{id}/aceitar/"]["post"]["responses"]

    observados = {(str(response.status_code), response.json()["errors"][0]["code"]) for response in respostas}
    esperados = {
        ("401", "auth.token_not_provided"),
        ("401", "auth.invalid_token"),
        ("401", "auth.expired_token"),
        ("401", "auth.revoked_token"),
        ("422", "organizations.header_required"),
        ("403", "organizations.membership_required"),
        ("403", "organizations.membership_inactive"),
        ("403", "organizations.organization_inactive"),
        ("409", "organizations.tenant_mismatch"),
    }
    assert observados == esperados
    for status_code, codigo in observados:
        assert codigo in documentadas[status_code]["description"]
    assert "billing.organization_restricted" in documentadas["403"]["description"]
    assert "billing.subscription_required" in documentadas["503"]["description"]
