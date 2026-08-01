from django.core.exceptions import ValidationError as DjangoValidationError

from rest_framework import exceptions as drf_exceptions
from rest_framework import serializers
from rest_framework.test import APIRequestFactory

import pytest

from apps.api.autenticacao.errors import AuthErrorCode
from apps.api.core.errors import (
    APIError,
    CoreErrorCode,
    ValidationErrorCode,
    api_exception_handler,
    flatten_validation_errors,
)
from apps.api.core.request_id import reset_request_id, set_request_id


@pytest.fixture
def context():
    request = APIRequestFactory().get("/v1/pedidos/")
    return {"request": request, "view": None, "args": (), "kwargs": {}}


@pytest.fixture
def request_id():
    token = set_request_id("01J-fake-request-id")
    yield "01J-fake-request-id"
    reset_request_id(token)


class ExemploSerializer(serializers.Serializer):
    email = serializers.CharField()


def _required_field_errors(*, nested=False):
    """Erros de campo real com `code='required'` gerado pelo próprio DRF."""
    serializer = ExemploSerializer(data={})
    serializer.is_valid()
    if nested:
        return {"members": [serializer.errors]}
    return dict(serializer.errors)


def test_validation_error_simples_vira_422(context, request_id):
    exc = serializers.ValidationError(_required_field_errors())

    response = api_exception_handler(exc, context)

    assert response.status_code == 422
    assert response.data["errors"] == [
        {
            "code": "validation.required",
            "message": "Este campo é obrigatório.",
            "field": "email",
            "path": ("email",),
            "context": {},
        }
    ]
    assert response.data["request_id"] == request_id


def test_validation_error_aninhado_vira_422_com_paths(context):
    exc = serializers.ValidationError(_required_field_errors(nested=True))

    response = api_exception_handler(exc, context)

    assert response.status_code == 422
    assert response.data["errors"][0]["code"] == "validation.required"
    assert response.data["errors"][0]["field"] == "email"
    assert list(response.data["errors"][0]["path"]) == ["members", 0, "email"]


def test_validation_error_com_multiplos_campos_produz_multiplos_itens(context):
    exc = serializers.ValidationError({"email": ["Este campo é obrigatório."], "nome": ["Valor inválido."]})

    response = api_exception_handler(exc, context)

    assert len(response.data["errors"]) == 2
    campos = {item["field"] for item in response.data["errors"]}
    assert campos == {"email", "nome"}


def test_django_validation_error_vira_422(context):
    exc = DjangoValidationError({"email": ["Valor inválido."]})

    response = api_exception_handler(exc, context)

    assert response.status_code == 422
    assert response.data["errors"][0]["field"] == "email"
    assert response.data["errors"][0]["code"] == ValidationErrorCode.INVALID.value


def test_api_error_e_preservado(context):
    exc = APIError(AuthErrorCode.REAUTHENTICATION_REQUIRED, status_code=401, field="mfa")

    response = api_exception_handler(exc, context)

    assert response.status_code == 401
    assert response.data["errors"][0]["code"] == "auth.reauthentication_required"
    assert response.data["errors"][0]["field"] == "mfa"


def test_parse_error_vira_400_malformed(context):
    response = api_exception_handler(drf_exceptions.ParseError(), context)

    assert response.status_code == 400
    assert response.data["errors"][0]["code"] == "validation.malformed"


def test_not_authenticated_vira_401(context):
    response = api_exception_handler(drf_exceptions.NotAuthenticated(), context)

    assert response.status_code == 401
    assert response.data["errors"][0]["code"] == "auth.not_authenticated"


def test_authentication_failed_vira_401_invalid_token(context):
    response = api_exception_handler(drf_exceptions.AuthenticationFailed("Invalid token."), context)

    assert response.status_code == 401
    assert response.data["errors"][0]["code"] == "auth.invalid_token"


def test_permission_denied_vira_403(context):
    response = api_exception_handler(drf_exceptions.PermissionDenied(), context)

    assert response.status_code == 403
    assert response.data["errors"][0]["code"] == "auth.permission_denied"


def test_not_found_vira_404(context):
    response = api_exception_handler(drf_exceptions.NotFound(), context)

    assert response.status_code == 404
    assert response.data["errors"][0]["code"] == "core.not_found"


def test_throttled_vira_429(context):
    response = api_exception_handler(drf_exceptions.Throttled(wait=12), context)

    assert response.status_code == 429
    assert response.data["errors"][0]["code"] == CoreErrorCode.THROTTLED.value
    assert response.data["errors"][0]["context"] == {"retry_after": 12}


def test_internal_error_nao_vaza_detalhe(context):
    response = api_exception_handler(RuntimeError("segredo"), context)

    assert response.status_code == 500
    assert "segredo" not in str(response.data)
    assert response.data["errors"][0]["code"] == "core.internal_error"


def test_flatten_validation_errors_usa_ultimo_campo_textual_como_field():
    items = flatten_validation_errors({"members": [{"email": ["obrigatório"]}]})

    assert len(items) == 1
    assert items[0].field == "email"
    assert items[0].path == ("members", 0, "email")
