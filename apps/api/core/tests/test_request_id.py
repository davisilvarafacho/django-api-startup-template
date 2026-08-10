"""Testes do correlation id (não tocam o banco)."""

from django.http import HttpResponse
from django.test import RequestFactory

import pytest

from apps.api.core.request_id import (
    HEADER_REQUEST_ID,
    RequestIDMiddleware,
    get_request_id,
    normalizar_request_id,
)


@pytest.fixture
def factory():
    return RequestFactory()


def responder(request):
    return HttpResponse("ok")


def test_gera_id_quando_o_cliente_nao_manda():
    middleware = RequestIDMiddleware(responder)
    request = RequestFactory().get("/qualquer/")

    response = middleware(request)

    assert response[HEADER_REQUEST_ID]
    assert response[HEADER_REQUEST_ID] == request.id


def test_reaproveita_o_id_enviado_pelo_cliente():
    middleware = RequestIDMiddleware(responder)
    request = RequestFactory().get("/qualquer/", HTTP_X_REQUEST_ID="abc123")

    response = middleware(request)

    assert response[HEADER_REQUEST_ID] == "abc123"


def test_id_fica_disponivel_durante_a_request():
    visto = {}

    def view(request):
        visto["id"] = get_request_id()
        return HttpResponse("ok")

    middleware = RequestIDMiddleware(view)
    request = RequestFactory().get("/qualquer/")

    middleware(request)

    assert visto["id"] == request.id


def test_contexto_e_liberado_ao_fim_da_request():
    middleware = RequestIDMiddleware(responder)

    middleware(RequestFactory().get("/qualquer/"))

    assert get_request_id() is None


def test_nao_libera_o_contexto_no_process_exception():
    """Um 500 precisa chegar ao log ainda carregando o id.

    O Django chama `process_exception` **antes** de converter a exceção em
    resposta e, só depois, o `process_response` de todo middleware já iniciado.
    Liberar o contexto no primeiro hook apagaria o id tanto do log de erro do
    `django.request` quanto do log de acesso do 500.
    """
    assert not hasattr(RequestIDMiddleware, "process_exception")


@pytest.mark.parametrize(
    "valor",
    [
        "",
        None,
        "x" * 65,  # comprimento absurdo
        "nao-eh-hex!",  # caractere de controle/pontuação
        "com espaco",
        "٣٢١٤",  # dígitos árabe-índicos: `isalnum()` aprova, ASCII não
        "café1234",  # letra acentuada
    ],
)
def test_rejeita_id_invalido_do_cliente(valor):
    assert normalizar_request_id(valor) is None


@pytest.mark.parametrize(
    "valor",
    [
        "abc123",
        "550e8400-e29b-41d4-a716-446655440000",
        "9f1c2b3a4d5e6f708192a3b4c5d6e7f8",
    ],
)
def test_aceita_id_valido_do_cliente(valor):
    assert normalizar_request_id(valor) == valor


def test_id_invalido_do_cliente_e_substituido_por_um_gerado():
    middleware = RequestIDMiddleware(responder)
    request = RequestFactory().get("/qualquer/", HTTP_X_REQUEST_ID="../../etc/passwd")

    response = middleware(request)

    assert response[HEADER_REQUEST_ID] != "../../etc/passwd"
    assert len(response[HEADER_REQUEST_ID]) == 32
