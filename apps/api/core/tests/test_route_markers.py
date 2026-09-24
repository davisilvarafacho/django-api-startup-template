"""Testes dos marcadores de rota por decorator (não tocam o banco)."""

from unittest.mock import patch

from django.db import connection
from django.test import override_settings
from django.urls import path

from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ViewSet

import pytest

import apps.api.core.route_markers as route_markers
from apps.api.core.route_markers import (
    MARCADOR_PUBLICA,
    MARCADOR_SEM_TENANCY,
    no_tenancy,
    public,
    tem_marcador,
    view_do_path,
)


class ViewSetComRegularizacao(ViewSet):
    @route_markers.regularizacao_assinatura
    def regularizar(self, request):
        return Response()

    def comum(self, request):
        return Response()


class ViewComMetodoRegularizacao(APIView):
    @route_markers.regularizacao_assinatura
    def post(self, request):
        return Response()


@route_markers.io_externo_sem_transacao
class ViewComIoExterno(APIView):
    def post(self, request):
        return Response()


class ViewTenantComum(APIView):
    def post(self, request):
        return Response()


@route_markers.no_workspace
class ViewSemWorkspace(APIView):
    def post(self, request):
        return Response()


urlpatterns = [
    path("_test/regularizar/", ViewSetComRegularizacao.as_view({"post": "regularizar"})),
    path("_test/comum/", ViewSetComRegularizacao.as_view({"post": "comum"})),
    path("_test/metodo/", ViewComMetodoRegularizacao.as_view()),
    path("_test/io-externo/", ViewComIoExterno.as_view()),
    path("_test/tenant-comum/", ViewTenantComum.as_view()),
    path("_test/sem-workspace/", ViewSemWorkspace.as_view()),
]


def test_public_marca_a_view():
    @public
    class Qualquer:
        pass

    assert tem_marcador(Qualquer, MARCADOR_PUBLICA)
    assert not tem_marcador(Qualquer, MARCADOR_SEM_TENANCY)


def test_no_tenancy_marca_a_view():
    @no_tenancy
    class Qualquer:
        pass

    assert tem_marcador(Qualquer, MARCADOR_SEM_TENANCY)
    assert not tem_marcador(Qualquer, MARCADOR_PUBLICA)


def test_decorators_sao_acumulaveis():
    @public
    @no_tenancy
    class Qualquer:
        pass

    assert tem_marcador(Qualquer, MARCADOR_PUBLICA)
    assert tem_marcador(Qualquer, MARCADOR_SEM_TENANCY)


def test_marcador_vale_para_instancia():
    """O DRF entrega a instância da view para a permissão, não a classe."""

    @no_tenancy
    class Qualquer:
        pass

    assert tem_marcador(Qualquer(), MARCADOR_SEM_TENANCY)


def test_marcador_funciona_em_funcao():
    @public
    def uma_view(request):
        return None

    assert tem_marcador(uma_view, MARCADOR_PUBLICA)


def test_view_sem_decorator_nao_tem_marcador():
    class Qualquer:
        pass

    assert not tem_marcador(Qualquer, MARCADOR_PUBLICA)
    assert not tem_marcador(None, MARCADOR_PUBLICA)


def test_regularizacao_assinatura_e_um_marcador_declarativo_registrado():
    @route_markers.regularizacao_assinatura
    class Qualquer:
        pass

    assert route_markers.MARCADOR_REGULARIZACAO_ASSINATURA in route_markers.MARCADORES_ROTA
    assert tem_marcador(Qualquer, route_markers.MARCADOR_REGULARIZACAO_ASSINATURA)


def test_io_externo_sem_transacao_e_um_marcador_declarativo_registrado():
    @route_markers.io_externo_sem_transacao
    class Qualquer:
        pass

    assert route_markers.MARCADOR_IO_EXTERNO_SEM_TRANSACAO in route_markers.MARCADORES_ROTA
    assert tem_marcador(Qualquer, route_markers.MARCADOR_IO_EXTERNO_SEM_TRANSACAO)


def test_no_workspace_e_um_marcador_declarativo_registrado():
    @route_markers.no_workspace
    class Qualquer:
        pass

    assert route_markers.MARCADOR_SEM_WORKSPACE in route_markers.MARCADORES_ROTA
    assert tem_marcador(Qualquer, route_markers.MARCADOR_SEM_WORKSPACE)


@override_settings(ROOT_URLCONF=__name__)
@pytest.mark.django_db(transaction=True)
def test_middleware_encerra_atomic_somente_para_rota_marcada(rf):
    from apps.organizacoes.middleware import OrganizacaoMiddleware

    observados = []

    def resposta(request):
        observados.append((request.path_info, connection.in_atomic_block))
        return Response()

    middleware = OrganizacaoMiddleware(resposta)
    middleware._preparar_contexto = lambda request: None
    with patch("apps.organizacoes.middleware.clear_rls_context"):
        middleware(rf.post("/_test/io-externo/"))
        middleware(rf.post("/_test/tenant-comum/"))

    assert observados == [("/_test/io-externo/", False), ("/_test/tenant-comum/", True)]


@override_settings(ROOT_URLCONF=__name__)
def test_marcador_de_regularizacao_resolve_action_sem_liberar_action_vizinha():
    assert route_markers.rota_tem_marcador(
        "/_test/regularizar/",
        "POST",
        route_markers.MARCADOR_REGULARIZACAO_ASSINATURA,
    )
    assert not route_markers.rota_tem_marcador(
        "/_test/comum/",
        "POST",
        route_markers.MARCADOR_REGULARIZACAO_ASSINATURA,
    )


@override_settings(ROOT_URLCONF=__name__)
def test_marcador_de_regularizacao_resolve_metodo_da_api_view():
    assert route_markers.rota_tem_marcador(
        "/_test/metodo/",
        "POST",
        route_markers.MARCADOR_REGULARIZACAO_ASSINATURA,
    )


@override_settings(ROOT_URLCONF=__name__)
def test_marcador_de_no_workspace_resolve_a_rota():
    assert route_markers.rota_tem_marcador(
        "/_test/sem-workspace/",
        "POST",
        route_markers.MARCADOR_SEM_WORKSPACE,
    )


def test_view_do_path_resolve_view_de_classe():
    """`/auth/login/` é uma APIView do DRF: deve devolver a classe, não o wrapper."""
    view = view_do_path("/auth/login/")

    assert view is not None
    assert isinstance(view, type)


def test_view_do_path_resolve_view_de_funcao():
    assert view_do_path("/health/") is not None


def test_view_do_path_retorna_none_para_rota_inexistente():
    assert view_do_path("/rota/que/nao/existe/") is None
