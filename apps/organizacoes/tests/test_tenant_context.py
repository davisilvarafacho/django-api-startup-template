import json
from dataclasses import FrozenInstanceError

from django.db import IntegrityError, connection
from django.http import HttpResponse
from django.test.utils import CaptureQueriesContext

from rest_framework.test import APIRequestFactory

import pytest
from django_rls.context import clear_rls_context, get_active_rls_context
from knox.models import get_token_model

from apps.api.autenticacao.models import TokenType
from apps.organizacoes import context
from apps.organizacoes.constants import META_HEADER_ORGANIZACAO
from apps.organizacoes.middleware import OrganizacaoMiddleware
from apps.organizacoes.models import Organizacao, Papel, Vinculo
from apps.organizacoes.permissions import TenantPermission
from tests.support.usuarios import criar_usuario


def _error_code(response):
    return json.loads(response.content)["errors"][0]["code"]


def _contexto_liberado(organizacao, vinculo):
    return context.ContextoOrganizacao(organizacao=organizacao, vinculo=vinculo)


def test_contexto_organizacao_e_imutavel_e_preserva_compatibilidade():
    organizacao = Organizacao(id=7, nome="Acme", slug="acme")
    vinculo = Vinculo(id=11, organizacao=organizacao, organizacao_id=organizacao.pk, papel=Papel.ADMINISTRADOR)

    tenant = _contexto_liberado(organizacao, vinculo)

    assert tenant.organization_id == 7
    assert tenant.organization_slug == "acme"
    assert tenant.membership_id == 11
    assert tenant.role == Papel.ADMINISTRADOR
    assert tenant.has_minimum_role(Papel.GESTOR) is True
    with pytest.raises(FrozenInstanceError):
        tenant.organizacao = Organizacao(id=8, nome="Outra", slug="outra")


def test_contexto_de_api_key_nao_inventa_vinculo_ou_papel():
    organizacao = Organizacao(id=7, nome="Acme", slug="acme")

    tenant = _contexto_liberado(organizacao, None)

    assert tenant.membership_id is None
    assert tenant.role is None
    assert tenant.has_minimum_role(Papel.VISUALIZADOR) is False


def test_middleware_de_organizacao_roda_depois_da_autenticacao_do_projeto(settings):
    assert settings.MIDDLEWARE.index("apps.api.autenticacao.middleware.AuthenticationMiddleware") < settings.MIDDLEWARE.index(
        "apps.organizacoes.middleware.OrganizacaoMiddleware"
    )


@pytest.mark.django_db(transaction=True)
def test_middleware_resolve_sessao_em_uma_query_e_mantem_transacao_ate_a_resposta():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme")
    vinculo = Vinculo.objects.create(usuario=usuario, organizacao=organizacao, papel=Papel.GESTOR)
    observado = {}

    def responder(request):
        observado["tenant"] = request.tenant
        observado["organizacao"] = request.organizacao
        observado["vinculo"] = request.vinculo
        observado["atomic"] = connection.in_atomic_block
        observado["rls"] = get_active_rls_context().get(context.CHAVE_TENANT)
        return HttpResponse()

    request = APIRequestFactory().get("/times/", **{META_HEADER_ORGANIZACAO: organizacao.slug})
    request.user = usuario

    with CaptureQueriesContext(connection) as queries:
        response = OrganizacaoMiddleware(responder)(request)

    assert response.status_code == 200
    assert observado == {
        "tenant": _contexto_liberado(organizacao, vinculo),
        "organizacao": organizacao,
        "vinculo": vinculo,
        "atomic": True,
        "rls": str(organizacao.pk),
    }
    selects_de_vinculo = [query for query in queries if 'FROM "vinculo"' in query["sql"]]
    assert len(selects_de_vinculo) == 3  # resolução do tenant + validação/lock do Workspace atual
    assert context.CHAVE_TENANT not in get_active_rls_context()


