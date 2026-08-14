"""Testes do paginador do projeto.

O envelope tem nomes em português (`total`/`proxima`/`anterior`/`resultados`), e o
schema OpenAPI precisa descrever exatamente esses nomes — o default do DRF
documenta `count`/`next`/`previous`/`results`, que a API nunca devolve.
"""

from django.urls import include, path

from rest_framework import serializers, viewsets
from rest_framework.permissions import AllowAny
from rest_framework.routers import SimpleRouter
from rest_framework.test import APIRequestFactory

import pytest
from drf_spectacular.generators import SchemaGenerator

from apps.api.core.pagination import CustomPagination
from apps.usuarios.models import Usuario
from tests.support.usuarios import criar_usuario


class _UsuarioSerializer(serializers.ModelSerializer):
    class Meta:
        model = Usuario
        fields = ["id", "email"]


class _UsuarioViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Usuario.objects.all()
    serializer_class = _UsuarioSerializer
    permission_classes = [AllowAny]
    authentication_classes = []
    filter_backends = []


def _envelope_publicado():
    """Resolve o schema do envelope a partir da própria rota, sem depender do nome do componente."""
    router = SimpleRouter()
    router.register("usuarios", _UsuarioViewSet, basename="usuario-paginacao")
    patterns = [path("", include(router.urls))]

    schema = SchemaGenerator(patterns=patterns).get_schema(request=None, public=True)
    resposta = schema["paths"]["/usuarios/"]["get"]["responses"]["200"]
    referencia = resposta["content"]["application/json"]["schema"]["$ref"]
    return schema["components"]["schemas"][referencia.rsplit("/", 1)[-1]]


def test_schema_do_envelope_usa_os_nomes_que_a_api_devolve():
    envelope = _envelope_publicado()

    assert set(envelope["properties"]) == {"total", "proxima", "anterior", "resultados"}
    assert envelope["required"] == ["total", "resultados"]
    assert envelope["properties"]["resultados"]["items"]["$ref"].endswith("Usuario")


def test_schema_marca_os_links_de_navegacao_como_anulaveis():
    envelope = _envelope_publicado()

    for campo in ("proxima", "anterior"):
        assert envelope["properties"][campo]["nullable"] is True
        assert envelope["properties"][campo]["format"] == "uri"


def test_schema_do_envelope_anuncia_o_parametro_de_pagina_configurado():
    envelope = _envelope_publicado()

    assert f"?{CustomPagination.page_query_param}=4" in envelope["properties"]["proxima"]["example"]


@pytest.mark.django_db
def test_resposta_paginada_bate_com_o_schema_publicado():
    """O envelope documentado e o devolvido não podem divergir: mesma origem, mesmas chaves."""
    criar_usuario(email="paginado@exemplo.com")
    request = APIRequestFactory().get("/usuarios/")

    response = _UsuarioViewSet.as_view({"get": "list"})(request)

    assert set(response.data) == set(_envelope_publicado()["properties"])
