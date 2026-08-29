"""Testes dos marcadores de rota por decorator (não tocam o banco)."""

from django.test import override_settings
from django.urls import path

from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ViewSet

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


urlpatterns = [
    path("_test/regularizar/", ViewSetComRegularizacao.as_view({"post": "regularizar"})),
    path("_test/comum/", ViewSetComRegularizacao.as_view({"post": "comum"})),
    path("_test/metodo/", ViewComMetodoRegularizacao.as_view()),
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


def test_view_do_path_resolve_view_de_classe():
    """`/auth/login/` é uma APIView do DRF: deve devolver a classe, não o wrapper."""
    view = view_do_path("/auth/login/")

    assert view is not None
    assert isinstance(view, type)


def test_view_do_path_resolve_view_de_funcao():
    assert view_do_path("/health/") is not None


def test_view_do_path_retorna_none_para_rota_inexistente():
    assert view_do_path("/rota/que/nao/existe/") is None
