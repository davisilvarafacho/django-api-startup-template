"""Contrato OpenAPI literal do encerramento de organizações."""

import pytest
from drf_spectacular.generators import SchemaGenerator

from apps.api.core.errors import discover_error_codes
from apps.organizacoes import urls as organizacoes_urls
from apps.organizacoes.views import OrganizacaoViewSet


@pytest.fixture(autouse=True)
def _sem_versionamento_e_registry_populado(monkeypatch):
    monkeypatch.setattr(OrganizacaoViewSet, "versioning_class", None)
    discover_error_codes(force=True)
    yield
    discover_error_codes(force=True)


def _operacoes_encerramento():
    schema = SchemaGenerator(patterns=organizacoes_urls.urlpatterns).get_schema(request=None, public=True)
    path = schema["paths"]["/organizacoes/{id}/encerramento/"]
    return schema, path["post"], path["delete"]


def test_post_encerramento_documenta_sem_body_e_somente_202_204_e_erros_reais():
    schema, post, _delete = _operacoes_encerramento()

    assert "requestBody" not in post
    assert set(post["responses"]) == {"202", "204", "401", "403", "404"}
    assert post["responses"]["202"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/EncerramentoAgendadoResponse"}
    scheduled_for = schema["components"]["schemas"]["EncerramentoAgendadoResponse"]["properties"]["scheduled_for"]
    assert scheduled_for == {"type": "string", "format": "date-time"}
    assert schema["components"]["schemas"]["EncerramentoAgendadoResponse"]["required"] == ["scheduled_for"]
    assert "auth.not_authenticated" in post["responses"]["401"]["description"]
    assert "auth.reauthentication_required" in post["responses"]["401"]["description"]
    assert "organizations.role_insufficient" in post["responses"]["403"]["description"]
    assert "core.not_found" in post["responses"]["404"]["description"]


def test_delete_encerramento_documenta_sem_body_204_e_erros_reais():
    _schema, _post, delete = _operacoes_encerramento()

    assert "requestBody" not in delete
    assert set(delete["responses"]) == {"204", "401", "403", "404"}
    assert "auth.not_authenticated" in delete["responses"]["401"]["description"]
    assert "auth.reauthentication_required" in delete["responses"]["401"]["description"]
    assert "organizations.role_insufficient" in delete["responses"]["403"]["description"]
    assert "core.not_found" in delete["responses"]["404"]["description"]
