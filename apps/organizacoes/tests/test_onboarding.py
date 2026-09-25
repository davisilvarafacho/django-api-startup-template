"""Onboarding transacional de organizações sem implementação comercial provisória."""

from django.utils import timezone

import pytest
from django_rls.context import get_active_rls_context

from apps.api.core.errors import APIError
from apps.organizacoes.models import Organizacao, Papel, Vinculo
from apps.usuarios.accounts import Contas
from apps.usuarios.models import Usuario
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db


class CatalogoPlanosTeste:
    chamadas = []

    @classmethod
    def obter_versao_inicial(cls, *, codigo, periodicidade, moeda):
        cls.chamadas.append(
            {
                "codigo": codigo,
                "periodicidade": periodicidade,
                "moeda": moeda,
                "contexto_rls": dict(get_active_rls_context()),
            }
        )
        return "versao-inicial", "preco-inicial"


class AssinaturasTeste:
    chamadas = []

    @classmethod
    def criar_gratuita(cls, *, organizacao, versao_plano, preco_plano):
        cls.chamadas.append(
            {
                "modo": "gratuito",
                "organizacao_id": organizacao.pk,
                "versao_plano": versao_plano,
                "preco_plano": preco_plano,
                "contexto_rls": dict(get_active_rls_context()),
            }
        )
        return object()

    @classmethod
    def criar_trial(cls, *, organizacao, versao_plano, preco_plano):
        cls.chamadas.append(
            {
                "modo": "trial",
                "organizacao_id": organizacao.pk,
                "versao_plano": versao_plano,
                "preco_plano": preco_plano,
                "contexto_rls": dict(get_active_rls_context()),
            }
        )
        return object()


@pytest.fixture(autouse=True)
def _limpar_colaboradores():
    CatalogoPlanosTeste.chamadas.clear()
    AssinaturasTeste.chamadas.clear()


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


def test_onboarding_inapto_falha_antes_de_carregar_modulos_comerciais(monkeypatch):
    from apps.organizacoes import onboarding

    usuario = criar_usuario(email_verificado_em=None)
    monkeypatch.setattr(
        onboarding,
        "_carregar_colaboradores_comerciais",
        lambda: (_ for _ in ()).throw(RuntimeError("comercial não deveria carregar")),
    )

    with pytest.raises(APIError) as excinfo:
        onboarding.OrganizationOnboarding.criar(usuario=usuario, nome="Inapta", slug="inapta")

    assert excinfo.value.code == "account.email_not_verified"


@pytest.mark.parametrize(
    ("modo", "metodo_esperado"),
    [("gratuito", "gratuito"), ("trial", "trial")],
)
def test_onboarding_copia_email_e_cria_proprietario_e_contrato_sob_rls(settings, modo, metodo_esperado):
    from apps.organizacoes.onboarding import OrganizationOnboarding
    from apps.workspaces.models import VinculoWorkspace, Workspace

    settings.ASSINATURAS_ONBOARDING_MODO = modo
    settings.ASSINATURAS_ONBOARDING_PLANO = "inicial"
    settings.ASSINATURAS_ONBOARDING_PERIODICIDADE = "mensal"
    usuario = criar_usuario(email="owner@example.com", email_verificado_em=timezone.now())

    organizacao = OrganizationOnboarding.criar(
        usuario=usuario,
        nome="Acme",
        slug=f"acme-{modo}",
        catalogo_planos=CatalogoPlanosTeste,
        assinaturas=AssinaturasTeste,
    )

    organizacao.refresh_from_db()
    vinculo = Vinculo.objects.get(organizacao=organizacao, usuario=usuario)
    workspace = Workspace.objects.get(organizacao=organizacao, slug="principal")
    assert organizacao.email_faturamento == "owner@example.com"
    assert vinculo.papel == Papel.PROPRIETARIO
    assert vinculo.current_workspace_id == workspace.pk
    assert VinculoWorkspace.objects.filter(vinculo=vinculo, workspace=workspace, selected_for_view=True).count() == 1
    assert CatalogoPlanosTeste.chamadas == [
        {
            "codigo": "inicial",
            "periodicidade": "mensal",
            "moeda": "BRL",
            "contexto_rls": {},
        }
    ]
    assert AssinaturasTeste.chamadas == [
        {
            "modo": metodo_esperado,
            "organizacao_id": organizacao.pk,
            "versao_plano": "versao-inicial",
            "preco_plano": "preco-inicial",
            "contexto_rls": {"tenant_id": str(organizacao.pk), "workspace_mode": "system"},
        }
    ]


@pytest.mark.parametrize(
    "colaborador",
    ["contas", "organizacoes", "vinculos", "workspace", "catalogo", "assinaturas"],
)
def test_onboarding_reverte_organizacao_e_vinculo_quando_colaborador_falha(monkeypatch, settings, colaborador):
    from apps.organizacoes import onboarding
    from apps.organizacoes.memberships import Vinculos
    from apps.organizacoes.organizations import Organizacoes

    settings.ASSINATURAS_ONBOARDING_MODO = "gratuito"
    settings.ASSINATURAS_ONBOARDING_PLANO = "inicial"
    settings.ASSINATURAS_ONBOARDING_PERIODICIDADE = "mensal"
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
    elif colaborador == "workspace":
        from apps.workspaces.workspaces import Workspaces

        monkeypatch.setattr(
            Workspaces,
            "criar_inicial",
            classmethod(lambda cls, **kwargs: (_ for _ in ()).throw(RuntimeError("workspace"))),
        )
    elif colaborador == "catalogo":
        monkeypatch.setattr(
            CatalogoPlanosTeste,
            "obter_versao_inicial",
            classmethod(lambda cls, **kwargs: (_ for _ in ()).throw(RuntimeError("catalogo"))),
        )
    else:
        monkeypatch.setattr(
            AssinaturasTeste,
            "criar_gratuita",
            classmethod(lambda cls, **kwargs: (_ for _ in ()).throw(RuntimeError("assinaturas"))),
        )

    with pytest.raises(RuntimeError, match=colaborador):
        onboarding.OrganizationOnboarding.criar(
            usuario=usuario,
            nome="Rollback",
            slug=f"rollback-{colaborador}",
            catalogo_planos=CatalogoPlanosTeste,
            assinaturas=AssinaturasTeste,
        )

    assert Organizacao.all_objects.filter(slug=f"rollback-{colaborador}").exists() is False
    assert Vinculo.all_objects.filter(usuario=usuario, organizacao__slug=f"rollback-{colaborador}").exists() is False
