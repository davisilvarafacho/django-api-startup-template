from django.utils import timezone

from rest_framework.test import APIClient

import pytest
from drf_spectacular.generators import SchemaGenerator

from apps.api.autenticacao.models import AuthToken, TokenMetaData, TokenType
from apps.api.core.errors import discover_error_codes
from apps.api.core.route_markers import MARCADOR_SEM_TENANCY, rota_tem_marcador
from apps.assinaturas import urls as assinaturas_urls
from apps.assinaturas.models import Periodicidade, Plano, PrecoPlano, VersaoPlano
from apps.assinaturas.views import CatalogoPlanosView
from apps.organizacoes.models import Organizacao, Papel, Vinculo
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db


def _versao_atual(plano, *, numero=1, ativa=True):
    return VersaoPlano.objects.create(
        plano=plano,
        numero=numero,
        atual=False,
        seats_inclusos=3,
        limite_seats_trial=2,
        duracao_trial_dias=14,
        carencia_pagamento_dias=7,
        carencia_excesso_seats_dias=5,
        expansao_automatica_seats=True,
        recursos={"quantidade_projetos": 10},
        is_active=ativa,
    )


def _publicar(versao):
    versao.publicada_em = timezone.now()
    versao.atual = True
    versao.save(update_fields=["publicada_em", "atual"])


def _preco(versao, *, periodicidade=Periodicidade.MENSAL, ativo=True):
    return PrecoPlano.objects.create(
        versao_plano=versao,
        periodicidade=periodicidade,
        moeda="BRL",
        valor_base_centavos=9_900,
        valor_seat_centavos=1_500,
        is_active=ativo,
    )


def test_get_planos_lista_apenas_catalogo_contratavel_sem_tenant():
    plano = Plano.objects.create(codigo="profissional-api", nome="Profissional", descricao="Para equipes", visivel=True)
    versao = _versao_atual(plano)
    preco_mensal = _preco(versao)
    preco_anual = _preco(versao, periodicidade=Periodicidade.ANUAL)
    _preco(versao, periodicidade=30, ativo=False)
    _publicar(versao)

    invisivel = Plano.objects.create(codigo="oculto-api", nome="Oculto", visivel=False)
    versao_invisivel = _versao_atual(invisivel)
    _preco(versao_invisivel)
    _publicar(versao_invisivel)
    inativo = Plano.objects.create(codigo="inativo-api", nome="Inativo", visivel=True, is_active=False)
    versao_inativa = _versao_atual(inativo)
    _preco(versao_inativa)
    _publicar(versao_inativa)
    rascunho = Plano.objects.create(codigo="rascunho-api", nome="Rascunho", visivel=True)
    _preco(_versao_atual(rascunho))

    usuario = criar_usuario(email_verificado_em=timezone.now())
    _, token = AuthToken.objects.create(user=usuario)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    response = client.get("/planos/")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": plano.pk,
            "codigo": "profissional-api",
            "nome": "Profissional",
            "descricao": "Para equipes",
            "versao": {
                "id": versao.pk,
                "numero": 1,
                "seats_inclusos": 3,
                "limite_seats_trial": 2,
                "duracao_trial_dias": 14,
                "carencia_pagamento_dias": 7,
                "carencia_excesso_seats_dias": 5,
                "expansao_automatica_seats": True,
                "recursos": {"quantidade_projetos": 10, "papeis_isentos_seat": []},
                "precos": [
                    {
                        "id": preco_mensal.pk,
                        "periodicidade": Periodicidade.MENSAL,
                        "moeda": "BRL",
                        "valor_base_centavos": 9_900,
                        "valor_seat_centavos": 1_500,
                    },
                    {
                        "id": preco_anual.pk,
                        "periodicidade": Periodicidade.ANUAL,
                        "moeda": "BRL",
                        "valor_base_centavos": 9_900,
                        "valor_seat_centavos": 1_500,
                    },
                ],
            },
        }
    ]
    assert rota_tem_marcador("/planos/", "GET", MARCADOR_SEM_TENANCY)


def test_get_planos_exige_autenticacao():
    assert APIClient().get("/planos/").status_code == 401


def test_get_planos_recusa_api_key_sem_resolver_assinatura():
    organizacao = Organizacao.objects.create(nome="Catálogo API key", slug="catalogo-api-key")
    usuario = criar_usuario(email="catalogo-api-key@example.com", email_verificado_em=timezone.now())
    Vinculo.objects.create(organizacao=organizacao, usuario=usuario, papel=Papel.PROPRIETARIO)
    token, plain_token = AuthToken.objects.create(
        user=usuario,
        type=TokenType.API_KEY,
        organization=organizacao,
        name="Catálogo",
        scopes=["*:*"],
        created_by=usuario,
    )
    TokenMetaData.objects.create(token=token)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {plain_token}")

    response = client.get("/planos/")

    assert response.status_code == 403
    assert response.json()["errors"][0]["code"] == "auth.permission_denied"


def test_get_planos_documenta_array_e_erro_de_autenticacao(monkeypatch):
    monkeypatch.setattr(CatalogoPlanosView, "versioning_class", None)
    discover_error_codes(force=True)

    schema = SchemaGenerator(patterns=assinaturas_urls.urlpatterns).get_schema(request=None, public=True)
    operacao = schema["paths"]["/planos/"]["get"]

    assert operacao["responses"]["200"]["content"]["application/json"]["schema"] == {
        "type": "array",
        "items": {"$ref": "#/components/schemas/PlanoCatalogo"},
    }
    assert set(operacao["responses"]) == {"200", "401", "403"}
    assert "auth.token_not_provided" in operacao["responses"]["401"]["description"]
    assert "auth.permission_denied" in operacao["responses"]["403"]["description"]
    versao_schema = schema["components"]["schemas"]["PlanoCatalogo"]["properties"]["versao"]
    assert versao_schema["allOf"] == [{"$ref": "#/components/schemas/VersaoPlanoCatalogo"}]
    assert versao_schema["readOnly"] is True