@pytest.mark.django_db(transaction=True)
def test_middleware_prioriza_vinculo_da_organizacao_atual_quando_slug_foi_reutilizado():
    usuario = criar_usuario()
    organizacao_antiga = Organizacao.objects.create(nome="Acme antiga", slug="acme")
    vinculo_antigo = Vinculo.objects.create(
        usuario=usuario,
        organizacao=organizacao_antiga,
        papel=Papel.PROPRIETARIO,
    )
    organizacao_antiga.is_active = False
    organizacao_antiga.is_deleted = True
    organizacao_antiga.save(update_fields=["is_active", "is_deleted"])
    vinculo_antigo.is_active = False
    vinculo_antigo.save(update_fields=["is_active"])

    organizacao_atual = Organizacao.objects.create(nome="Acme atual", slug="acme")
    vinculo_atual = Vinculo.objects.create(
        usuario=usuario,
        organizacao=organizacao_atual,
        papel=Papel.MEMBRO,
    )
    observado = {}

    def responder(request):
        observado["tenant"] = request.tenant
        observado["rls"] = get_active_rls_context().get(context.CHAVE_TENANT)
        return HttpResponse()

    request = APIRequestFactory().get("/times/", **{META_HEADER_ORGANIZACAO: "acme"})
    request.user = usuario

    with CaptureQueriesContext(connection) as queries:
        response = OrganizacaoMiddleware(responder)(request)

    assert response.status_code == 200
    assert observado == {
        "tenant": _contexto_liberado(organizacao_atual, vinculo_atual),
        "rls": str(organizacao_atual.pk),
    }
    selects_de_vinculo = [query for query in queries if 'FROM "vinculo"' in query["sql"]]
    assert len(selects_de_vinculo) == 3  # resolução do tenant + validação/lock do Workspace atual


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("cenario", "codigo"),
    [
        ("sem_vinculo_atual", "organizations.organization_inactive"),
        ("vinculo_atual_inativo", "organizations.membership_inactive"),
        ("organizacao_atual_inativa", "organizations.organization_inactive"),
        ("usuario_alheio_organizacao_atual_inativa", "organizations.membership_required"),
    ],
)
def test_middleware_preserva_precisao_sem_enumerar_slug_reutilizado(cenario, codigo):
    usuario = criar_usuario()
    outro_usuario = criar_usuario() if cenario == "usuario_alheio_organizacao_atual_inativa" else usuario

    organizacao_antiga = Organizacao.objects.create(nome="Acme antiga", slug="acme")
    Vinculo.objects.create(
        usuario=outro_usuario,
        organizacao=organizacao_antiga,
        papel=Papel.PROPRIETARIO,
        is_active=False,
    )
    organizacao_antiga.is_active = False
    organizacao_antiga.is_deleted = True
    organizacao_antiga.save(update_fields=["is_active", "is_deleted"])

    organizacao_atual = Organizacao.objects.create(nome="Acme atual", slug="acme")
    if cenario != "sem_vinculo_atual":
        Vinculo.objects.create(
            usuario=outro_usuario,
            organizacao=organizacao_atual,
            papel=Papel.MEMBRO,
            is_active=cenario != "vinculo_atual_inativo",
        )
    if cenario in {"organizacao_atual_inativa", "usuario_alheio_organizacao_atual_inativa"}:
        organizacao_atual.is_active = False
        organizacao_atual.save(update_fields=["is_active"])

    request = APIRequestFactory().get("/times/", **{META_HEADER_ORGANIZACAO: "acme"})
    request.user = usuario

    with CaptureQueriesContext(connection) as queries:
        response = OrganizacaoMiddleware(lambda request: HttpResponse())(request)

    assert response.status_code == 403
    assert _error_code(response) == codigo
    selects_de_vinculo = [query for query in queries if 'FROM "vinculo"' in query["sql"]]
    assert len(selects_de_vinculo) == 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("cenario", "codigo"),
    [
        ("slug_inexistente", "organizations.membership_required"),
        ("sem_vinculo", "organizations.membership_required"),
        ("organizacao_alheia", "organizations.membership_required"),
        ("vinculo_inativo", "organizations.membership_inactive"),
        ("vinculo_excluido", "organizations.membership_required"),
        ("organizacao_inativa", "organizations.organization_inactive"),
        ("organizacao_excluida", "organizations.organization_inactive"),
    ],
)
def test_middleware_distingue_estados_sem_enumerar_organizacao_alheia(cenario, codigo):
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme")
    if cenario == "organizacao_alheia":
        Vinculo.objects.create(usuario=criar_usuario(), organizacao=organizacao)
    elif cenario != "sem_vinculo":
        vinculo = Vinculo.objects.create(usuario=usuario, organizacao=organizacao)
        if cenario == "vinculo_inativo":
            vinculo.is_active = False
            vinculo.save(update_fields=["is_active"])
        elif cenario == "vinculo_excluido":
            vinculo.is_deleted = True
            vinculo.save(update_fields=["is_deleted"])
        elif cenario == "organizacao_excluida":
            organizacao.is_deleted = True
            organizacao.save(update_fields=["is_deleted"])
        else:
            organizacao.is_active = False
            organizacao.save(update_fields=["is_active"])

    slug = "inexistente" if cenario == "slug_inexistente" else organizacao.slug
    request = APIRequestFactory().get("/times/", **{META_HEADER_ORGANIZACAO: slug})
    request.user = usuario
    response = OrganizacaoMiddleware(lambda request: HttpResponse())(request)

    assert response.status_code == 403
    assert _error_code(response) == codigo


