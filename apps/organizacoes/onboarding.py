"""Orquestração transacional do onboarding de uma organização."""

from typing import Protocol

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction

from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.memberships import Vinculos
from apps.organizacoes.models import Organizacao
from apps.organizacoes.organizations import Organizacoes
from apps.usuarios.accounts import Contas
from apps.usuarios.models import Usuario


class CatalogoPlanosOnboarding(Protocol):
    """Parte do catálogo consumida antes de ``apps.assinaturas`` existir."""

    @classmethod
    def obter_versao_inicial(cls, *, codigo: str, periodicidade: str, moeda: str) -> tuple[object, object]: ...


class AssinaturasOnboarding(Protocol):
    """Presets de contrato que a Task 9 ligará à implementação comercial."""

    @classmethod
    def criar_gratuita(cls, *, organizacao: Organizacao, versao_plano: object, preco_plano: object) -> object: ...

    @classmethod
    def criar_trial(cls, *, organizacao: Organizacao, versao_plano: object, preco_plano: object) -> object: ...


def _carregar_colaboradores_comerciais() -> tuple[type[CatalogoPlanosOnboarding], type[AssinaturasOnboarding]]:
    """Importa as implementações reais somente depois de a Task 9 criá-las."""
    from apps.assinaturas.catalogs import CatalogoPlanos
    from apps.assinaturas.subscriptions import Assinaturas

    return CatalogoPlanos, Assinaturas


def _configuracao_onboarding() -> tuple[str, str, str]:
    modo = settings.ASSINATURAS_ONBOARDING_MODO
    periodicidade = settings.ASSINATURAS_ONBOARDING_PERIODICIDADE
    if modo not in {"gratuito", "trial"}:
        raise ImproperlyConfigured("ASSINATURAS_ONBOARDING_MODO precisa ser 'gratuito' ou 'trial'.")
    if periodicidade not in {"mensal", "anual"}:
        raise ImproperlyConfigured("ASSINATURAS_ONBOARDING_PERIODICIDADE precisa ser 'mensal' ou 'anual'.")
    return modo, settings.ASSINATURAS_ONBOARDING_PLANO, periodicidade


class OrganizationOnboarding:
    """Coordena conta, tenant, propriedade, catálogo e contrato inicial."""

    @classmethod
    def criar(
        cls,
        *,
        usuario: Usuario,
        nome: str,
        slug: str,
        catalogo_planos: type[CatalogoPlanosOnboarding] | None = None,
        assinaturas: type[AssinaturasOnboarding] | None = None,
    ) -> Organizacao:
        if (catalogo_planos is None) != (assinaturas is None):
            raise TypeError("catalogo_planos e assinaturas precisam ser fornecidos juntos.")
        database_alias = usuario._state.db or "default"
        with transaction.atomic(using=database_alias):
            conta = Contas.validar_para_onboarding(usuario)
            if catalogo_planos is None or assinaturas is None:
                catalogo_planos, assinaturas = _carregar_colaboradores_comerciais()
            modo, codigo_plano, periodicidade = _configuracao_onboarding()
            organizacao = Organizacoes.criar(nome=nome, slug=slug, proprietario=conta)
            proprietario = Vinculos.criar_proprietario(organizacao, conta)
            from apps.workspaces.workspaces import Workspaces

            Workspaces.criar_inicial(organizacao=organizacao, proprietario=proprietario)
            versao_plano, preco_plano = catalogo_planos.obter_versao_inicial(
                codigo=codigo_plano,
                periodicidade=periodicidade,
                moeda="BRL",
            )
            with organizacao_atual_privilegiada(organizacao.pk):
                criar_assinatura = assinaturas.criar_gratuita if modo == "gratuito" else assinaturas.criar_trial
                criar_assinatura(
                    organizacao=organizacao,
                    versao_plano=versao_plano,
                    preco_plano=preco_plano,
                )
            return organizacao
