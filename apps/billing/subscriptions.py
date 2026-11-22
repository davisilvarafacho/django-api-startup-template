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
            assinatura, _ = Subscription.all_objects.get_or_create(
                organizacao_id=organization_id,
                defaults={"plan": Plan.get_free(), "status": SubscriptionStatus.ACTIVE},
            )
        return assinatura

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
            consulta = Subscription.all_objects.select_related("plan", "pending_plan")
            if lock:
                consulta = consulta.select_for_update(of=("self",))
            return consulta.get(organizacao_id=organization_id)

    @classmethod
    def link_external(cls, organization_id: int, *, external_id: str, gateway_variant: str) -> None:
        """Liga o espelho à assinatura remota, sem sobrescrever um vínculo já existente.

        Args:
            organization_id (int): Organização dona da assinatura.
            external_id (str): Identificador da assinatura no gateway.
            gateway_variant (str): Variante do gateway que emitiu a assinatura.
        """
        with organization_scope(organization_id), transaction.atomic():
            assinatura = cls.get_current(organization_id, lock=True)
            if not assinatura.external_id:
                assinatura.external_id = external_id
                assinatura.gateway_variant = gateway_variant
                assinatura.save()
            elif assinatura.external_id != external_id:
                logger.error(
                    "Assinatura %s já vinculada a outro ID no gateway; o vínculo %s foi ignorado.",
                    assinatura.pk,
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
            assinatura = cls.get_current(organization_id, lock=True)
            if assinatura.external_id:
                client = get_client()
                remota = client.subscriptions.retrieve(assinatura.external_id)
                cls._espelhar(assinatura, remota, organization_id, client)
            MemberLimits.apply(organization_id)
        return assinatura

    @classmethod
    def _espelhar(cls, assinatura: Subscription, remota, organization_id: int, client) -> None:
        """Copia o estado remoto para o espelho, tratando cancelamento por falta de pagamento e encerramento."""
        if remota.reference_id != str(organization_id):
            raise ReferenceMismatch(f"A assinatura {remota.external_id} não pertence à organização {organization_id}.")

        status = SubscriptionStatus(remota.status.value)
        agora = timezone.now()

        if status == SubscriptionStatus.UNPAID:
            client.subscriptions.cancel(
                assinatura.external_id,
                timing=CancellationTiming.IMMEDIATELY,
                idempotency_key=f"billing:abandon:{assinatura.external_id}",
            )

        if status in Subscription.TERMINAL_STATUSES | Subscription.ABANDONED_STATUSES:
            cls._voltar_ao_gratuito(assinatura, agora)
        else:
            cls._aplicar_estado(assinatura, remota, status, agora)
        assinatura.save()

    @staticmethod
    def _aplicar_estado(assinatura: Subscription, remota, status: SubscriptionStatus, agora) -> None:
        """Grava status, plano e datas da assinatura remota ativa, em teste, atrasada, pausada ou incompleta."""
        preco = remota.items[0].price_id if remota.items else None
        plano = Plan.objects.filter(gateway_price_id=preco).first() if preco else None
        if plano is None:
            logger.error("Preço %s sem plano correspondente; o plano atual foi mantido.", preco)
        else:
            assinatura.plan = plano

        assinatura.status = status
        assinatura.current_period_end = remota.current_period_end
        assinatura.cancel_at = remota.cancel_at
        assinatura.customer_id = remota.customer_id
        assinatura.synced_at = agora
        if assinatura.pending_plan_id == assinatura.plan_id:
            assinatura.pending_plan = None

    @staticmethod
    def _voltar_ao_gratuito(assinatura: Subscription, agora) -> None:
        """Devolve a linha ao plano gratuito, sem vínculo com a assinatura remota encerrada."""
        assinatura.plan = Plan.get_free()
        assinatura.status = SubscriptionStatus.ACTIVE
        assinatura.external_id = None
        assinatura.gateway_variant = None
        assinatura.customer_id = None
        assinatura.current_period_end = None
        assinatura.cancel_at = None
        assinatura.pending_plan = None
        assinatura.synced_at = agora
