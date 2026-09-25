from django.http import HttpResponse
from django.test import override_settings

from rest_framework.test import APIRequestFactory

import pytest
from django_rls.context import get_active_rls_context

from apps.assinaturas.access_policies import SituacaoAcesso, StatusAcesso
from apps.assinaturas.models import AssinaturaOrganizacao, StatusAssinatura, StatusFinanceiro
from apps.assinaturas.subscriptions import UtilizacaoSeats
from apps.organizacoes.constants import META_HEADER_ORGANIZACAO
from apps.organizacoes.context import ContextoOrganizacao
from apps.organizacoes.middleware import OrganizacaoMiddleware
from apps.organizacoes.models import Organizacao, Papel, Vinculo
from apps.workspaces.context import (
    CHAVE_CURRENT_WORKSPACE,
    CHAVE_MEMBERSHIP,
    CHAVE_WORKSPACE_MODE,
    definir_contexto_workspace_api_key,
    definir_contexto_workspace_control,
    definir_contexto_workspace_membership,
    definir_contexto_workspace_system,
)
from apps.workspaces.models import VinculoWorkspace, Workspace
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db


def _politica_liberada(organizacao, vinculo, *, regularizacao_assinatura):
    assinatura = AssinaturaOrganizacao(
        id=organizacao.pk,
        organizacao=organizacao,
        organizacao_id=organizacao.pk,
        status=StatusAssinatura.ATIVA,
        status_financeiro=StatusFinanceiro.ISENTO,
        seats_contratados=1,
    )
    utilizacao = UtilizacaoSeats(
        contratados=1,
        consumidos=0,
        reservados=0,
        comprometidos=0,
        disponiveis=1,
        excesso_real=0,
        excesso_comprometido=0,
    )
    return ContextoOrganizacao(
        organizacao=organizacao,
        vinculo=vinculo,
        assinatura=assinatura,
        utilizacao_seats=utilizacao,
        situacao_acesso=SituacaoAcesso(status=StatusAcesso.LIBERADO, motivos=(), regularizar_ate=None),
    )


def test_contexto_humano_publica_vinculo_e_workspace_atual():
    definir_contexto_workspace_membership(membership_id=11, current_workspace_id=23)

    assert get_active_rls_context() == {
        CHAVE_MEMBERSHIP: "11",
        CHAVE_CURRENT_WORKSPACE: "23",
        CHAVE_WORKSPACE_MODE: "membership",
    }


def test_contexto_api_key_limpa_vinculo_e_workspace_atual():
    definir_contexto_workspace_membership(membership_id=11, current_workspace_id=23)
    definir_contexto_workspace_api_key()

    assert get_active_rls_context() == {CHAVE_WORKSPACE_MODE: "api_key"}


def test_contexto_control_e_system_publicam_modos_fechados():
    definir_contexto_workspace_control()
    assert get_active_rls_context() == {CHAVE_WORKSPACE_MODE: "control"}

    definir_contexto_workspace_system()
    assert get_active_rls_context() == {CHAVE_WORKSPACE_MODE: "system"}


@pytest.mark.django_db(transaction=True)
def test_middleware_publica_current_humano_e_limpa_contexto_ao_final():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme")
    vinculo = Vinculo.objects.create(usuario=usuario, organizacao=organizacao, papel=Papel.GESTOR)
    workspace = Workspace.objects.create(organizacao=organizacao, nome="Matriz", slug="matriz")
    VinculoWorkspace.objects.create(vinculo=vinculo, workspace=workspace)
    vinculo.current_workspace = workspace
    vinculo.save(update_fields=["current_workspace"])
    observado = {}

    def responder(request):
        observado.update(
            current_workspace=request.current_workspace,
            workspace_required=request.workspace_required,
            rls=get_active_rls_context(),
        )
        return HttpResponse()

    request = APIRequestFactory().get("/times/", **{META_HEADER_ORGANIZACAO: organizacao.slug})
    request.user = usuario

    response = OrganizacaoMiddleware(responder, politica_comercial=_politica_liberada)(request)

    assert response.status_code == 200
    assert observado["current_workspace"].pk == workspace.pk
    assert observado["workspace_required"] is True
    assert observado["rls"][CHAVE_MEMBERSHIP] == str(vinculo.pk)
    assert CHAVE_MEMBERSHIP not in get_active_rls_context()


@pytest.mark.django_db(transaction=True)
@override_settings(ROOT_URLCONF="apps.api.core.tests.test_route_markers")
def test_middleware_control_nao_exige_current_workspace():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme")
    vinculo = Vinculo.objects.create(usuario=usuario, organizacao=organizacao, papel=Papel.GESTOR)
    workspace = Workspace.objects.create(organizacao=organizacao, nome="Matriz", slug="matriz")
    VinculoWorkspace.objects.create(vinculo=vinculo, workspace=workspace)
    vinculo.current_workspace = workspace
    vinculo.save(update_fields=["current_workspace"])
    observado = {}

    def responder(request):
        observado.update(workspace_required=request.workspace_required, rls=get_active_rls_context())
        return HttpResponse()

    request = APIRequestFactory().post("/_test/sem-workspace/", **{META_HEADER_ORGANIZACAO: organizacao.slug})
    request.user = usuario

    response = OrganizacaoMiddleware(responder, politica_comercial=_politica_liberada)(request)

    assert response.status_code == 200
    assert observado == {"workspace_required": False, "rls": {"tenant_id": str(organizacao.pk), "workspace_mode": "control"}}
