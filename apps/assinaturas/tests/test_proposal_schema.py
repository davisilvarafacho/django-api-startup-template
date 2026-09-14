"""Contrato OpenAPI literal do aceite de proposta."""

from django.urls import resolve

import pytest
from drf_spectacular.generators import SchemaGenerator

from apps.api.core.errors import discover_error_codes
from apps.assinaturas.subapps.faturamento import urls as faturamento_urls
from apps.assinaturas.subapps.faturamento.views import (
    AceitarPropostaView,
    CriarCheckoutAssinaturaView,
    CriarCheckoutFormaPagamentoView,
    ListarCheckoutsView,
    ListarFaturasView,
)


@pytest.fixture(autouse=True)
def _sem_versionamento_e_registry_populado(monkeypatch):
    for view in (
        AceitarPropostaView,
        CriarCheckoutAssinaturaView,
        CriarCheckoutFormaPagamentoView,
        ListarCheckoutsView,
        ListarFaturasView,
    ):
        monkeypatch.setattr(view, "versioning_class", None)
    discover_error_codes(force=True)
    yield
    discover_error_codes(force=True)


def test_aceite_documenta_body_resposta_e_erros_reais():
    schema = SchemaGenerator(patterns=faturamento_urls.urlpatterns).get_schema(request=None, public=True)
    post = schema["paths"]["/assinatura/propostas/{id}/aceitar/"]["post"]

    assert post["requestBody"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/AceitarPropostaRequest"}
    assert post["responses"]["200"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/AceitarPropostaResponse"}
    assert set(post["responses"]) == {"200", "400", "401", "403", "404", "409", "422", "503"}
    for codigo in (
        "auth.token_not_provided",
        "auth.invalid_token",
        "auth.expired_token",
        "auth.revoked_token",
        "auth.reauthentication_required",
    ):
        assert codigo in post["responses"]["401"]["description"]
    assert "organizations.membership_required" in post["responses"]["403"]["description"]
    assert "organizations.membership_inactive" in post["responses"]["403"]["description"]
    assert "organizations.organization_inactive" in post["responses"]["403"]["description"]
    assert "organizations.role_insufficient" in post["responses"]["403"]["description"]
    assert "billing.organization_restricted" in post["responses"]["403"]["description"]
    assert "account.email_not_verified" in post["responses"]["403"]["description"]
    assert "core.not_found" in post["responses"]["404"]["description"]
    assert "billing.proposal_invalid" in post["responses"]["409"]["description"]
    assert "organizations.tenant_mismatch" in post["responses"]["409"]["description"]
    assert "billing.checkout_pending" in post["responses"]["409"]["description"]
    assert "billing.checkout_conflict" in post["responses"]["409"]["description"]
    assert "organizations.header_required" in post["responses"]["422"]["description"]
    assert "billing.checkout_unavailable" in post["responses"]["422"]["description"]
    assert "billing.subscription_required" in post["responses"]["503"]["description"]
    assert "billing.checkout_uncertain" in post["responses"]["503"]["description"]


@pytest.mark.parametrize(
    "path",
    [
        "/assinatura/checkouts/",
        "/faturamento/forma-pagamento/checkouts/",
    ],
)
def test_checkouts_documentam_precondicao_de_email_verificado(path):
    schema = SchemaGenerator(patterns=faturamento_urls.urlpatterns).get_schema(request=None, public=True)

    assert "account.email_not_verified" in schema["paths"][path]["post"]["responses"]["403"]["description"]


def test_rota_de_aceite_e_owned_pelo_subapp_faturamento():
    view = resolve("/assinatura/propostas/1/aceitar/").func.view_class
    assert view is AceitarPropostaView
    assert view.__module__ == "apps.assinaturas.subapps.faturamento.views"


@pytest.mark.parametrize("path", ["/faturamento/checkouts/", "/faturamento/faturas/"])
def test_listagens_documentam_paginacao_limitada(path):
    schema = SchemaGenerator(patterns=faturamento_urls.urlpatterns).get_schema(request=None, public=True)
    parameters = {parameter["name"]: parameter for parameter in schema["paths"][path]["get"]["parameters"]}

    assert set(parameters) == {"page", "size"}
    assert parameters["page"]["schema"] == {"type": "integer", "minimum": 1}
    assert parameters["size"]["schema"] == {"type": "integer", "minimum": 1, "maximum": 50}
