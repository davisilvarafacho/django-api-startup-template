"""Casos de uso do ciclo de vida de organizações globais."""

from typing import TYPE_CHECKING

from django.db import transaction
from django.utils import timezone

from apps.api.autenticacao.services import lock_user_accounts
from apps.organizacoes.memberships import Vinculos
from apps.organizacoes.models import Convite, Organizacao, Papel, Vinculo
from apps.usuarios.policies import exigir_email_verificado

if TYPE_CHECKING:
    from apps.usuarios.models import Usuario


class Organizacoes:
    """Criação e encerramento de organizações."""

    @classmethod
    def criar(cls, *, nome: str, slug: str, proprietario: "Usuario") -> Organizacao:
        """Cria a raiz global copiando o e-mail verificado do proprietário."""
        return Organizacao.objects.create(nome=nome, slug=slug, email_faturamento=proprietario.email)

    @classmethod
    def atualizar_email_faturamento(
        cls,
        organizacao: Organizacao,
        *,
        email_faturamento: str | None,
        ator: "Usuario",
    ) -> Organizacao:
        """Atualiza o endereço financeiro sob lock da raiz da organização."""
        database_alias = organizacao._state.db or "default"
        with transaction.atomic(using=database_alias):
            ator = lock_user_accounts((ator,), using=database_alias)[ator.pk]
            exigir_email_verificado(ator)
            atual = Organizacao.all_objects.using(database_alias).select_for_update().get(pk=organizacao.pk)
            Vinculos.bloquear_e_exigir_papel(
                organizacao=atual,
                usuario=ator,
                papel_minimo=Papel.ADMINISTRADOR,
                using=database_alias,
            )
            atual.email_faturamento = email_faturamento
            atual.save(using=database_alias, update_fields=["email_faturamento", "last_modified_at"])
            return atual

    @classmethod
    def solicitar_encerramento(cls, organizacao: Organizacao, *, ator=None, agora=None) -> bool:
        """Encerra a organização imediatamente, numa única seção crítica.

        Returns:
            ``True`` se a organização foi encerrada nesta chamada; ``False`` se
            já estava encerrada ou inativa.
        """
        agora = agora or timezone.now()
        database_alias = organizacao._state.db or "default"
        with transaction.atomic(using=database_alias):
            atores_bloqueados = lock_user_accounts((ator,), using=database_alias)
            if ator is not None:
                ator = atores_bloqueados[ator.pk]
                exigir_email_verificado(ator)
            atual = Organizacao.all_objects.using(database_alias).select_for_update().get(pk=organizacao.pk)
            if atual.is_deleted or not atual.is_active or atual.encerramento_solicitado_em is not None:
                return False
            if ator is not None:
                Vinculos.bloquear_e_exigir_papel(
                    organizacao=atual,
                    usuario=ator,
                    papel_minimo=Papel.PROPRIETARIO,
                    using=database_alias,
                )
            atual.encerramento_solicitado_em = agora
            atual.save(using=database_alias, update_fields=["encerramento_solicitado_em"])
            cls._efetivar_bloqueada(atual, ator=ator, agora=agora, using=database_alias)
            return True

    @classmethod
    def _efetivar_bloqueada(cls, organizacao: Organizacao, *, ator, agora, using: str) -> None:
        """Aplica efeitos sob o lock de ``Organizacao`` já adquirido."""
        from apps.api.autenticacao.services import revoke_organization_api_keys

        vinculos = list(Vinculo.objects.using(using).select_for_update().filter(organizacao=organizacao, is_active=True).order_by("pk"))
        for vinculo in vinculos:
            vinculo.is_active = False
            vinculo.save(using=using, update_fields=["is_active"])

        revoke_organization_api_keys(organizacao, actor=ator, revoked_at=agora, using=using)

        convites = list(
            Convite.all_objects.using(using)
            .select_for_update()
            .filter(organizacao=organizacao, is_deleted=False, aceito_em__isnull=True)
            .order_by("pk")
        )
        for convite in convites:
            convite.delete(using=using)

        organizacao.is_active = False
        organizacao.save(using=using, update_fields=["is_active"])
        organizacao.delete(using=using)
