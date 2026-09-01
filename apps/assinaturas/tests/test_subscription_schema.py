"""Contrato OpenAPI literal das rotas de assinatura."""

import pytest
from drf_spectacular.generators import SchemaGenerator

from apps.api.core.errors import discover_error_codes
from apps.api.core.route_markers import MARCADOR_REGULARIZACAO_ASSINATURA, rota_tem_marcador
from apps.assinaturas import urls as assinaturas_urls
from apps.assinaturas.views import (
    AlteracoesAssinaturaView,
    AssinaturaView,
    CancelamentoAssinaturaView,
    RecursosAssinaturaView,
    UtilizacaoSeatsView,
)

VIEWS = (
    AssinaturaView,
    RecursosAssinaturaView,
    UtilizacaoSeatsView,
    AlteracoesAssinaturaView,
    CancelamentoAssinaturaView,
)


@pytest.fixture(autouse=True)
def _sem_versionamento_e_registry_populado(monkeypatch):
    for view in VIEWS:
        monkeypatch.setattr(view, "versioning_class", None)
    discover_error_codes(force=True)
    yield
    discover_error_codes(force=True)


def test_consultas_documentam_respostas_e_erros_reais():
    schema = SchemaGenerator(patterns=assinaturas_urls.urlpatterns).get_schema(request=None, public=True)

    assert schema["paths"]["/assinatura/"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/AssinaturaResponse"
    }
    assert schema["paths"]["/assinatura/recursos/"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/RecursosAssinaturaResponse"
    }
    assert schema["paths"]["/assinatura/utilizacao-seats/"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/UtilizacaoSeatsResponse"
    }
    for path in ("/assinatura/", "/assinatura/recursos/", "/assinatura/utilizacao-seats/"):
        responses = schema["paths"][path]["get"]["responses"]
        assert set(responses) == {"200", "401", "403", "409", "422", "503"}
        assert "billing.organization_restricted" in responses["403"]["description"]
        assert "billing.subscription_required" in responses["503"]["description"]


def test_mutacoes_documentam_body_revisao_recencia_e_conflitos():
    schema = SchemaGenerator(patterns=assinaturas_urls.urlpatterns).get_schema(request=None, public=True)
    alteracao = schema["paths"]["/assinatura/alteracoes/"]["post"]
    cancelamento = schema["paths"]["/assinatura/cancelamento/"]

    assert alteracao["requestBody"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/SolicitarAlteracaoRequest"}
    assert alteracao["responses"]["201"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/AlteracaoAssinaturaResponse"}
    assert set(alteracao["responses"]) == {"201", "400", "401", "403", "409", "422", "503"}
    assert "auth.reauthentication_required" in alteracao["responses"]["401"]["description"]
    assert "billing.subscription_conflict" in alteracao["responses"]["409"]["description"]

    assert cancelamento["post"]["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/CancelamentoAssinaturaRequest"
    }
    assert cancelamento["post"]["responses"]["202"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/CancelamentoAssinaturaResponse"
    }
    assert cancelamento["post"]["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/CancelamentoAssinaturaResponse"
    }
    assert cancelamento["delete"]["responses"]["204"]["description"]
    assert set(cancelamento["post"]["responses"]) == {"200", "202", "400", "401", "403", "409", "422", "503"}
    assert set(cancelamento["delete"]["responses"]) == {"204", "400", "401", "403", "409", "422", "503"}


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/assinatura/", "GET"),
        ("/assinatura/recursos/", "GET"),
        ("/assinatura/utilizacao-seats/", "GET"),
        ("/assinatura/alteracoes/", "POST"),
        ("/assinatura/cancelamento/", "POST"),
        ("/assinatura/cancelamento/", "DELETE"),
    ],
)
def test_rotas_comerciais_usam_marcador_de_regularizacao(path, method):
    assert rota_tem_marcador(path, method, MARCADOR_REGULARIZACAO_ASSINATURA) is True
