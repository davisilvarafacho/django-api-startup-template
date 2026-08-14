"""Testes da action de histórico de auditoria herdada pelo `BaseModelViewSet`."""

from django.urls import NoReverseMatch, include, path, reverse

from rest_framework.permissions import AllowAny
from rest_framework.routers import SimpleRouter
from rest_framework.test import APIRequestFactory

import pytest
from drf_spectacular.generators import SchemaGenerator

from apps.api.base.serializers import BaseModelSerializer
from apps.api.base.views import BaseModelViewSet, PermissionsViewSetMixin
from apps.logs.models import LogAlteracao
from apps.usuarios.models import Usuario
from tests.support.usuarios import criar_usuario


class _UsuarioSerializer(BaseModelSerializer):
    class Meta:
        model = Usuario
        fields = ["id", "email", "first_name"]


class _UsuarioViewSet(BaseModelViewSet):
    queryset = Usuario.objects.all()
    serializer_class = _UsuarioSerializer
    permission_classes = [AllowAny]
    authentication_classes = []
    filter_backends = []


def _pedir_logs(usuario, **query):
    request = APIRequestFactory().get(f"/usuarios/{usuario.pk}/logs/", query)
    view = _UsuarioViewSet.as_view({"get": "logs"})
    return view(request, pk=usuario.pk)


@pytest.mark.django_db
def test_logs_devolve_o_historico_do_registro_do_mais_recente_ao_mais_antigo():
    usuario = criar_usuario(email="alvo@exemplo.com", first_name="Antes")
    usuario.first_name = "Depois"
    usuario.save()

    response = _pedir_logs(usuario)

    assert response.status_code == 200
    registros = response.data["resultados"]
    assert [registro["action"] for registro in registros] == [
        LogAlteracao.Action.UPDATE,
        LogAlteracao.Action.CREATE,
    ]
    assert registros[0]["changes"]["first_name"] == ["Antes", "Depois"]
    assert registros[0]["model"] == "usuarios.usuario"


@pytest.mark.django_db
def test_logs_nao_vaza_o_historico_de_outro_registro():
    alvo = criar_usuario(email="alvo@exemplo.com")
    outro = criar_usuario(email="outro@exemplo.com")
    outro.first_name = "Renomeado"
    outro.save()

    response = _pedir_logs(alvo)

    assert {registro["object_id"] for registro in response.data["resultados"]} == {alvo.pk}


@pytest.mark.django_db
def test_logs_e_paginado_pelo_paginator_do_projeto():
    usuario = criar_usuario(email="alvo@exemplo.com", first_name="Nome0")
    for indice in range(1, 4):
        usuario.first_name = f"Nome{indice}"
        usuario.save()

    response = _pedir_logs(usuario, size=2)

    assert response.data["total"] == 4
    assert len(response.data["resultados"]) == 2
    assert response.data["proxima"] is not None


def test_endpoint_global_de_logs_nao_existe_mais():
    with pytest.raises(NoReverseMatch):
        reverse("log-alteracao-list")


def test_openapi_documenta_o_corpo_da_resposta_de_logs():
    """O `AutoSchema` não enxerga serpy; sem `LOGS_ACTION_SCHEMA` a rota sairia sem corpo."""
    router = SimpleRouter()
    router.register("usuarios", _UsuarioViewSet, basename="usuario-logs-schema")
    patterns = [path("", include(router.urls))]

    schema = SchemaGenerator(patterns=patterns).get_schema(request=None, public=True)
    operation = schema["paths"]["/usuarios/{id}/logs/"]["get"]
    resposta = operation["responses"]["200"]["content"]["application/json"]["schema"]

    assert operation["summary"] == "Lista o histórico de auditoria do registro"
    assert resposta["$ref"].endswith("/PaginatedLogAlteracaoSchemaList")


def test_logs_exige_a_permission_de_leitura_do_recurso():
    assert PermissionsViewSetMixin.base_permissions["logs"] == ["%(app_label)s.view_%(model_name)s"]


@pytest.mark.django_db
def test_logs_recusa_metodos_de_escrita():
    usuario = criar_usuario(email="alvo@exemplo.com")
    request = APIRequestFactory().post(f"/usuarios/{usuario.pk}/logs/", {}, format="json")

    response = _UsuarioViewSet.as_view({"get": "logs"})(request, pk=usuario.pk)

    assert response.status_code == 405
