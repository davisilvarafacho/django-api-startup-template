from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.test import APIRequestFactory

import pytest

from apps.api.base.serializers import BaseModelSerializer
from apps.api.base.views import BaseModelViewSet, ClonarViewSetMixin
from apps.usuarios.models import Usuario
from tests.support.usuarios import criar_usuario


class _UsuarioSerializer(BaseModelSerializer):
    class Meta:
        model = Usuario
        fields = ["id", "email", "first_name", "last_name"]


class _UsuarioCloneViewSet(ClonarViewSetMixin, BaseModelViewSet):
    queryset = Usuario.objects.all()
    serializer_class = _UsuarioSerializer
    permission_classes = [AllowAny]
    authentication_classes = []
    filter_backends = []


class _UsuarioReadSerializer(BaseModelSerializer):
    nome_completo = serializers.SerializerMethodField()

    class Meta:
        model = Usuario
        fields = ["id", "email", "nome_completo"]

    def get_nome_completo(self, instance):
        return instance.get_full_name()


class _UsuarioReadViewSet(BaseModelViewSet):
    queryset = Usuario.objects.all()
    serializer_class = _UsuarioReadSerializer
    permission_classes = [AllowAny]
    authentication_classes = []
    filter_backends = []


@pytest.mark.django_db
def test_clonar_aplica_body_validado_antes_da_primeira_gravacao():
    usuario = criar_usuario(email="original@example.com", first_name="Original")
    request = APIRequestFactory().post(
        f"/usuarios/{usuario.pk}/clonar/",
        {"email": "clone@example.com", "first_name": "Clone"},
        format="json",
    )
    view = _UsuarioCloneViewSet.as_view({"post": "clonar"})

    response = view(request, pk=usuario.pk)

    assert response.status_code == 201
    assert response.data["email"] == "clone@example.com"
    assert response.data["first_name"] == "Clone"
    assert Usuario.objects.filter(email="original@example.com").count() == 1
    assert Usuario.objects.filter(email="clone@example.com", first_name="Clone").count() == 1


@pytest.mark.django_db
def test_clonar_nao_persiste_quando_body_e_invalido():
    usuario = criar_usuario(email="original@example.com")
    request = APIRequestFactory().post(
        f"/usuarios/{usuario.pk}/clonar/",
        {"email": "email-invalido"},
        format="json",
    )
    view = _UsuarioCloneViewSet.as_view({"post": "clonar"})

    response = view(request, pk=usuario.pk)

    assert response.status_code == 422
    assert Usuario.objects.count() == 1


@pytest.mark.django_db
def test_list_usa_representacao_do_serializer():
    criar_usuario(first_name="Maria", last_name="Silva")
    request = APIRequestFactory().get("/usuarios/")
    view = _UsuarioReadViewSet.as_view({"get": "list"})

    response = view(request)

    assert response.status_code == 200
    assert response.data["resultados"][0]["nome_completo"] == "Maria Silva"


@pytest.mark.django_db
def test_retrieve_usa_representacao_do_serializer():
    usuario = criar_usuario(first_name="Maria", last_name="Silva")
    request = APIRequestFactory().get(f"/usuarios/{usuario.pk}/")
    view = _UsuarioReadViewSet.as_view({"get": "retrieve"})

    response = view(request, pk=usuario.pk)

    assert response.status_code == 200
    assert response.data["nome_completo"] == "Maria Silva"
