"""Orquestração transacional do onboarding de uma organização."""

from django.db import transaction

from apps.organizacoes.memberships import Vinculos
from apps.organizacoes.models import Organizacao
from apps.organizacoes.organizations import Organizacoes
from apps.usuarios.accounts import Contas
from apps.usuarios.models import Usuario


class OrganizationOnboarding:
    """Coordena conta, tenant, propriedade e workspace inicial."""

    @classmethod
    def criar(
        cls,
        *,
        usuario: Usuario,
        nome: str,
        slug: str,
    ) -> Organizacao:
        database_alias = usuario._state.db or "default"
        with transaction.atomic(using=database_alias):
            conta = Contas.validar_para_onboarding(usuario)
            organizacao = Organizacoes.criar(nome=nome, slug=slug, proprietario=conta)
            proprietario = Vinculos.criar_proprietario(organizacao, conta)
            from apps.workspaces.workspaces import Workspaces

            Workspaces.criar_inicial(organizacao=organizacao, proprietario=proprietario)
            return organizacao
