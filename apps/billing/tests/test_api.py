"""Testes da camada HTTP de cobrança: catálogo, assinatura, checkout, troca de plano e cancelamento."""

from rest_framework import status
from rest_framework.test import APIClient

import pytest
from django_checkouts.client import CheckoutClient
from django_checkouts.enums import CheckoutMode, Gateway
from django_checkouts.exceptions import GatewayTemporaryError
from django_checkouts.gateways.commands import CreateCheckout
from django_checkouts.types.checkouts import Checkout
from knox.models import get_token_model

from apps.api.autenticacao.models import TokenMetaData, TokenType
from apps.billing.models import Plan
from apps.billing.subscriptions import Subscriptions
from apps.organizacoes.constants import META_HEADER_ORGANIZACAO
from apps.organizacoes.models import Organizacao, Papel, Vinculo
from internal_frameworks.context import ContextVariable
from tests.support.billing import fake_gateway
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db

AuthToken = get_token_model()
IDEMPOTENCY_HEADER = "HTTP_IDEMPOTENCY_KEY"


@pytest.fixture(autouse=True)
def configurar_request_de_teste(settings):
    settings.ALLOWED_HOSTS = ["testserver"]
    ContextVariable.clear_context()
    yield
    ContextVariable.clear_context()


def client_autenticado(usuario):
    _, token = AuthToken.objects.create(user=usuario)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def client_com_api_key(usuario, scopes, organizacao):
    instance, token = AuthToken.objects.create(
        responsavel=usuario,
        type=TokenType.API_KEY,
        created_by=usuario,
        organization=organizacao,
        name="Integração de teste",
        scopes=scopes,
    )
    TokenMetaData.objects.create(token=instance)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def _organizacao(slug="acme"):
    return Organizacao.objects.create(nome=slug, slug=slug)


def _vincular(usuario, organizacao, papel):
    return Vinculo.objects.create(usuario=usuario, organizacao=organizacao, papel=papel)


def _plano_pago(slug="pro", *, rank=2, price="price_pro"):
    return Plan.objects.create(name=slug, slug=slug, rank=rank, member_limit=10, gateway_price_id=price)


def _gateway(monkeypatch, results):
    monkeypatch.setattr("apps.billing.subscriptions.get_client", lambda: CheckoutClient(fake_gateway(results)))


def _checkout(url="https://checkout.example/sessao"):
    return Checkout(
        external_id="cs_1",
        gateway=Gateway.STRIPE,
        variant="stripe",
        status="pending",
        mode=CheckoutMode.SUBSCRIPTION,
        url=url,
        amount_total=0,
        currency="BRL",
        customer=None,
        reference_id=None,
        subscription_id=None,
        expires_at=None,
        created_at=None,
        raw={},
    )


def test_plans_list_without_organization_header():
    usuario = criar_usuario()
    _plano_pago("pro")
    _plano_pago("inativo", rank=3, price="price_inativo")
    Plan.objects.filter(slug="inativo").update(is_active=False)

    response = client_autenticado(usuario).get("/cobranca/planos/")

    assert response.status_code == status.HTTP_200_OK
    slugs = [plano["slug"] for plano in response.json()["resultados"]]
    assert "pro" in slugs
    assert "inativo" not in slugs


def test_subscription_requires_admin():
    organizacao = _organizacao()
    membro = criar_usuario()
    administrador = criar_usuario()
    _vincular(membro, organizacao, Papel.MEMBRO)
    _vincular(administrador, organizacao, Papel.ADMINISTRADOR)
    Subscriptions.ensure_free(organizacao.pk)

    negado = client_autenticado(membro).get("/cobranca/assinatura/", **{META_HEADER_ORGANIZACAO: "acme"})
    permitido = client_autenticado(administrador).get("/cobranca/assinatura/", **{META_HEADER_ORGANIZACAO: "acme"})

    assert negado.status_code == status.HTTP_403_FORBIDDEN
    assert permitido.status_code == status.HTTP_200_OK
    corpo = permitido.json()
    assert corpo["plan"]["is_free"] is True
    assert corpo["has_access"] is True
    assert corpo["members"] == {"active": 2, "limit": Plan.get_free().member_limit}


