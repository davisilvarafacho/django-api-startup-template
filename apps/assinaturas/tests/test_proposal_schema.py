"""Contrato OpenAPI literal do aceite de proposta."""

from django.urls import resolve

import pytest
from drf_spectacular.generators import SchemaGenerator

from apps.api.core.errors import discover_error_codes
from apps.assinaturas import urls as assinaturas_urls
from apps.assinaturas.subapps.faturamento.views import AceitarPropostaView


@pytest.fixture(autouse=True)
def _sem_versionamento_e_registry_populado(monkeypatch):
    monkeypatch.setattr(AceitarPropostaView, "versioning_class", None)
    discover_error_codes(force=True)
    yield
    discover_error_codes(force=True)


def test_aceite_documenta_body_resposta_e_erros_reais():
    schema = SchemaGenerator(patterns=assinaturas_urls.urlpatterns).get_schema(request=None, public=True)
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
    assert "core.not_found" in post["responses"]["404"]["description"]
    assert "billing.proposal_invalid" in post["responses"]["409"]["description"]
    assert "organizations.tenant_mismatch" in post["responses"]["409"]["description"]
    assert "billing.checkout_pending" in post["responses"]["409"]["description"]
    assert "billing.checkout_conflict" in post["responses"]["409"]["description"]
    assert "organizations.header_required" in post["responses"]["422"]["description"]
    assert "billing.checkout_unavailable" in post["responses"]["422"]["description"]
    assert "billing.subscription_required" in post["responses"]["503"]["description"]
    assert "billing.checkout_uncertain" in post["responses"]["503"]["description"]


def test_rota_de_aceite_e_owned_pelo_subapp_faturamento():
    view = resolve("/assinatura/propostas/1/aceitar/").func.view_class
    assert view is AceitarPropostaView
    assert view.__module__ == "apps.assinaturas.subapps.faturamento.views"
