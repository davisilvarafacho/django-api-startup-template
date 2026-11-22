"""Limite de membros pelo plano: contagem, bloqueio de entrada e suspensão/reativação por excesso."""

from datetime import datetime

from django.db import transaction
from django.utils import timezone

from apps.api.core.errors import APIError
from apps.billing.context import organization_scope
from apps.billing.errors import BillingErrorCode
from apps.billing.models import Plan, Subscription
from apps.organizacoes.models import Convite, Organizacao, Papel, Vinculo


class MemberLimits:
    """Regras do limite de membros da organização, derivadas da assinatura."""

    @classmethod
    def active_count(cls, organization_id: int, *, using: str = "default") -> int:
        """Conta os vínculos ativos e não excluídos da organização."""
        return Vinculo.all_objects.using(using).filter(organizacao_id=organization_id, is_active=True, is_deleted=False).count()

    @classmethod
    def limit_for(cls, organization_id: int) -> int:
        """Retorna o limite de membros da organização; sem assinatura, o do plano gratuito."""
        with organization_scope(organization_id):
            assinatura = Subscription.objects.filter(organizacao_id=organization_id).first()
            if assinatura is None:
                return Plan.get_free().member_limit
            return assinatura.member_limit

    @classmethod
    def ensure_capacity(
        cls,
        organization_id: int,
        *,
        count_pending_invites: bool = False,
        using: str = "default",
    ) -> None:
        """Bloqueia a entrada de mais um membro quando a organização já está no limite.

        Args:
            organization_id (int): Organização que vai receber o novo membro.
            count_pending_invites (bool): Quando True, convites pendentes também ocupam vaga.
            using (str): Alias do banco.

        Raises:
            APIError: `billing.member_limit_reached` (409) quando não há vaga.
        """
        limite = cls.limit_for(organization_id)
        ocupados = cls.active_count(organization_id, using=using)
        if count_pending_invites:
            ocupados += cls._convites_pendentes(organization_id, using=using).count()
        if ocupados >= limite:
            raise APIError(BillingErrorCode.MEMBER_LIMIT_REACHED, status_code=409, context={"limit": limite})

    @classmethod
    def apply(
        cls,
        organization_id: int,
        *,
        now: datetime | None = None,
        using: str = "default",
    ) -> None:
        """Alinha os vínculos ativos ao limite atual, suspendendo ou reativando quem a cobrança mexeu.

        Acima do limite, suspende os ativos mais recentes (exceto proprietários). Abaixo do
        limite, reativa só os suspensos pela cobrança, do mais antigo para o mais novo. Vínculos
        desativados manualmente, sem a marca `plan_limit_suspended_at`, nunca são reativados aqui.

        Args:
            organization_id (int): Organização a alinhar.
            now (datetime | None): Momento gravado nos vínculos suspensos; padrão, agora.
            using (str): Alias do banco.
        """
        agora = now or timezone.now()
        with transaction.atomic(using=using):
            organizacao = Organizacao.all_objects.using(using).select_for_update().get(pk=organization_id)
            if not organizacao.is_active or organizacao.is_deleted:
                return

            limite = cls.limit_for(organization_id)
            vinculos = list(
                Vinculo.all_objects.using(using).select_for_update().filter(organizacao_id=organization_id, is_deleted=False).order_by("pk")
            )
            ativos = [vinculo for vinculo in vinculos if vinculo.is_active]

            excedente = len(ativos) - limite
            if excedente > 0:
                candidatos = sorted(
                    (vinculo for vinculo in ativos if vinculo.papel != Papel.PROPRIETARIO),
                    key=lambda vinculo: (vinculo.created_at, vinculo.pk),
                    reverse=True,
                )
                for vinculo in candidatos[:excedente]:
                    vinculo.is_active = False
                    vinculo.plan_limit_suspended_at = agora
                    vinculo.save(update_fields=["is_active", "plan_limit_suspended_at"])
                return

            vagas = limite - len(ativos)
            suspensos = sorted(
                (vinculo for vinculo in vinculos if not vinculo.is_active and vinculo.plan_limit_suspended_at is not None),
                key=lambda vinculo: (vinculo.plan_limit_suspended_at, vinculo.created_at, vinculo.pk),
            )
            for vinculo in suspensos[:vagas]:
                vinculo.is_active = True
                vinculo.plan_limit_suspended_at = None
                vinculo.save(update_fields=["is_active", "plan_limit_suspended_at"])

    @staticmethod
    def _convites_pendentes(organization_id: int, *, using: str = "default"):
        """Convites que ainda podem ser aceitos: espelha `Convite.pendente` numa consulta."""
        return Convite.objects.using(using).filter(
            organizacao_id=organization_id,
            is_active=True,
            aceito_em__isnull=True,
            expira_em__gt=timezone.now(),
        )
