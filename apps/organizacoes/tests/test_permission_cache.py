from unittest.mock import Mock, patch

from django.db import models

from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, force_authenticate

import pytest

from apps.api.core.errors import APIError
from apps.organizacoes.access import TenantAccessResolver
from apps.organizacoes.errors import OrganizationErrorCode
from apps.organizacoes.middleware import OrganizacaoMiddleware
from apps.organizacoes.models import Organizacao, Papel, Time, Vinculo
from apps.organizacoes.permissions import PapelMinimoPermission, TenantPermission
from apps.organizacoes.rules import e_gestor
from apps.organizacoes.serializers import ConviteCreateSerializer, VinculoSerializer
from apps.organizacoes.tests.test_api import client_autenticado
from apps.organizacoes.views import TimeViewSet
from internal_frameworks.permission_cache.types import TenantAccess
from tests.support.usuarios import criar_usuario


@pytest.mark.django_db
def test_tenant_permission_sets_request_tenant_and_rls_context():
    user = criar_usuario()
    organization = Organizacao.objects.create(nome="Acme", slug="acme")
    membership = Vinculo.objects.create(usuario=user, organizacao=organization, papel=Papel.GESTOR)
    raw_request = APIRequestFactory().get("/times/", HTTP_X_ORGANIZATION="acme")
    force_authenticate(raw_request, user=user)
    request = Request(raw_request)
    request.organizacao_slug = "acme"

    with patch("apps.organizacoes.permissions.definir_organizacao_atual") as define_rls:
        assert TenantPermission().has_permission(request, TimeViewSet()) is True

    assert request.tenant == TenantAccess(organization.pk, "acme", membership.pk, Papel.GESTOR)
    define_rls.assert_called_once_with(request.tenant.organization_id)


def test_papel_minimo_permission_reads_tenant_dataclass():
    request = Mock(tenant=TenantAccess(1, "acme", 2, Papel.GESTOR))
    view = Mock(action="create", papeis_por_action={"create": Papel.GESTOR})

    assert PapelMinimoPermission().has_permission(request, view) is True


@pytest.mark.django_db
def test_papel_minimo_rule_reuses_tenant_resolver(monkeypatch):
    usuario = criar_usuario()
    objeto = Mock(organizacao_id=1)
    resolver = Mock(return_value=TenantAccess(1, "acme", 2, Papel.GESTOR))
    monkeypatch.setattr(TenantAccessResolver, "by_organization_id", resolver)

    assert e_gestor.test(usuario, objeto) is True
    resolver.assert_called_once_with(
        usuario.pk,
        objeto.organizacao_id,
        database_alias=usuario._state.db or "default",
        metric_layer="rules",
    )


@pytest.mark.django_db
def test_tenant_permission_denies_cached_missing_membership():
    user = criar_usuario()
    client = client_autenticado(user)

    first = client.get("/times/", HTTP_X_ORGANIZATION="missing")
    with patch("apps.organizacoes.access.TenantAccessResolver._load", wraps=TenantAccessResolver._load) as loader:
        second = client.get("/times/", HTTP_X_ORGANIZATION="missing")

    assert first.status_code == second.status_code == 403
    loader.assert_not_called()


@pytest.mark.django_db
def test_time_view_filters_and_creates_by_tenant_organization_id():
    user = criar_usuario()
    acme = Organizacao.objects.create(nome="Acme", slug="acme")
    other = Organizacao.objects.create(nome="Other", slug="other")
    Vinculo.objects.create(usuario=user, organizacao=acme, papel=Papel.GESTOR)
    Time.objects.create(organizacao=acme, nome="Acme team", created_by=user)
    Time.objects.create(organizacao=other, nome="Other team", created_by=user)
    client = client_autenticado(user)

    response = client.get("/times/", HTTP_X_ORGANIZATION="acme")
    assert [item["nome"] for item in response.data["resultados"]] == ["Acme team"]
    response = client.post("/times/", {"nome": "New team"}, format="json", HTTP_X_ORGANIZATION="acme")

    assert response.status_code == 201
    assert Time.objects.get(nome="New team").organizacao_id == acme.pk


def test_vinculo_serializer_compares_tenant_role():
    request = Mock(tenant=TenantAccess(1, "acme", 2, Papel.GESTOR))
    serializer = VinculoSerializer(data={"papel": Papel.PROPRIETARIO}, context={"request": request})

    with pytest.raises(APIError) as excinfo:
        serializer.is_valid()

    assert excinfo.value.code == OrganizationErrorCode.ROLE_INSUFFICIENT.value
    assert "papel acima do seu" in excinfo.value.message


def test_convite_serializer_compares_tenant_role():
    request = Mock(tenant=TenantAccess(1, "acme", 2, Papel.GESTOR))
    serializer = ConviteCreateSerializer(data={"email": "new@example.com", "papel": Papel.PROPRIETARIO}, context={"request": request})

    with pytest.raises(APIError) as excinfo:
        serializer.is_valid()

    assert excinfo.value.code == OrganizationErrorCode.ROLE_INSUFFICIENT.value
    assert "papel acima do seu" in excinfo.value.message


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


@pytest.mark.django_db
def test_tenant_resolver_e_papel_minimo_recusam_conta_excluida_ou_inativa():
    """Vínculo ativo não basta: a conta por trás dele precisa continuar existindo."""
    excluido = criar_usuario()
    inativo = criar_usuario(is_active=False)
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme-excluidos")
    Vinculo.objects.create(usuario=excluido, organizacao=organizacao, papel=Papel.GESTOR)
    Vinculo.objects.create(usuario=inativo, organizacao=organizacao, papel=Papel.GESTOR)
    # Conta legada: excluída antes da correção, portanto ainda ativa.
    models.QuerySet.update(Usuario.all_objects.filter(pk=excluido.pk), is_deleted=True)
    excluido = Usuario.all_objects.get(pk=excluido.pk)
    objeto = Mock(organizacao_id=organizacao.pk)
    resolver = TenantAccessResolver()

    assert resolver.by_organization_id(excluido.pk, organizacao.pk) is None
    assert resolver.by_slug(inativo.pk, organizacao.slug) is None
    assert e_gestor.test(excluido, objeto) is False
    assert e_gestor.test(inativo, objeto) is False


@pytest.mark.django_db
def test_tenant_resolver_recusa_snapshot_aquecido_apos_exclusao_em_lote(django_capture_on_commit_callbacks):
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Cache", slug="cache-exclusao")
    Vinculo.objects.create(usuario=usuario, organizacao=organizacao, papel=Papel.GESTOR)
    resolver = TenantAccessResolver()

    assert resolver.by_slug(usuario.pk, organizacao.slug) is not None
    # A API em lote precisa executar o protocolo e invalidar o snapshot no commit.
    with django_capture_on_commit_callbacks(execute=True):
        Usuario.all_objects.filter(pk=usuario.pk).update(is_deleted=True)

    assert resolver.by_slug(usuario.pk, organizacao.slug) is None
