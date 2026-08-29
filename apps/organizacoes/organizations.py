"""Casos de uso do ciclo de vida de organizações globais."""

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from django.db import transaction
from django.utils import timezone

from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Convite, Organizacao, Vinculo

if TYPE_CHECKING:
    from apps.usuarios.models import Usuario


@dataclass(frozen=True)
class TermoEncerramentoImediato:
    """Contrato gratuito: a efetivação ocorre na mesma transação do pedido."""


@dataclass(frozen=True)
class TermoEncerramentoAgendado:
    """Contrato pago: preserva acesso até o fim do período contratado."""

    agendado_para: datetime


type TermoEncerramento = TermoEncerramentoImediato | TermoEncerramentoAgendado


@dataclass(frozen=True)
class EncerramentoEfetivado:
    """A solicitação encerrou a organização nesta mesma transação."""


@dataclass(frozen=True)
class EncerramentoAgendado:
    """A solicitação registrou o fim do período contratado."""

    agendado_para: datetime


@dataclass(frozen=True)
class EncerramentoSemAlteracao:
    """O estado desejado já existia; a data distingue um pedido pendente."""

    agendado_para: datetime | None


type ResultadoSolicitacaoEncerramento = EncerramentoEfetivado | EncerramentoAgendado | EncerramentoSemAlteracao


class AssinaturasEncerramento(Protocol):
    """Seam que a Task 9 implementará sobre o contrato tenantizado real."""

    @classmethod
    def encerrar(cls, organizacao: Organizacao, *, encerrada_em: datetime) -> None: ...


class AssinaturasCicloOrganizacao(AssinaturasEncerramento, Protocol):
    """Planejamento e efetivação que a Task 9 ligará ao contrato real."""

    @classmethod
    def obter_termo_encerramento(cls, organizacao: Organizacao) -> TermoEncerramento: ...


class Organizacoes:
    """Criação e encerramento sem importar o núcleo comercial no carregamento."""

    @classmethod
    def criar(cls, *, nome: str, slug: str, proprietario: "Usuario") -> Organizacao:
        """Cria a raiz global copiando o e-mail verificado do proprietário."""
        return Organizacao.objects.create(nome=nome, slug=slug, email_faturamento=proprietario.email)

    @classmethod
    def solicitar_encerramento(
        cls,
        organizacao: Organizacao,
        termo: TermoEncerramento,
        *,
        assinaturas: type[AssinaturasEncerramento],
        ator=None,
        agora=None,
    ) -> ResultadoSolicitacaoEncerramento:
        """Registra o pedido e efetiva imediatamente somente o termo gratuito."""
        if not isinstance(termo, TermoEncerramentoImediato | TermoEncerramentoAgendado):
            raise TypeError("termo precisa implementar TermoEncerramento.")
        agora = agora or timezone.now()
        database_alias = organizacao._state.db or "default"
        with transaction.atomic(using=database_alias):
            atual = Organizacao.all_objects.using(database_alias).select_for_update().get(pk=organizacao.pk)
            if atual.is_deleted or not atual.is_active or atual.encerramento_solicitado_em is not None:
                return EncerramentoSemAlteracao(agendado_para=atual.encerramento_agendado_para)

            atual.encerramento_solicitado_em = agora
            atual.encerramento_agendado_para = termo.agendado_para if isinstance(termo, TermoEncerramentoAgendado) else None
            atual.save(
                using=database_alias,
                update_fields=["encerramento_solicitado_em", "encerramento_agendado_para"],
            )

            if isinstance(termo, TermoEncerramentoAgendado):
                return EncerramentoAgendado(agendado_para=termo.agendado_para)
            cls._efetivar_bloqueada(
                atual,
                assinaturas=assinaturas,
                ator=ator,
                agora=agora,
                using=database_alias,
            )
            return EncerramentoEfetivado()

    @classmethod
    def cancelar_encerramento(cls, organizacao: Organizacao) -> bool:
        """Cancela um pedido ainda não efetivado sem reabrir organização."""
        database_alias = organizacao._state.db or "default"
        with transaction.atomic(using=database_alias):
            atual = Organizacao.all_objects.using(database_alias).select_for_update().get(pk=organizacao.pk)
            if atual.is_deleted or not atual.is_active or atual.encerramento_solicitado_em is None:
                return False
            atual.encerramento_solicitado_em = None
            atual.encerramento_agendado_para = None
            atual.save(
                using=database_alias,
                update_fields=["encerramento_solicitado_em", "encerramento_agendado_para"],
            )
            return True

    @classmethod
    def efetivar_encerramento(
        cls,
        organizacao_id: int,
        *,
        assinaturas: type[AssinaturasEncerramento],
        ator=None,
        agora=None,
        using="default",
    ) -> bool:
        """Efetiva uma organização vencida uma única vez sob lock de linha."""
        agora = agora or timezone.now()
        with transaction.atomic(using=using):
            organizacao = Organizacao.all_objects.using(using).select_for_update().get(pk=organizacao_id)
            if (
                organizacao.is_deleted
                or not organizacao.is_active
                or organizacao.encerramento_solicitado_em is None
                or organizacao.encerramento_agendado_para is None
                or organizacao.encerramento_agendado_para > agora
            ):
                return False
            return cls._efetivar_bloqueada(
                organizacao,
                assinaturas=assinaturas,
                ator=ator,
                agora=agora,
                using=using,
            )

    @classmethod
    def efetivar_encerramentos_vencidos(
        cls,
        *,
        assinaturas: type[AssinaturasEncerramento],
        batch_size: int,
        agora=None,
        using="default",
    ) -> int:
        """Processa um lote global; cada tenant mantém sua própria transação."""
        if batch_size < 1:
            raise ValueError("batch_size precisa ser positivo.")
        agora = agora or timezone.now()
        organizacao_ids = list(
            Organizacao.objects.using(using)
            .filter(
                encerramento_solicitado_em__isnull=False,
                encerramento_agendado_para__lte=agora,
            )
            .order_by("pk")
            .values_list("pk", flat=True)[:batch_size]
        )
        return sum(
            cls.efetivar_encerramento(
                organizacao_id,
                assinaturas=assinaturas,
                agora=agora,
                using=using,
            )
            for organizacao_id in organizacao_ids
        )

    @classmethod
    def _efetivar_bloqueada(
        cls,
        organizacao: Organizacao,
        *,
        assinaturas: type[AssinaturasEncerramento],
        ator,
        agora,
        using: str,
    ) -> bool:
        """Aplica efeitos sob o lock de ``Organizacao`` já adquirido."""
        from apps.api.autenticacao.services import revoke_organization_api_keys

        with organizacao_atual_privilegiada(organizacao.pk):
            assinaturas.encerrar(organizacao, encerrada_em=agora)

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
        return True