@pytest.mark.django_db
@pytest.mark.parametrize(
    "path",
    [
        "/health/",
        "/admin/",
        "/organizacoes/",
        "/auth/reauthenticate/",
        "/auth/sessions/",
        "/auth/logout/",
    ],
)
def test_rota_publica_ou_sem_tenancy_nao_resolve_organizacao(path):
    request = APIRequestFactory().get(path)
    request.user = criar_usuario()
    observado = {}

    def responder(request):
        observado["tenant"] = request.tenant
        observado["tenant_required"] = request.tenant_required
        return HttpResponse()

    with CaptureQueriesContext(connection) as queries:
        response = OrganizacaoMiddleware(responder)(request)

    assert response.status_code == 200
    assert observado == {"tenant": None, "tenant_required": False}
    assert not [query for query in queries if 'FROM "organizacao"' in query["sql"] or 'FROM "vinculo"' in query["sql"]]


@pytest.mark.django_db
def test_contexto_rls_e_limpo_quando_a_resposta_interna_falha():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme")
    Vinculo.objects.create(usuario=usuario, organizacao=organizacao)

    def falhar(request):
        assert get_active_rls_context().get(context.CHAVE_TENANT) == str(organizacao.pk)
        raise RuntimeError("falha da view")

    request = APIRequestFactory().get("/times/", **{META_HEADER_ORGANIZACAO: organizacao.slug})
    request.user = usuario

    with pytest.raises(RuntimeError, match="falha da view"):
        OrganizacaoMiddleware(falhar)(request)

    assert context.CHAVE_TENANT not in get_active_rls_context()


@pytest.mark.django_db(transaction=True)
def test_contexto_rls_e_limpo_sem_mascarar_erro_de_transacao_quebrada():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme")
    Vinculo.objects.create(usuario=usuario, organizacao=organizacao)

    def quebrar_transacao(request):
        Vinculo.objects.create(usuario=usuario, organizacao=organizacao)

    request = APIRequestFactory().get("/times/", **{META_HEADER_ORGANIZACAO: organizacao.slug})
    request.user = usuario

    try:
        with pytest.raises(IntegrityError):
            OrganizacaoMiddleware(quebrar_transacao)(request)
        assert context.CHAVE_TENANT not in get_active_rls_context()
    finally:
        clear_rls_context({context.CHAVE_TENANT})


@pytest.mark.django_db(transaction=True)
def test_middleware_deriva_contexto_de_api_key_sem_inventar_vinculo():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme")
    Vinculo.objects.create(usuario=usuario, organizacao=organizacao, papel=Papel.PROPRIETARIO)
    token, _ = get_token_model().objects.create(
        responsavel=usuario,
        type=TokenType.API_KEY,
        created_by=usuario,
        organization=organizacao,
        name="Integração",
        scopes=["teams:read"],
    )
    observado = {}

    def responder(request):
        observado["tenant"] = request.tenant
        observado["vinculo"] = request.vinculo
        observado["atomic"] = connection.in_atomic_block
        observado["rls"] = get_active_rls_context().get(context.CHAVE_TENANT)
        return HttpResponse()

    request = APIRequestFactory().get("/times/")
    request.user = usuario
    request.auth = token
    response = OrganizacaoMiddleware(responder)(request)

    assert response.status_code == 200
    assert observado == {
        "tenant": _contexto_liberado(organizacao, None),
        "vinculo": None,
        "atomic": True,
        "rls": str(organizacao.pk),
    }


def test_tenant_permission_recusa_rota_tenantizada_sem_contexto():
    request = APIRequestFactory().get("/times/")
    request.tenant = None

    assert TenantPermission().has_permission(request, object()) is False


def test_tenant_permission_aceita_isencao_ja_resolvida_pelo_middleware():
    request = APIRequestFactory().get("/times/")
    request.tenant = None
    request.tenant_required = False

    assert TenantPermission().has_permission(request, object()) is True


@pytest.mark.django_db(transaction=True)
def test_middleware_publica_contexto_do_tenant_dentro_do_rls():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme")
    vinculo = Vinculo.objects.create(usuario=usuario, organizacao=organizacao, papel=Papel.ADMINISTRADOR)
    observado = {}

    def responder(request):
        observado.update(
            tenant=request.tenant,
            atomic=connection.in_atomic_block,
            rls=get_active_rls_context().get(context.CHAVE_TENANT),
        )
        return HttpResponse()

    request = APIRequestFactory().get("/times/", **{META_HEADER_ORGANIZACAO: organizacao.slug})
    request.user = usuario
    response = OrganizacaoMiddleware(responder)(request)

    assert response.status_code == 200
    assert observado == {
        "tenant": _contexto_liberado(organizacao, vinculo),
        "atomic": True,
        "rls": str(organizacao.pk),
    }
