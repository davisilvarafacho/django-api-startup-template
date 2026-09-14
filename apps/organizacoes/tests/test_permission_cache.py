from unittest.mock import Mock

from django.db import connection
from django.test.utils import CaptureQueriesContext

from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, force_authenticate

import pytest

from apps.api.core.errors import APIError
from apps.organizacoes.access import TenantAccessResolver
from apps.organizacoes.errors import OrganizationErrorCode
from apps.organizacoes.middleware import OrganizacaoMiddleware
from apps.organizacoes.models import Convite, Organizacao, Papel, Time, Vinculo
from apps.organizacoes.permissions import PapelMinimoPermission, TenantPermission
from apps.organizacoes.rules import e_gestor
from apps.organizacoes.serializers import ConviteCreateSerializer, ConviteSerializer, TimeSerializer, VinculoSerializer
from apps.organizacoes.tests.test_api import client_autenticado
from apps.organizacoes.tests.test_tenant_context import _contexto_liberado
from apps.organizacoes.views import ConviteViewSet, TimeViewSet, VinculoViewSet
from internal_frameworks.permission_cache.types import TenantAccess
from tests.support.usuarios import criar_usuario


@pytest.mark.django_db
def test_tenant_permission_consumes_prepared_context_without_database(django_assert_num_queries):
    user = criar_usuario()
    organization = Organizacao.objects.create(nome="Acme", slug="acme")
    membership = Vinculo.objects.create(usuario=user, organizacao=organization, papel=Papel.GESTOR)
    raw_request = APIRequestFactory().get("/times/", HTTP_X_ORGANIZATION="acme")
    force_authenticate(raw_request, user=user)
    request = Request(raw_request)
    request.tenant = _contexto_liberado(organization, membership)

    with django_assert_num_queries(0):
        assert TenantPermission().has_permission(request, TimeViewSet()) is True

    assert request.tenant.organization_id == organization.pk


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
def test_middleware_resolves_missing_membership_once_per_request():
    user = criar_usuario()
    client = client_autenticado(user)

    first = client.get("/times/", HTTP_X_ORGANIZATION="missing")
    with CaptureQueriesContext(connection) as queries:
        second = client.get("/times/", HTTP_X_ORGANIZATION="missing")

    assert first.status_code == second.status_code == 403
    selects_de_vinculo = [query for query in queries if 'FROM "vinculo"' in query["sql"]]
    assert len(selects_de_vinculo) == 1


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
def test_criacao_de_convite_revalida_papel_atual_do_ator():
    ator = criar_usuario(email="ator-convite@example.com")
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme-ator-convite")
    vinculo_ator = Vinculo.objects.create(usuario=ator, organizacao=organizacao, papel=Papel.MEMBRO)
    request = Mock(
        user=ator,
        tenant=TenantAccess(organizacao.pk, organizacao.slug, vinculo_ator.pk, Papel.GESTOR),
    )
    serializer = ConviteCreateSerializer(
        data={"email": "new@example.com", "papel": Papel.MEMBRO},
        context={"request": request},
    )
    serializer.is_valid(raise_exception=True)

    with pytest.raises(APIError) as excinfo:
        serializer.save(organizacao_id=organizacao.pk, convidado_por=ator)

    assert excinfo.value.code == OrganizationErrorCode.ROLE_INSUFFICIENT.value
    assert Convite.objects.filter(organizacao=organizacao, email="new@example.com").exists() is False


@pytest.mark.django_db
def test_atualizacao_de_convite_revalida_teto_do_papel_atual_do_ator():
    ator = criar_usuario(email="ator-patch-convite@example.com")
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme-ator-patch-convite")
    vinculo_ator = Vinculo.objects.create(usuario=ator, organizacao=organizacao, papel=Papel.GESTOR)
    convite = Convite.objects.create(organizacao=organizacao, email="convite@example.com", papel=Papel.MEMBRO)
    request = Mock(
        user=ator,
        tenant=TenantAccess(organizacao.pk, organizacao.slug, vinculo_ator.pk, Papel.PROPRIETARIO),
    )
    serializer = ConviteSerializer(
        convite,
        data={"papel": Papel.PROPRIETARIO},
        partial=True,
        context={"request": request},
    )
    serializer.is_valid(raise_exception=True)

    with pytest.raises(APIError) as excinfo:
        serializer.save()

    assert excinfo.value.code == OrganizationErrorCode.ROLE_INSUFFICIENT.value
    convite.refresh_from_db()
    assert convite.papel == Papel.MEMBRO


@pytest.mark.django_db
def test_atualizacao_de_vinculo_revalida_teto_do_papel_atual_do_ator():
    ator = criar_usuario(email="ator-patch-vinculo@example.com")
    alvo = criar_usuario(email="alvo-patch-vinculo@example.com")
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme-ator-patch-vinculo")
    vinculo_ator = Vinculo.objects.create(usuario=ator, organizacao=organizacao, papel=Papel.ADMINISTRADOR)
    vinculo_alvo = Vinculo.objects.create(usuario=alvo, organizacao=organizacao, papel=Papel.MEMBRO)
    request = Mock(
        user=ator,
        tenant=TenantAccess(organizacao.pk, organizacao.slug, vinculo_ator.pk, Papel.PROPRIETARIO),
    )
    serializer = VinculoSerializer(
        vinculo_alvo,
        data={"papel": Papel.PROPRIETARIO},
        partial=True,
        context={"request": request},
    )
    serializer.is_valid(raise_exception=True)

    with pytest.raises(APIError) as excinfo:
        serializer.save()

    assert excinfo.value.code == OrganizationErrorCode.ROLE_INSUFFICIENT.value
    vinculo_alvo.refresh_from_db()
    assert vinculo_alvo.papel == Papel.MEMBRO


