"""Testes do par AuthenticationMiddleware + PassthroughAuthentication.

Não tocam o banco: os autenticadores Knox são substituídos por dublês, já que o
que está sob teste é o fluxo de decisão, não a validação do token em si.
"""
import json

from django.test import RequestFactory

from rest_framework.exceptions import AuthenticationFailed

import pytest

from apps.api.autenticacao.authentications import PassthroughAuthentication
from apps.api.autenticacao.constants import (
    REQUEST_ATTR_RESOLVED,
    RESOLVED_PRIVATE,
    RESOLVED_PUBLIC,
)
from apps.api.autenticacao.middleware import AuthenticationMiddleware


class UsuarioFalso:
    def __init__(self, is_active=True):
        self.is_active = is_active
        self.is_authenticated = True


class AutenticadorFalso:
    """Dublê no formato esperado pelo middleware."""

    def __init__(self, resultado=None, erro=None):
        self.resultado = resultado
        self.erro = erro
        self.chamadas = 0

    def authenticate(self, request):
        self.chamadas += 1
        if self.erro is not None:
            raise self.erro
        return self.resultado


@pytest.fixture
def rf():
    return RequestFactory()


@pytest.fixture
def rota_privada(monkeypatch):
    """Faz o registry considerar qualquer rota como privada."""
    monkeypatch.setattr("apps.api.autenticacao.middleware.routes_registry.matches", lambda path: False)


@pytest.fixture
def rota_publica(monkeypatch):
    monkeypatch.setattr("apps.api.autenticacao.middleware.routes_registry.matches", lambda path: True)


def build_middleware(*autenticadores):
    chamadas = []

    def get_response(request):
        chamadas.append(request)
        return "resposta-da-view"

    middleware = AuthenticationMiddleware(get_response)
    middleware.authenticators = list(autenticadores)
    return middleware, chamadas


# ---- middleware ----


def test_rota_publica_nao_exige_token(rf, rota_publica):
    autenticador = AutenticadorFalso()
    middleware, chamadas = build_middleware(autenticador)

    resposta = middleware(rf.get("/auth/login/"))

    assert resposta == "resposta-da-view"
    assert autenticador.chamadas == 0
    assert getattr(chamadas[0], REQUEST_ATTR_RESOLVED) == RESOLVED_PUBLIC


def test_rota_privada_sem_token_retorna_401(rf, rota_privada):
    middleware, chamadas = build_middleware(AutenticadorFalso(resultado=None))

    resposta = middleware(rf.get("/v1/pedidos/"))

    assert resposta.status_code == 401
    assert json.loads(resposta.content)["mensagem"] == "Token não fornecido."
    assert chamadas == []


def test_rota_privada_com_token_invalido_retorna_401(rf, rota_privada):
    erro = AuthenticationFailed("Invalid token.")
    middleware, chamadas = build_middleware(AutenticadorFalso(erro=erro))

    resposta = middleware(rf.get("/v1/pedidos/"))

    assert resposta.status_code == 401
    assert json.loads(resposta.content)["mensagem"] == "Invalid token."
    assert chamadas == []


def test_rota_privada_com_token_valido_resolve_o_usuario(rf, rota_privada):
    usuario = UsuarioFalso()
    middleware, chamadas = build_middleware(AutenticadorFalso(resultado=(usuario, "token-obj")))

    resposta = middleware(rf.get("/v1/pedidos/"))

    assert resposta == "resposta-da-view"
    request = chamadas[0]
    assert request.user is usuario
    assert request.auth == "token-obj"
    assert getattr(request, REQUEST_ATTR_RESOLVED) == RESOLVED_PRIVATE


def test_usa_o_proximo_autenticador_quando_o_primeiro_devolve_none(rf, rota_privada):
    usuario = UsuarioFalso()
    primeiro = AutenticadorFalso(resultado=None)
    segundo = AutenticadorFalso(resultado=(usuario, "token-obj"))
    middleware, chamadas = build_middleware(primeiro, segundo)

    middleware(rf.get("/v1/pedidos/"))

    assert primeiro.chamadas == 1
    assert segundo.chamadas == 1
    assert chamadas[0].user is usuario


def test_nao_consulta_os_demais_apos_o_primeiro_sucesso(rf, rota_privada):
    primeiro = AutenticadorFalso(resultado=(UsuarioFalso(), "token-obj"))
    segundo = AutenticadorFalso(resultado=None)
    middleware, _ = build_middleware(primeiro, segundo)

    middleware(rf.get("/v1/pedidos/"))

    assert segundo.chamadas == 0


def test_rota_de_debug_e_liberada_com_debug_ligado(rf, rota_privada, settings):
    settings.DEBUG = True
    autenticador = AutenticadorFalso(resultado=None)
    middleware, chamadas = build_middleware(autenticador)

    resposta = middleware(rf.get("/silk/requests/"))

    assert resposta == "resposta-da-view"
    assert autenticador.chamadas == 0
    assert getattr(chamadas[0], REQUEST_ATTR_RESOLVED) == RESOLVED_PUBLIC


def test_rota_de_debug_exige_token_com_debug_desligado(rf, rota_privada, settings):
    settings.DEBUG = False
    middleware, chamadas = build_middleware(AutenticadorFalso(resultado=None))

    resposta = middleware(rf.get("/silk/requests/"))

    assert resposta.status_code == 401
    assert chamadas == []


# ---- passthrough ----


def test_passthrough_falha_alto_sem_o_middleware(rf):
    request = rf.get("/v1/pedidos/")

    with pytest.raises(RuntimeError, match="AuthenticationMiddleware"):
        PassthroughAuthentication().authenticate(request)


def test_passthrough_devolve_none_em_rota_publica(rf):
    request = rf.get("/auth/login/")
    setattr(request, REQUEST_ATTR_RESOLVED, RESOLVED_PUBLIC)

    assert PassthroughAuthentication().authenticate(request) is None


def test_passthrough_reaproveita_usuario_e_token(rf):
    usuario = UsuarioFalso()
    request = rf.get("/v1/pedidos/")
    setattr(request, REQUEST_ATTR_RESOLVED, RESOLVED_PRIVATE)
    request.user = usuario
    request.auth = "token-obj"

    assert PassthroughAuthentication().authenticate(request) == (usuario, "token-obj")


def test_passthrough_recusa_usuario_inativo(rf):
    request = rf.get("/v1/pedidos/")
    setattr(request, REQUEST_ATTR_RESOLVED, RESOLVED_PRIVATE)
    request.user = UsuarioFalso(is_active=False)

    with pytest.raises(AuthenticationFailed):
        PassthroughAuthentication().authenticate(request)


def test_passthrough_le_a_request_interna_do_drf(rf):
    """O DRF passa sua própria Request; o marcador vive na HttpRequest do Django."""
    usuario = UsuarioFalso()
    django_request = rf.get("/v1/pedidos/")
    setattr(django_request, REQUEST_ATTR_RESOLVED, RESOLVED_PRIVATE)
    django_request.user = usuario
    django_request.auth = "token-obj"

    class DRFRequestFalsa:
        _request = django_request

    assert PassthroughAuthentication().authenticate(DRFRequestFalsa()) == (usuario, "token-obj")


def test_passthrough_expoe_authenticate_header(rf):
    """Sem o header o DRF responderia 403 em vez de 401 para anônimos."""
    assert PassthroughAuthentication().authenticate_header(rf.get("/v1/pedidos/"))
