"""Contrato HTTP da action de metadata do `BaseModelViewSet`."""

from django.contrib.auth.models import Permission

from rest_framework.test import force_authenticate

import pytest

from apps.api.base.serializers import BaseModelSerializer
from apps.api.base.tests.support import TenantFreeAuthenticatedRequestFactory, usuario_policy
from apps.api.base.views import BaseModelViewSet
from apps.api.metadata.models import Metadata
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Organizacao
from apps.usuarios.models import Usuario
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db


class _UsuarioSerializer(BaseModelSerializer):
    class Meta:
        model = Usuario
        fields = ["id", "email"]


class _UsuarioViewSet(BaseModelViewSet):
    queryset = Usuario.objects.all()
    serializer_class = _UsuarioSerializer
    permission_classes = []
    authorization_policy = usuario_policy()
    authentication_classes = []
    filter_backends = []


class _SemMetadataViewSet(_UsuarioViewSet):
    metadata_habilitado = False
    authorization_policy = usuario_policy(metadata=False)


class _UsuarioProtegidoViewSet(_UsuarioViewSet):
    """Exercita a policy com permissions reais de usuários sem privilégios."""

    permission_classes = []


@pytest.fixture
def organizacao():
    return Organizacao.objects.create(nome="Acme", slug="acme")


def test_get_devolve_documento_vazio_quando_nao_ha_metadata(organizacao):
    usuario = criar_usuario()
    request = TenantFreeAuthenticatedRequestFactory().get(f"/usuarios/{usuario.pk}/metadata/")
    view = _UsuarioViewSet.as_view({"get": "metadata"})

    with organizacao_atual_privilegiada(organizacao.pk):
        response = view(request, pk=usuario.pk)

    assert response.status_code == 200
    assert response.data == {"dados": {}}


def test_patch_grava_e_devolve_o_documento(organizacao):
    usuario = criar_usuario()
    request = TenantFreeAuthenticatedRequestFactory().patch(
        f"/usuarios/{usuario.pk}/metadata/",
        {"dados": {"erp_id": "X-1"}},
        format="json",
    )
    view = _UsuarioViewSet.as_view({"patch": "metadata_update"})

    with organizacao_atual_privilegiada(organizacao.pk):
        response = view(request, pk=usuario.pk)

        assert response.status_code == 200
        assert response.data == {"dados": {"erp_id": "X-1"}}
        assert Metadata.objects.get().dados == {"erp_id": "X-1"}


def test_patch_funde_com_o_documento_existente(organizacao):
    usuario = criar_usuario()
    view = _UsuarioViewSet.as_view({"patch": "metadata_update"})
    factory = TenantFreeAuthenticatedRequestFactory()

    with organizacao_atual_privilegiada(organizacao.pk):
        view(
            factory.patch("/", {"dados": {"erp_id": "X-1", "nota": "urgente"}}, format="json"),
            pk=usuario.pk,
        )
        response = view(factory.patch("/", {"dados": {"nota": None}}, format="json"), pk=usuario.pk)

    assert response.data == {"dados": {"erp_id": "X-1"}}


def test_patch_invalido_devolve_422(organizacao):
    usuario = criar_usuario()
    request = TenantFreeAuthenticatedRequestFactory().patch(
        f"/usuarios/{usuario.pk}/metadata/",
        {"dados": {"tentativas": 3}},
        format="json",
    )
    view = _UsuarioViewSet.as_view({"patch": "metadata_update"})

    with organizacao_atual_privilegiada(organizacao.pk):
        response = view(request, pk=usuario.pk)

        assert response.status_code == 422
        assert Metadata.all_objects.count() == 0


def test_action_e_registrada_por_padrao():
    nomes = [action.__name__ for action in _UsuarioViewSet.get_extra_actions()]

    assert "metadata" in nomes


def test_opt_out_remove_a_rota():
    nomes = [action.__name__ for action in _SemMetadataViewSet.get_extra_actions()]

    assert "metadata" not in nomes


def test_get_exige_permissao_de_visualizacao(organizacao):
    usuario = criar_usuario()
    request = TenantFreeAuthenticatedRequestFactory().get(f"/usuarios/{usuario.pk}/metadata/")
    force_authenticate(request, user=usuario)
    view = _UsuarioProtegidoViewSet.as_view({"get": "metadata"})

    with organizacao_atual_privilegiada(organizacao.pk):
        response = view(request, pk=usuario.pk)

    assert response.status_code == 403


def test_patch_exige_permissao_de_alteracao_e_nao_basta_a_de_leitura(organizacao):
    usuario = criar_usuario()
    usuario.user_permissions.add(Permission.objects.get(codename="view_usuario"))
    autor = Usuario.objects.get(pk=usuario.pk)
    request = TenantFreeAuthenticatedRequestFactory().patch(
        f"/usuarios/{usuario.pk}/metadata/",
        {"dados": {"erp_id": "X-1"}},
        format="json",
    )
    force_authenticate(request, user=autor)
    view = _UsuarioProtegidoViewSet.as_view({"patch": "metadata_update"})

    with organizacao_atual_privilegiada(organizacao.pk):
        response = view(request, pk=usuario.pk)

        assert response.status_code == 403
        assert Metadata.all_objects.count() == 0


def test_patch_passa_com_permissao_de_alteracao(organizacao):
    usuario = criar_usuario()
    usuario.user_permissions.add(Permission.objects.get(codename="change_usuario"))
    autor = Usuario.objects.get(pk=usuario.pk)
    request = TenantFreeAuthenticatedRequestFactory().patch(
        f"/usuarios/{usuario.pk}/metadata/",
        {"dados": {"erp_id": "X-1"}},
        format="json",
    )
    force_authenticate(request, user=autor)
    view = _UsuarioProtegidoViewSet.as_view({"patch": "metadata_update"})

    with organizacao_atual_privilegiada(organizacao.pk):
        response = view(request, pk=usuario.pk)

    assert response.status_code == 200


def test_listagem_com_prefetch_nao_cresce_em_queries(organizacao, django_assert_num_queries):
    with organizacao_atual_privilegiada(organizacao.pk):
        for _ in range(3):
            usuario = criar_usuario()
            Metadata.objects.create(
                content_type=Usuario.get_content_type(),
                object_id=usuario.pk,
                dados={"erp_id": "X-1"},
            )

        with django_assert_num_queries(2):
            documentos = [instancia.raw_metadata for instancia in Usuario.objects.prefetch_related("metadata_registros")]

    assert documentos == [{"erp_id": "X-1"}] * 3
