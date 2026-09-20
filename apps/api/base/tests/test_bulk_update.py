"""Testes da action `bulk_update` do `BulkUpdateViewSetMixin`.

Como a `Base` é abstrata, os testes usam o model concreto `Usuario` com um ViewSet
e serializer locais, dirigidos por `APIRequestFactory` (sem depender de rota).
"""

import pytest

from apps.api.base.serializers import BaseModelSerializer
from apps.api.base.tests.support import TenantFreeAuthenticatedRequestFactory, usuario_policy
from apps.api.base.views import BaseModelViewSet, BulkUpdateViewSetMixin
from apps.usuarios.models import Usuario
from tests.support.usuarios import criar_usuario


class _UsuarioWriteSerializer(BaseModelSerializer):
    class Meta:
        model = Usuario
        fields = ["id", "first_name", "last_name"]


class _UsuarioViewSet(BulkUpdateViewSetMixin, BaseModelViewSet):
    queryset = Usuario.objects.all()
    serializer_class = _UsuarioWriteSerializer
    permission_classes = []
    authorization_policy = usuario_policy("bulk_update")
    authentication_classes = []
    filter_backends = []


def _bulk_update(payload):
    request = TenantFreeAuthenticatedRequestFactory().patch("/bulk_update/", payload, format="json")
    view = _UsuarioViewSet.as_view({"patch": "bulk_update"})
    return view(request)


@pytest.mark.django_db
def test_bulk_update_atualiza_multiplos_registros():
    u1 = criar_usuario(first_name="Antigo1")
    u2 = criar_usuario(first_name="Antigo2")

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
    assert response.data["errors"][0]["code"] == "core.bad_request"


@pytest.mark.django_db
def test_bulk_update_exige_id_em_cada_item():
    usuario = criar_usuario()
    response = _bulk_update([{"first_name": "SemId"}, {"id": usuario.id, "first_name": "Ok"}])
    assert response.status_code == 400
    assert response.data["errors"][0]["code"] == "core.bad_request"


@pytest.mark.django_db
def test_bulk_update_registro_inexistente_retorna_404():
    response = _bulk_update([{"id": 999999, "first_name": "Fantasma"}])
    assert response.status_code == 404
    assert response.data["errors"][0]["code"] == "core.not_found"


@pytest.mark.django_db
def test_bulk_update_e_atomico_em_erro_de_validacao():
    usuario = criar_usuario(first_name="Preservado")

    # `first_name` tem max_length=30; o item inválido deve impedir qualquer escrita.
    response = _bulk_update(
        [
            {"id": usuario.id, "first_name": "Valido"},
            {"id": usuario.id, "first_name": "x" * 31},
        ]
    )

    assert response.status_code == 422
    usuario.refresh_from_db()
    assert usuario.first_name == "Preservado"
