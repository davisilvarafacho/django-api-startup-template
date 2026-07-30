"""Testes da action `bulk_update` do `BaseModelViewSet`.

Como a `Base` é abstrata, os testes usam o model concreto `Usuario` com um ViewSet
e serializer locais, dirigidos por `APIRequestFactory` (sem depender de rota).
"""

from rest_framework.permissions import AllowAny
from rest_framework.test import APIRequestFactory

import pytest

from apps.api.base.serializers import BaseModelSerializer
from apps.api.base.views import BaseModelViewSet
from apps.usuarios.factories import UsuarioFactory
from apps.usuarios.models import Usuario


class _UsuarioWriteSerializer(BaseModelSerializer):
    class Meta:
        model = Usuario
        fields = ["id", "first_name", "last_name"]


class _UsuarioViewSet(BaseModelViewSet):
    queryset = Usuario.objects.all()
    serializer_class = _UsuarioWriteSerializer
    permission_classes = [AllowAny]
    authentication_classes = []
    filter_backends = []
    has_is_active_field = False


def _bulk_update(payload):
    request = APIRequestFactory().patch("/bulk_update/", payload, format="json")
    view = _UsuarioViewSet.as_view({"patch": "bulk_update"})
    return view(request)


@pytest.mark.django_db
def test_bulk_update_atualiza_multiplos_registros():
    u1 = UsuarioFactory(first_name="Antigo1")
    u2 = UsuarioFactory(first_name="Antigo2")

    response = _bulk_update(
        [
            {"id": u1.id, "first_name": "Novo1"},
            {"id": u2.id, "first_name": "Novo2"},
        ]
    )

    assert response.status_code == 200
    u1.refresh_from_db()
    u2.refresh_from_db()
    assert u1.first_name == "Novo1"
    assert u2.first_name == "Novo2"


@pytest.mark.django_db
def test_bulk_update_exige_lista():
    response = _bulk_update({"id": 1, "first_name": "X"})
    assert response.status_code == 400


@pytest.mark.django_db
def test_bulk_update_exige_id_em_cada_item():
    usuario = UsuarioFactory()
    response = _bulk_update([{"first_name": "SemId"}, {"id": usuario.id, "first_name": "Ok"}])
    assert response.status_code == 400


@pytest.mark.django_db
def test_bulk_update_registro_inexistente_retorna_404():
    response = _bulk_update([{"id": 999999, "first_name": "Fantasma"}])
    assert response.status_code == 404


@pytest.mark.django_db
def test_bulk_update_e_atomico_em_erro_de_validacao():
    usuario = UsuarioFactory(first_name="Preservado")

    # `first_name` tem max_length=30; o item inválido deve impedir qualquer escrita.
    response = _bulk_update(
        [
            {"id": usuario.id, "first_name": "Valido"},
            {"id": usuario.id, "first_name": "x" * 31},
        ]
    )

    assert response.status_code == 400
    usuario.refresh_from_db()
    assert usuario.first_name == "Preservado"
