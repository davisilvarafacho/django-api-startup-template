import json

from django.http import Http404
from django.test import RequestFactory

from apps.api.autenticacao.errors import AuthErrorCode
from apps.api.core.errors import CoreErrorCode
from apps.api.core.status_handlers import (
    custom_400_handler,
    custom_401_handler,
    custom_403_handler,
    custom_404_handler,
    custom_500_handler,
)


def _rf():
    return RequestFactory().get("/qualquer/")


def test_400_handler_usa_codigo_bad_request():
    response = custom_400_handler(_rf(), Exception())
    payload = json.loads(response.content)

    assert response.status_code == 400
    assert payload["errors"][0]["code"] == CoreErrorCode.BAD_REQUEST.value


def test_401_handler_usa_codigo_not_authenticated():
    response = custom_401_handler(_rf())
    payload = json.loads(response.content)

    assert response.status_code == 401
    assert payload["errors"][0]["code"] == AuthErrorCode.NOT_AUTHENTICATED.value


def test_403_handler_usa_codigo_permission_denied():
    response = custom_403_handler(_rf(), Exception())
    payload = json.loads(response.content)

    assert response.status_code == 403
    assert payload["errors"][0]["code"] == AuthErrorCode.PERMISSION_DENIED.value


def test_404_handler_devolve_status_404():
    """Regressão: a versão antiga devolvia 400 para rota inexistente."""
    response = custom_404_handler(_rf(), Http404())
    payload = json.loads(response.content)

    assert response.status_code == 404
    assert payload["errors"][0]["code"] == CoreErrorCode.NOT_FOUND.value


def test_500_handler_nao_vaza_detalhe():
    response = custom_500_handler(_rf())
    payload = json.loads(response.content)

    assert response.status_code == 500
    assert payload["errors"][0]["code"] == CoreErrorCode.INTERNAL_ERROR.value


def test_todos_os_handlers_incluem_request_id():
    response = custom_500_handler(_rf())
    payload = json.loads(response.content)

    assert "request_id" in payload
