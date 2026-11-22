"""Serviço de assinatura: espelho local da assinatura da organização e sua sincronização com o gateway."""

import logging

from django.db import transaction
from django.utils import timezone

from django_checkouts.enums import CancellationTiming

from apps.billing.context import organization_scope
from apps.billing.gateway import get_client
from apps.billing.members import MemberLimits
from apps.billing.models import Plan, Subscription, SubscriptionStatus

logger = logging.getLogger("apps.billing")


class ReferenceMismatch(Exception):
    """A assinatura remota aponta para outra organização; nada é gravado."""


class Subscriptions:
    """Operações sobre o espelho local da assinatura. `sync` é a única escrita vinda do gateway."""

    @classmethod
    def ensure_free(cls, organization_id: int) -> Subscription:
        """Cria a assinatura gratuita da organização, se ainda não existir.

        Args:
            organization_id (int): Organização dona da assinatura.

        Returns:
            Subscription: A assinatura existente ou a recém-criada.
        """
        with organization_scope(organization_id):
            subscription, _ = Subscription.all_objects.get_or_create(
                organizacao_id=organization_id,
                defaults={"plan": Plan.get_free(), "status": SubscriptionStatus.ACTIVE},
            )
        return subscription

    @classmethod
    def get_current(cls, organization_id: int, *, lock: bool = False) -> Subscription:
        """Devolve o espelho da organização; com `lock`, trava a linha até o fim da transação.

        Args:
            organization_id (int): Organização dona da assinatura.
            lock (bool): Quando True, aplica `select_for_update`; exige transação aberta.

        Returns:
            Subscription: A assinatura espelhada da organização.
        """
        with organization_scope(organization_id):
            query = Subscription.all_objects.select_related("plan", "pending_plan")
            if lock:
                query = query.select_for_update(of=("self",))
            return query.get(organizacao_id=organization_id)

    @classmethod
    def link_external(cls, organization_id: int, *, external_id: str, gateway_variant: str) -> None:
        """Liga o espelho à assinatura remota, sem sobrescrever um vínculo já existente.

        Args:
            organization_id (int): Organização dona da assinatura.
            external_id (str): Identificador da assinatura no gateway.
            gateway_variant (str): Variante do gateway que emitiu a assinatura.
        """
        with organization_scope(organization_id), transaction.atomic():
            subscription = cls.get_current(organization_id, lock=True)
            if not subscription.external_id:
                subscription.external_id = external_id
                subscription.gateway_variant = gateway_variant
                subscription.save()
            elif subscription.external_id != external_id:
                logger.error(
                    "Assinatura %s já vinculada a outro ID no gateway; o vínculo %s foi ignorado.",
                    subscription.pk,
                    external_id,
                )

    @classmethod
    def sync(cls, organization_id: int) -> Subscription:
        """Atualiza o espelho a partir do gateway e alinha o limite de membros.

        A linha local é travada antes da leitura remota: duas sincronizações da mesma
        organização se serializam e a segunda grava o estado mais novo.

        Args:
            organization_id (int): Organização a sincronizar.

        Returns:
            Subscription: A assinatura espelhada, já atualizada.

        Raises:
            ReferenceMismatch: Se a assinatura remota aponta para outra organização.
        """
        with organization_scope(organization_id), transaction.atomic():
            cls.ensure_free(organization_id)
            subscription = cls.get_current(organization_id, lock=True)
            if subscription.external_id:
                client = get_client()
                remote = client.subscriptions.retrieve(subscription.external_id)
                cls._mirror_remote(subscription, remote, organization_id, client)
            MemberLimits.apply(organization_id)
        return subscription

    @classmethod
    def _mirror_remote(cls, subscription: Subscription, remote, organization_id: int, client) -> None:
        """Copia o estado remoto para o espelho, tratando cancelamento por falta de pagamento e encerramento."""
        if remote.reference_id != str(organization_id):
            raise ReferenceMismatch(f"A assinatura {remote.external_id} não pertence à organização {organization_id}.")

        status = SubscriptionStatus(remote.status.value)
        now = timezone.now()

        if status == SubscriptionStatus.UNPAID:
            client.subscriptions.cancel(
                subscription.external_id,
                timing=CancellationTiming.IMMEDIATELY,
                idempotency_key=f"billing:abandon:{subscription.external_id}",
            )

        if status in Subscription.TERMINAL_STATUSES | Subscription.ABANDONED_STATUSES:
            cls._reset_to_free(subscription, now)
        else:
            cls._apply_state(subscription, remote, status, now)
        subscription.save()

    @staticmethod
    def _apply_state(subscription: Subscription, remote, status: SubscriptionStatus, now) -> None:
        """Grava status, plano e datas da assinatura remota ativa, em teste, atrasada, pausada ou incompleta."""
        price_id = remote.items[0].price_id if remote.items else None
        plan = Plan.objects.filter(gateway_price_id=price_id).first() if price_id else None
        if plan is None:
            logger.error("Preço %s sem plano correspondente; o plano atual foi mantido.", price_id)
        else:
            subscription.plan = plan

        subscription.status = status
        subscription.current_period_end = remote.current_period_end
        subscription.cancel_at = remote.cancel_at
        subscription.customer_id = remote.customer_id
        subscription.synced_at = now
        if subscription.pending_plan_id == subscription.plan_id:
            subscription.pending_plan = None

    @staticmethod
    def _reset_to_free(subscription: Subscription, now) -> None:
        """Devolve a linha ao plano gratuito, sem vínculo com a assinatura remota encerrada."""
        subscription.plan = Plan.get_free()
        subscription.status = SubscriptionStatus.ACTIVE
        subscription.external_id = None
        subscription.gateway_variant = None
        subscription.customer_id = None
        subscription.current_period_end = None
        subscription.cancel_at = None
        subscription.pending_plan = None
        subscription.synced_at = now
