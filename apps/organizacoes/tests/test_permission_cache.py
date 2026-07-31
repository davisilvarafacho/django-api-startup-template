from unittest.mock import Mock, patch

from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, force_authenticate

import pytest

from apps.organizacoes.middleware import OrganizacaoMiddleware
from apps.organizacoes.models import Organizacao, Papel, Time, Vinculo
from apps.organizacoes.permissions import PapelMinimoPermission, TenantPermission
from apps.organizacoes.serializers import ConviteCreateSerializer, VinculoSerializer
from apps.organizacoes.tests.test_api import client_autenticado
from apps.organizacoes.views import TimeViewSet
from apps.usuarios.factories import UsuarioFactory
from common.permission_cache.resolvers.tenant import TenantAccessResolver
from common.permission_cache.types import TenantAccess


@pytest.mark.django_db
def test_tenant_permission_sets_request_tenant_and_rls_context():
    user = UsuarioFactory()
    organization = Organizacao.objects.create(nome="Acme", slug="acme")
    membership = Vinculo.objects.create(usuario=user, organizacao=organization, papel=Papel.GESTOR)
    raw_request = APIRequestFactory().get("/times/", HTTP_X_ORGANIZATION="acme")
    force_authenticate(raw_request, user=user)
    request = Request(raw_request)
    request.organizacao_slug = "acme"

    with patch("apps.organizacoes.permissions.definir_organizacao_atual") as define_rls:
        assert TenantPermission().has_permission(request, TimeViewSet()) is True

    assert request.tenant == TenantAccess(organization.pk, "acme", membership.pk, Papel.GESTOR)
    define_rls.assert_called_once_with(organization.pk)


def test_papel_minimo_permission_reads_tenant_dataclass():
    request = Mock(tenant=TenantAccess(1, "acme", 2, Papel.GESTOR))
    view = Mock(action="create", papeis_por_action={"create": Papel.GESTOR})

    assert PapelMinimoPermission().has_permission(request, view) is True


@pytest.mark.django_db
def test_tenant_permission_denies_cached_missing_membership():
    user = UsuarioFactory()
    client = client_autenticado(user)

    first = client.get("/times/", HTTP_X_ORGANIZATION="missing")
    with patch("common.permission_cache.resolvers.tenant.TenantAccessResolver._load", wraps=TenantAccessResolver._load) as loader:
        second = client.get("/times/", HTTP_X_ORGANIZATION="missing")

    assert first.status_code == second.status_code == 403
    loader.assert_not_called()


@pytest.mark.django_db
def test_time_view_filters_and_creates_by_tenant_organization_id():
    user = UsuarioFactory()
    acme = Organizacao.objects.create(nome="Acme", slug="acme")
    other = Organizacao.objects.create(nome="Other", slug="other")
    Vinculo.objects.create(usuario=user, organizacao=acme, papel=Papel.GESTOR)
    Time.objects.create(organizacao=acme, nome="Acme team", owner=user)
    Time.objects.create(organizacao=other, nome="Other team", owner=user)
    client = client_autenticado(user)

    response = client.get("/times/", HTTP_X_ORGANIZATION="acme")
    assert [item["nome"] for item in response.data["resultados"]] == ["Acme team"]
    response = client.post("/times/", {"nome": "New team"}, format="json", HTTP_X_ORGANIZATION="acme")

    assert response.status_code == 201
    assert Time.objects.get(nome="New team").organizacao_id == acme.pk


def test_vinculo_serializer_compares_tenant_role():
    request = Mock(tenant=TenantAccess(1, "acme", 2, Papel.GESTOR))
    serializer = VinculoSerializer(data={"papel": Papel.PROPRIETARIO}, context={"request": request})

    assert not serializer.is_valid()
    assert "papel acima do seu" in str(serializer.errors["papel"][0])


def test_convite_serializer_compares_tenant_role():
    request = Mock(tenant=TenantAccess(1, "acme", 2, Papel.GESTOR))
    serializer = ConviteCreateSerializer(data={"email": "new@example.com", "papel": Papel.PROPRIETARIO}, context={"request": request})

    assert not serializer.is_valid()
    assert "papel acima do seu" in str(serializer.errors["papel"][0])


@pytest.mark.django_db
def test_middleware_initializes_only_request_tenant():
    seen = {}

    def response(request):
        seen["tenant"] = request.tenant
        seen["has_organizacao"] = hasattr(request, "organizacao")
        seen["has_vinculo"] = hasattr(request, "vinculo")
        return Mock()

    request = APIRequestFactory().get("/times/", HTTP_X_ORGANIZATION="acme")
    OrganizacaoMiddleware(response)(request)

    assert seen == {"tenant": None, "has_organizacao": False, "has_vinculo": False}
