"""Onboarding transacional de organizações."""

from django.utils import timezone

import pytest

from apps.api.core.errors import APIError
from apps.organizacoes.models import Organizacao, Papel, Vinculo
from apps.usuarios.accounts import Contas
from apps.usuarios.models import Usuario
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ("campos", "codigo"),
    [
        ({"is_active": False}, "auth.user_inactive"),
        ({"is_deleted": True}, "auth.user_inactive"),
        ({"exclusao_agendada_para": timezone.now()}, "auth.user_inactive"),
        ({"email_verificado_em": None}, "account.email_not_verified"),
    ],
)
def test_conta_precisa_estar_ativa_sem_exclusao_e_com_email_verificado(campos, codigo):
    usuario = criar_usuario(email_verificado_em=timezone.now())
    Usuario.all_objects.filter(pk=usuario.pk).update(**campos)
    usuario = Usuario.all_objects.get(pk=usuario.pk)

    with pytest.raises(APIError) as excinfo:
        Contas.validar_para_onboarding(usuario)

    assert excinfo.value.code == codigo


def test_onboarding_inapto_nao_cria_organizacao():
    from apps.organizacoes.onboarding import OrganizationOnboarding

    usuario = criar_usuario(email_verificado_em=None)

    with pytest.raises(APIError) as excinfo:
        OrganizationOnboarding.criar(usuario=usuario, nome="Inapta", slug="inapta")

    assert excinfo.value.code == "account.email_not_verified"
    assert Organizacao.all_objects.filter(slug="inapta").exists() is False


def test_onboarding_copia_email_e_cria_proprietario_e_workspace_inicial():
    from apps.organizacoes.onboarding import OrganizationOnboarding
    from apps.workspaces.models import VinculoWorkspace, Workspace

    usuario = criar_usuario(email="owner@example.com", email_verificado_em=timezone.now())

    organizacao = OrganizationOnboarding.criar(usuario=usuario, nome="Acme", slug="acme")

    organizacao.refresh_from_db()
    vinculo = Vinculo.objects.get(organizacao=organizacao, usuario=usuario)
    workspace = Workspace.objects.get(organizacao=organizacao, slug="principal")
    assert organizacao.email_faturamento == "owner@example.com"
    assert vinculo.papel == Papel.PROPRIETARIO
    assert vinculo.current_workspace_id == workspace.pk
    assert VinculoWorkspace.objects.filter(vinculo=vinculo, workspace=workspace, selected_for_view=True).count() == 1


@pytest.mark.parametrize(
    "colaborador",
    ["contas", "organizacoes", "vinculos", "workspace"],
)
def test_onboarding_reverte_organizacao_e_vinculo_quando_colaborador_falha(monkeypatch, colaborador):
    from apps.organizacoes import onboarding
    from apps.organizacoes.memberships import Vinculos
    from apps.organizacoes.organizations import Organizacoes

    usuario = criar_usuario(email_verificado_em=timezone.now())

    if colaborador == "contas":
        monkeypatch.setattr(Contas, "validar_para_onboarding", lambda usuario: (_ for _ in ()).throw(RuntimeError("contas")))
    elif colaborador == "organizacoes":
        original = Organizacoes.criar.__func__

        def criar_e_falhar(cls, **kwargs):
            original(cls, **kwargs)
            raise RuntimeError("organizacoes")

        monkeypatch.setattr(Organizacoes, "criar", classmethod(criar_e_falhar))
    elif colaborador == "vinculos":
        original = Vinculos.criar_proprietario.__func__

        def vincular_e_falhar(cls, organizacao, usuario):
            original(cls, organizacao, usuario)
            raise RuntimeError("vinculos")

        monkeypatch.setattr(Vinculos, "criar_proprietario", classmethod(vincular_e_falhar))
    else:
        from apps.workspaces.workspaces import Workspaces

        monkeypatch.setattr(
            Workspaces,
            "criar_inicial",
            classmethod(lambda cls, **kwargs: (_ for _ in ()).throw(RuntimeError("workspace"))),
        )

    with pytest.raises(RuntimeError, match=colaborador):
        onboarding.OrganizationOnboarding.criar(
            usuario=usuario,
            nome="Rollback",
            slug=f"rollback-{colaborador}",
        )

    assert Organizacao.all_objects.filter(slug=f"rollback-{colaborador}").exists() is False
    assert Vinculo.all_objects.filter(usuario=usuario, organizacao__slug=f"rollback-{colaborador}").exists() is False
