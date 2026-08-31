"""Contrato OpenAPI literal do aceite de proposta."""

import pytest
from drf_spectacular.generators import SchemaGenerator

from apps.api.core.errors import discover_error_codes
from apps.assinaturas import urls as assinaturas_urls
from apps.assinaturas.views import AceitarPropostaView


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
    assert set(post["responses"]) == {"200", "400", "401", "403", "404", "409"}
    assert "auth.reauthentication_required" in post["responses"]["401"]["description"]
    assert "organizations.role_insufficient" in post["responses"]["403"]["description"]
    assert "core.not_found" in post["responses"]["404"]["description"]
    assert "billing.proposal_invalid" in post["responses"]["409"]["description"]
