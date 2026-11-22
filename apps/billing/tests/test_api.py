"""Testes da camada HTTP de cobrança: catálogo, assinatura, checkout, troca de plano e cancelamento."""

from rest_framework import status
from rest_framework.test import APIClient

import pytest
from django_checkouts.enums import Gateway
from django_checkouts.exceptions import GatewayTemporaryError
from django_checkouts.gateways.commands import CreateCheckout
from knox.models import get_token_model

from apps.api.autenticacao.errors import AuthErrorCode
from apps.api.autenticacao.models import TokenMetaData, TokenType
from apps.billing.models import Plan
from apps.billing.subscriptions import Subscriptions
from apps.organizacoes.constants import META_HEADER_ORGANIZACAO
from apps.organizacoes.models import Organizacao, Papel, Vinculo
from internal_frameworks.context import ContextVariable
from tests.support.billing import fake_checkout, patch_billing_gateway
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db

AuthToken = get_token_model()
IDEMPOTENCY_HEADER = "HTTP_IDEMPOTENCY_KEY"


@pytest.fixture(autouse=True)
def configure_test_request(settings):
    settings.ALLOWED_HOSTS = ["testserver"]
    ContextVariable.clear_context()
    yield
    ContextVariable.clear_context()


def authenticated_client(user):
    _, token = AuthToken.objects.create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def api_key_client(user, scopes, organization):
    instance, token = AuthToken.objects.create(
        responsavel=user,
        type=TokenType.API_KEY,
        created_by=user,
        organization=organization,
        name="Integração de teste",
        scopes=scopes,
    )
    TokenMetaData.objects.create(token=instance)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def _organization(slug="acme"):
    return Organizacao.objects.create(nome=slug, slug=slug)


def _link(user, organization, role):
    return Vinculo.objects.create(usuario=user, organizacao=organization, papel=role)


def _paid_plan(slug="pro", *, rank=2, price="price_pro"):
    return Plan.objects.create(name=slug, slug=slug, rank=rank, member_limit=10, gateway_price_id=price)


def test_plans_list_without_organization_header():
    user = criar_usuario()
    _paid_plan("pro")
    _paid_plan("inativo", rank=3, price="price_inativo")
    Plan.objects.filter(slug="inativo").update(is_active=False)

    response = authenticated_client(user).get("/cobranca/planos/")

    assert response.status_code == status.HTTP_200_OK
    slugs = [plan["slug"] for plan in response.json()["resultados"]]
    assert "pro" in slugs
    assert "inativo" not in slugs


def test_subscription_requires_admin():
    organization = _organization()
    member = criar_usuario()
    admin = criar_usuario()
    _link(member, organization, Papel.MEMBRO)
    _link(admin, organization, Papel.ADMINISTRADOR)
    Subscriptions.ensure_free(organization.pk)

    denied = authenticated_client(member).get("/cobranca/assinatura/", **{META_HEADER_ORGANIZACAO: "acme"})
    allowed = authenticated_client(admin).get("/cobranca/assinatura/", **{META_HEADER_ORGANIZACAO: "acme"})

    assert denied.status_code == status.HTTP_403_FORBIDDEN
    assert allowed.status_code == status.HTTP_200_OK
    body = allowed.json()
    assert body["plan"]["is_free"] is True
    assert body["has_access"] is True
    assert body["members"] == {"active": 2, "limit": Plan.get_free().member_limit}


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/cobranca/assinatura/checkout/"),
        ("post", "/cobranca/assinatura/alteracao/"),
        ("post", "/cobranca/assinatura/cancelamento/"),
        ("delete", "/cobranca/assinatura/cancelamento/"),
    ],
)
def test_mutations_require_owner(method, path):
    organization = _organization()
    admin = criar_usuario()
    _link(admin, organization, Papel.ADMINISTRADOR)
    Subscriptions.ensure_free(organization.pk)
    client = authenticated_client(admin)

    response = getattr(client, method)(path, {"plan": "pro"}, format="json", **{META_HEADER_ORGANIZACAO: "acme", IDEMPOTENCY_HEADER: "k"})

    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/cobranca/assinatura/checkout/"),
        ("post", "/cobranca/assinatura/alteracao/"),
        ("post", "/cobranca/assinatura/cancelamento/"),
        ("delete", "/cobranca/assinatura/cancelamento/"),
    ],
)
def test_mutations_reject_api_keys(method, path):
    organization = _organization()
    owner = criar_usuario()
    _link(owner, organization, Papel.PROPRIETARIO)
    Subscriptions.ensure_free(organization.pk)
    client = api_key_client(owner, ["organizations:read"], organization)

    response = getattr(client, method)(path, {"plan": "pro"}, format="json", **{IDEMPOTENCY_HEADER: "k"})

    assert response.status_code == status.HTTP_403_FORBIDDEN
    # Só `session_only` recusa a API key antes da checagem de escopos. Sem ele, a view não
    # declara escopo e a recusa viria como `auth.insufficient_scope`.
    assert response.json()["errors"][0]["code"] == AuthErrorCode.PERMISSION_DENIED


def test_mutation_requires_idempotency_key():
    organization = _organization()
    owner = criar_usuario()
    _link(owner, organization, Papel.PROPRIETARIO)
    Subscriptions.ensure_free(organization.pk)
    _paid_plan()

    response = authenticated_client(owner).post(
        "/cobranca/assinatura/checkout/",
        {"plan": "pro"},
        format="json",
        **{META_HEADER_ORGANIZACAO: "acme"},
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert response.json()["errors"][0]["code"] == "billing.idempotency_key_required"


def test_checkout_returns_url(monkeypatch):
    organization = _organization()
    owner = criar_usuario()
    _link(owner, organization, Papel.PROPRIETARIO)
    Subscriptions.ensure_free(organization.pk)
    _paid_plan()
    patch_billing_gateway(monkeypatch, {CreateCheckout: fake_checkout()})

    response = authenticated_client(owner).post(
        "/cobranca/assinatura/checkout/",
        {"plan": "pro"},
        format="json",
        **{META_HEADER_ORGANIZACAO: "acme", IDEMPOTENCY_HEADER: "idem-1"},
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert response.json() == {"url": "https://checkout.example/sessao"}


def test_gateway_temporary_error_returns_503(monkeypatch):
    organization = _organization()
    owner = criar_usuario()
    _link(owner, organization, Papel.PROPRIETARIO)
    Subscriptions.ensure_free(organization.pk)
    _paid_plan()

    def temporary_failure(command):
        raise GatewayTemporaryError("indisponível", gateway=Gateway.STRIPE, variant="stripe")

    patch_billing_gateway(monkeypatch, {CreateCheckout: temporary_failure})

    response = authenticated_client(owner).post(
        "/cobranca/assinatura/checkout/",
        {"plan": "pro"},
        format="json",
        **{META_HEADER_ORGANIZACAO: "acme", IDEMPOTENCY_HEADER: "idem-1"},
    )

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json()["errors"][0]["code"] == "billing.gateway_uncertain"
