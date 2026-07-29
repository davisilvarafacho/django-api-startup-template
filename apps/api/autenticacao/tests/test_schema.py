"""Contrato OpenAPI dos endpoints de autenticação com efeito colateral/segredo.

A geração de schema do projeto inteiro pula endpoints versionados sem
`DEFAULT_VERSION` (bug pré-existente, ortogonal a este plano); aqui a
`versioning_class` é zerada nas views testadas para isolar o que
`apps.api.autenticacao.schema` realmente documenta.
"""
import pytest
from drf_spectacular.generators import SchemaGenerator

from apps.api.autenticacao import urls as auth_urls
from apps.api.autenticacao.views import APIKeyViewSet, LoginView, ReauthenticateView
from apps.api.core.errors import discover_error_codes


@pytest.fixture(autouse=True)
def _sem_versionamento_e_registry_populado(monkeypatch):
    monkeypatch.setattr(LoginView, "versioning_class", None)
    monkeypatch.setattr(ReauthenticateView, "versioning_class", None)
    monkeypatch.setattr(APIKeyViewSet, "versioning_class", None)
    discover_error_codes(force=True)
    yield
    discover_error_codes(force=True)


def _gerar_schema():
    generator = SchemaGenerator(patterns=auth_urls.urlpatterns)
    return generator.get_schema(request=None, public=True)


def test_login_documenta_401_e_nao_omite_a_resposta_de_sucesso():
    schema = _gerar_schema()

    operation = schema["paths"]["/auth/login/"]["post"]

    assert {"200", "401"} <= operation["responses"].keys()
    assert "token" in schema["components"]["schemas"]["LoginResponse"]["properties"]


def test_reauthenticate_documenta_401():
    schema = _gerar_schema()

    operation = schema["paths"]["/auth/reauthenticate/"]["post"]

    assert "401" in operation["responses"]


def test_criacao_de_api_key_documenta_erros_e_nao_omite_o_sucesso():
    schema = _gerar_schema()

    operation = schema["paths"]["/auth/api_keys/"]["post"]

    assert {"201", "401", "403", "422"} <= operation["responses"].keys()


def test_rotacao_de_api_key_documenta_erros_e_nao_omite_o_sucesso():
    schema = _gerar_schema()

    operation = schema["paths"]["/auth/api_keys/{uuid}/rotate/"]["post"]

    assert {"201", "401", "409"} <= operation["responses"].keys()


def test_schema_de_api_key_nunca_declara_campos_de_segredo():
    schema = _gerar_schema()

    componentes = schema["components"]["schemas"]
    for nome, definicao in componentes.items():
        if not nome.startswith("APIKey"):
            continue
        propriedades = definicao.get("properties", {})
        assert "digest" not in propriedades
        assert "token_key" not in propriedades