@pytest.mark.django_db
def test_remocao_de_vinculo_revalida_papel_atual_do_ator():
    ator = criar_usuario(email="ator-delete-vinculo@example.com")
    alvo = criar_usuario(email="alvo-delete-vinculo@example.com")
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme-ator-delete-vinculo")
    Vinculo.objects.create(usuario=ator, organizacao=organizacao, papel=Papel.GESTOR)
    vinculo_alvo = Vinculo.objects.create(usuario=alvo, organizacao=organizacao, papel=Papel.MEMBRO)
    view = VinculoViewSet()
    view.request = Mock(user=ator)

    with pytest.raises(APIError) as excinfo:
        view.perform_destroy(vinculo_alvo)

    assert excinfo.value.code == OrganizationErrorCode.ROLE_INSUFFICIENT.value
    assert Vinculo.objects.filter(pk=vinculo_alvo.pk).exists() is True


@pytest.mark.django_db
def test_criacao_de_time_revalida_papel_atual_do_ator():
    ator = criar_usuario(email="ator-create-time@example.com")
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme-ator-create-time")
    vinculo_ator = Vinculo.objects.create(usuario=ator, organizacao=organizacao, papel=Papel.MEMBRO)
    request = Mock(
        user=ator,
        tenant=TenantAccess(organizacao.pk, organizacao.slug, vinculo_ator.pk, Papel.GESTOR),
    )
    serializer = TimeSerializer(data={"nome": "Produto"}, context={"request": request})
    serializer.is_valid(raise_exception=True)

    with pytest.raises(APIError) as excinfo:
        serializer.save(organizacao_id=organizacao.pk)

    assert excinfo.value.code == OrganizationErrorCode.ROLE_INSUFFICIENT.value
    assert Time.objects.filter(organizacao=organizacao, nome="Produto").exists() is False


@pytest.mark.django_db
def test_atualizacao_de_time_revalida_papel_atual_do_ator():
    ator = criar_usuario(email="ator-patch-time@example.com")
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme-ator-patch-time")
    vinculo_ator = Vinculo.objects.create(usuario=ator, organizacao=organizacao, papel=Papel.MEMBRO)
    time = Time.objects.create(organizacao=organizacao, nome="Antes")
    request = Mock(
        user=ator,
        tenant=TenantAccess(organizacao.pk, organizacao.slug, vinculo_ator.pk, Papel.GESTOR),
    )
    serializer = TimeSerializer(time, data={"nome": "Depois"}, partial=True, context={"request": request})
    serializer.is_valid(raise_exception=True)

    with pytest.raises(APIError) as excinfo:
        serializer.save()

    assert excinfo.value.code == OrganizationErrorCode.ROLE_INSUFFICIENT.value
    time.refresh_from_db()
    assert time.nome == "Antes"


@pytest.mark.django_db
def test_remocao_de_time_revalida_papel_atual_do_ator():
    ator = criar_usuario(email="ator-delete-time@example.com")
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme-ator-delete-time")
    Vinculo.objects.create(usuario=ator, organizacao=organizacao, papel=Papel.MEMBRO)
    time = Time.objects.create(organizacao=organizacao, nome="Produto")
    view = TimeViewSet()
    view.request = Mock(user=ator)

    with pytest.raises(APIError) as excinfo:
        view.perform_destroy(time)

    assert excinfo.value.code == OrganizationErrorCode.ROLE_INSUFFICIENT.value
    assert Time.objects.filter(pk=time.pk).exists() is True


@pytest.mark.django_db
def test_remocao_de_convite_revalida_papel_atual_do_ator():
    ator = criar_usuario(email="ator-delete-convite@example.com")
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme-ator-delete-convite")
    Vinculo.objects.create(usuario=ator, organizacao=organizacao, papel=Papel.MEMBRO)
    convite = Convite.objects.create(organizacao=organizacao, email="convidado@example.com")
    view = ConviteViewSet()
    view.request = Mock(user=ator)

    with pytest.raises(APIError) as excinfo:
        view.perform_destroy(convite)

    assert excinfo.value.code == OrganizationErrorCode.ROLE_INSUFFICIENT.value
    assert Convite.objects.filter(pk=convite.pk).exists() is True


@pytest.mark.django_db
def test_middleware_initializes_only_request_tenant_on_public_route():
    seen = {}

    def response(request):
        seen["tenant"] = request.tenant
        seen["has_organizacao"] = hasattr(request, "organizacao")
        seen["has_vinculo"] = hasattr(request, "vinculo")
        return Mock()

    request = APIRequestFactory().get("/health/")
    OrganizacaoMiddleware(response)(request)

    assert seen == {"tenant": None, "has_organizacao": False, "has_vinculo": False}
