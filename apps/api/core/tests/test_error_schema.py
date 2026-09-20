from django.core.exceptions import ImproperlyConfigured
from django.urls import path

from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

import pytest
from drf_spectacular.generators import SchemaGenerator

from apps.api.autenticacao.errors import AuthErrorCode
from apps.api.core.errors import ValidationErrorCode, discover_error_codes
from apps.api.core.schema import document_error_responses


class _ExemploView(APIView):
    permission_classes = [AllowAny]
    # A geração de schema do drf-spectacular pula endpoints versionados sem
    # `DEFAULT_VERSION` (não configurado no projeto); isso é ortogonal ao
    # contrato de erros testado aqui.
    versioning_class = None

    @document_error_responses(
        {
            401: [AuthErrorCode.NOT_AUTHENTICATED],
            403: [AuthErrorCode.PERMISSION_DENIED],
            422: [ValidationErrorCode.INVALID],
        }
    )
    def get(self, request):
        return Response({})


urlpatterns = [path("exemplo/", _ExemploView.as_view(), name="exemplo")]


@pytest.fixture(autouse=True)
def _registry_populado():
    discover_error_codes(force=True)
    yield
    discover_error_codes(force=True)


def _gerar_schema():
    generator = SchemaGenerator(patterns=urlpatterns)
    return generator.get_schema(request=None, public=True)


def test_schema_documenta_operacao_de_exemplo_com_401_403_422():
    schema = _gerar_schema()

    operation = schema["paths"]["/exemplo/"]["get"]

    assert {"401", "403", "422"} <= operation["responses"].keys()


def test_api_error_response_schema_expoe_todos_os_campos():
    schema = _gerar_schema()

    componentes = schema["components"]["schemas"]
    item_schema = componentes["APIErrorItemSchema"]["properties"]

    assert set(item_schema) == {"code", "message", "field", "path", "context"}
    assert "errors" in componentes["APIErrorResponseSchema"]["properties"]
    assert "request_id" in componentes["APIErrorResponseSchema"]["properties"]


def test_schema_path_aceita_componentes_textuais_e_indices_numericos():
    schema = _gerar_schema()

    path_schema = schema["components"]["schemas"]["APIErrorItemSchema"]["properties"]["path"]

    assert path_schema["type"] == "array"
    assert path_schema["items"] == {}


def test_document_error_codes_rejeita_codigo_nao_registrado():
    from django.db import models

    class CodigoForaDoRegistry(models.TextChoices):
        FORA = "fora.do_registry", "Fora"

    with pytest.raises(ImproperlyConfigured):
        document_error_responses({422: [CodigoForaDoRegistry.FORA]})


def test_resource_policy_schema_describes_session_only_and_key_actions():
    from apps.api.core.schema import ResourceAwareAutoSchema
    from apps.organizacoes.views import VinculoViewSet

    schema = ResourceAwareAutoSchema()
    schema.view = VinculoViewSet()
    schema.view.action = "destroy"
    schema.method = "DELETE"
    extensions = schema.get_extensions()
    assert extensions["x-resource-authorization"] == {
        "resource": "memberships",
        "action": "delete",
        "available_for_api_key": False,
        "scope": None,
        "session_requires_permission": True,
        "minimum_role": int(VinculoViewSet.authorization_policy.minimum_roles["delete"]),
    }
    schema.view.action = "list"
    schema.method = "GET"
    authorization = schema.get_extensions()["x-resource-authorization"]
    assert authorization["available_for_api_key"] is True
    assert authorization["scope"] == "memberships:read"