@pytest.mark.parametrize(
    ("metodo", "caminho"),
    [
        ("post", "/cobranca/assinatura/checkout/"),
        ("post", "/cobranca/assinatura/alteracao/"),
        ("post", "/cobranca/assinatura/cancelamento/"),
        ("delete", "/cobranca/assinatura/cancelamento/"),
    ],
)
def test_mutations_require_owner(metodo, caminho):
    organizacao = _organizacao()
    administrador = criar_usuario()
    _vincular(administrador, organizacao, Papel.ADMINISTRADOR)
    Subscriptions.ensure_free(organizacao.pk)
    cliente = client_autenticado(administrador)

    response = getattr(cliente, metodo)(caminho, {"plan": "pro"}, format="json", **{META_HEADER_ORGANIZACAO: "acme", IDEMPOTENCY_HEADER: "k"})

    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.parametrize(
    ("metodo", "caminho"),
    [
        ("post", "/cobranca/assinatura/checkout/"),
        ("post", "/cobranca/assinatura/alteracao/"),
        ("post", "/cobranca/assinatura/cancelamento/"),
        ("delete", "/cobranca/assinatura/cancelamento/"),
    ],
)
def test_mutations_reject_api_keys(metodo, caminho):
    organizacao = _organizacao()
    proprietario = criar_usuario()
    _vincular(proprietario, organizacao, Papel.PROPRIETARIO)
    Subscriptions.ensure_free(organizacao.pk)
    cliente = client_com_api_key(proprietario, ["organizations:read"], organizacao)

    response = getattr(cliente, metodo)(caminho, {"plan": "pro"}, format="json", **{IDEMPOTENCY_HEADER: "k"})

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_mutation_requires_idempotency_key():
    organizacao = _organizacao()
    proprietario = criar_usuario()
    _vincular(proprietario, organizacao, Papel.PROPRIETARIO)
    Subscriptions.ensure_free(organizacao.pk)
    _plano_pago()

    response = client_autenticado(proprietario).post(
        "/cobranca/assinatura/checkout/",
        {"plan": "pro"},
        format="json",
        **{META_HEADER_ORGANIZACAO: "acme"},
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert response.json()["errors"][0]["code"] == "billing.idempotency_key_required"


def test_checkout_returns_url(monkeypatch):
    organizacao = _organizacao()
    proprietario = criar_usuario()
    _vincular(proprietario, organizacao, Papel.PROPRIETARIO)
    Subscriptions.ensure_free(organizacao.pk)
    _plano_pago()
    _gateway(monkeypatch, {CreateCheckout: _checkout()})

    response = client_autenticado(proprietario).post(
        "/cobranca/assinatura/checkout/",
        {"plan": "pro"},
        format="json",
        **{META_HEADER_ORGANIZACAO: "acme", IDEMPOTENCY_HEADER: "idem-1"},
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert response.json() == {"url": "https://checkout.example/sessao"}


def test_gateway_temporary_error_returns_503(monkeypatch):
    organizacao = _organizacao()
    proprietario = criar_usuario()
    _vincular(proprietario, organizacao, Papel.PROPRIETARIO)
    Subscriptions.ensure_free(organizacao.pk)
    _plano_pago()

    def falha_temporaria(command):
        raise GatewayTemporaryError("indisponível", gateway=Gateway.STRIPE, variant="stripe")

    _gateway(monkeypatch, {CreateCheckout: falha_temporaria})

    response = client_autenticado(proprietario).post(
        "/cobranca/assinatura/checkout/",
        {"plan": "pro"},
        format="json",
        **{META_HEADER_ORGANIZACAO: "acme", IDEMPOTENCY_HEADER: "idem-1"},
    )

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json()["errors"][0]["code"] == "billing.gateway_uncertain"
