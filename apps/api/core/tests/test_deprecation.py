from django.http import HttpResponse
from django.urls import include, path

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.response import Response
from rest_framework.routers import SimpleRouter
from rest_framework.test import APIRequestFactory

import pytest
from drf_spectacular.generators import SchemaGenerator

from apps.api.core.deprecation import api_deprecated


def decorar(**overrides):
    parametros = {
        "since": "2026-08-01",
        "sunset": "2026-11-01",
        "documentation": "/docs/deprecations/usuarios/",
        "replacement": "/api/v2/usuarios/",
    }
    parametros.update(overrides)
    return api_deprecated(**parametros)


def test_decorator_adiciona_headers_e_preserva_link_existente():
    @decorar()
    def handler():
        response = HttpResponse(status=200)
        response["Link"] = '</api/schema/>; rel="service-desc"'
        return response

    response = handler()

    assert response["Deprecation"] == "@1785542400"
    assert response["Sunset"] == "Sun, 01 Nov 2026 00:00:00 GMT"
    assert response["Link"] == (
        '</api/schema/>; rel="service-desc", '
        '</docs/deprecations/usuarios/>; rel="deprecation"; type="text/html"'
    )


def test_decorator_nao_duplica_link_de_deprecacao():
    link = '</docs/deprecations/usuarios/>; rel="deprecation"; type="text/html"'

    @decorar()
    def handler():
        response = HttpResponse(status=200)
        response["Link"] = link
        return response

    assert handler()["Link"] == link


@pytest.mark.parametrize(
    ("campo", "valor", "mensagem"),
    [
        ("since", "01/08/2026", "since"),
        ("sunset", "2026-10-29", "90 dias"),
        ("documentation", "docs/deprecations/usuarios", "documentation"),
        ("documentation", "ftp://example.com/guia", "documentation"),
        ("replacement", "//example.com/api/v2", "replacement"),
    ],
)
def test_decorator_rejeita_configuracao_invalida(campo, valor, mensagem):
    with pytest.raises(ValueError, match=mensagem):
        decorar(**{campo: valor})


def test_decorator_rejeita_aplicacao_duplicada():
    primeiro = decorar()

    with pytest.raises(ValueError, match="mais de uma vez"):
        decorar()(primeiro(lambda: HttpResponse()))


def test_retorno_invalido_fica_para_validacao_do_drf():
    @decorar()
    def handler():
        return {"invalido": True}

    assert handler() == {"invalido": True}


class NegarTudo(BasePermission):
    def has_permission(self, request, view):
        return False


class DeprecatedViewSet(viewsets.ViewSet):
    authentication_classes = []
    permission_classes = [AllowAny]
    versioning_class = None

    @api_deprecated(since="2026-08-01", sunset="2026-11-01", documentation="/docs/deprecations/listagem/", replacement="/api/v2/itens/")
    def list(self, request):
        return Response({"ok": True})

    @action(detail=False, methods=["get"])
    @api_deprecated(since="2026-08-01", sunset="2026-11-01", documentation="/docs/deprecations/relatorio/")
    def relatorio(self, request):
        return Response({"ok": True})

    @relatorio.mapping.delete
    def apagar_relatorio(self, request):
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=["post"])
    @api_deprecated(since="2026-08-01", sunset="2026-11-01", documentation="/docs/deprecations/validacao/")
    def resposta_400(self, request):
        return Response({"erro": True}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=["post"])
    @api_deprecated(since="2026-08-01", sunset="2026-11-01", documentation="/docs/deprecations/excecao/")
    def excecao_400(self, request):
        raise ValidationError("inválido")


class ProtectedDeprecatedViewSet(viewsets.ViewSet):
    authentication_classes = []
    permission_classes = [NegarTudo]

    @api_deprecated(since="2026-08-01", sunset="2026-11-01", documentation="/docs/deprecations/protegido/")
    def list(self, request):
        return Response({"ok": True})


def executar(viewset, method, action_name, path="/teste/"):
    request = getattr(APIRequestFactory(), method)(path, {}, format="json")
    return viewset.as_view({method: action_name})(request)


def test_action_padrao_recebe_headers():
    response = executar(DeprecatedViewSet, "get", "list")
    assert response.status_code == 200
    assert response["Deprecation"] == "@1785542400"


def test_deprecacao_segue_o_handler_do_method_mapper():
    assert "Deprecation" in executar(DeprecatedViewSet, "get", "relatorio")
    assert "Deprecation" not in executar(DeprecatedViewSet, "delete", "apagar_relatorio")


def test_respostas_e_fluxos_externos_ao_handler():
    assert "Deprecation" in executar(DeprecatedViewSet, "post", "resposta_400")
    assert "Deprecation" not in executar(DeprecatedViewSet, "post", "excecao_400")
    assert "Deprecation" not in executar(ProtectedDeprecatedViewSet, "get", "list")


def gerar_schema():
    router = SimpleRouter()
    router.register("deprecated-test", DeprecatedViewSet, basename="deprecated-test")
    return SchemaGenerator(patterns=[path("", include(router.urls))]).get_schema(request=None, public=True)


def test_openapi_recebe_metadados_do_mesmo_decorator():
    operation = gerar_schema()["paths"]["/deprecated-test/"]["get"]
    assert operation["deprecated"] is True
    assert operation["externalDocs"] == {"url": "/docs/deprecations/listagem/"}
    assert operation["x-deprecation-since"] == "2026-08-01"
    assert operation["x-sunset"] == "2026-11-01"
    assert operation["x-replacement"] == "/api/v2/itens/"


def test_openapi_omite_replacement_quando_nao_configurado():
    assert "x-replacement" not in gerar_schema()["paths"]["/deprecated-test/relatorio/"]["get"]


def test_cors_expoe_headers_de_deprecacao(settings):
    assert settings.CORS_EXPOSE_HEADERS == ["Deprecation", "Sunset", "Link"]
