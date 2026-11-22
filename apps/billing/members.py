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
            subscription = Subscription.objects.filter(organizacao_id=organization_id).first()
            if subscription is None:
                return Plan.get_free().member_limit
            return subscription.member_limit

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
        limit = cls.limit_for(organization_id)
        occupied = cls.active_count(organization_id, using=using)
        if count_pending_invites:
            occupied += cls._pending_invites(organization_id, using=using).count()
        if occupied >= limit:
            raise APIError(BillingErrorCode.MEMBER_LIMIT_REACHED, status_code=409, context={"limit": limit})

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
        current_time = now or timezone.now()
        with transaction.atomic(using=using):
            organization = Organizacao.all_objects.using(using).select_for_update().get(pk=organization_id)
            if not organization.is_active or organization.is_deleted:
                return

            limit = cls.limit_for(organization_id)
            memberships = list(
                Vinculo.all_objects.using(using).select_for_update().filter(organizacao_id=organization_id, is_deleted=False).order_by("pk")
            )
            active = [membership for membership in memberships if membership.is_active]

            excess = len(active) - limit
            if excess > 0:
                candidates = sorted(
                    (membership for membership in active if membership.papel != Papel.PROPRIETARIO),
                    key=lambda membership: (membership.created_at, membership.pk),
                    reverse=True,
                )
                for membership in candidates[:excess]:
                    membership.is_active = False
                    membership.plan_limit_suspended_at = current_time
                    membership.save(update_fields=["is_active", "plan_limit_suspended_at"])
                return

            free_slots = limit - len(active)
            suspended = sorted(
                (membership for membership in memberships if not membership.is_active and membership.plan_limit_suspended_at is not None),
                key=lambda membership: (membership.plan_limit_suspended_at, membership.created_at, membership.pk),
            )
            for membership in suspended[:free_slots]:
                membership.is_active = True
                membership.plan_limit_suspended_at = None
                membership.save(update_fields=["is_active", "plan_limit_suspended_at"])

    @staticmethod
    def _pending_invites(organization_id: int, *, using: str = "default"):
        """Convites que ainda podem ser aceitos: espelha `Convite.pendente` numa consulta."""
        return Convite.objects.using(using).filter(
            organizacao_id=organization_id,
            is_active=True,
            aceito_em__isnull=True,
            expira_em__gt=timezone.now(),
        )
