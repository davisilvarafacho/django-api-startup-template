"""Serviço de assinatura: espelho local da assinatura da organização e sua sincronização com o gateway."""

import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from django_checkouts.enums import CancellationTiming, ChangeTiming, CheckoutMode, ProrationBehavior
from django_checkouts.types import CatalogPrice, CheckoutCreate, CheckoutItem, Customer
from django_checkouts.types.subscriptions import ChangeSubscription, ReplacePrice

from apps.api.core.errors import APIError
from apps.billing.context import organization_scope
from apps.billing.errors import BillingErrorCode
from apps.billing.gateway import ReferenceMismatch, get_client
from apps.billing.members import MemberLimits
from apps.billing.models import Plan, Subscription, SubscriptionStatus

logger = logging.getLogger("apps.billing")


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

        Se a organização já está ligada a outra assinatura, a recém-chegada é uma cobrança
        duplicada: ela é cancelada já no gateway, e a assinatura original fica intacta. O
        cancelamento roda fora do lock e da transação, depois que elas foram liberadas. A chave
        de idempotência deriva do id da duplicada, então repetir não cancela duas vezes.

        O reembolso da duplicada não é automático: continua manual.

        Args:
            organization_id (int): Organização dona da assinatura.
            external_id (str): Identificador da assinatura no gateway.
            gateway_variant (str): Variante do gateway que emitiu a assinatura.

        Raises:
            CheckoutError: Falha ao cancelar a duplicada; a tarefa que chama registra o erro e
                não marca o evento como processado.
        """
        with organization_scope(organization_id), transaction.atomic():
            subscription = cls.get_current(organization_id, lock=True)
            if not subscription.external_id:
                subscription.external_id = external_id
                subscription.gateway_variant = gateway_variant
                subscription.save()
                return
            linked_id = subscription.external_id

        if linked_id == external_id:
            return
        logger.error(
            "Organização %s já tem a assinatura %s no gateway; a assinatura duplicada %s foi cancelada imediatamente. "
            "Se houve cobrança dela, o reembolso é manual.",
            organization_id,
            linked_id,
            external_id,
        )
        get_client().subscriptions.cancel(
            external_id,
            timing=CancellationTiming.IMMEDIATELY,
            idempotency_key=f"billing:duplicate:{external_id}",
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
    def subscribe(cls, organization_id: int, *, plan: Plan, idempotency_key: str, customer_email: str | None) -> str:
        """Cria o checkout de assinatura do plano pago e devolve a URL de pagamento.

        Só a organização sem assinatura no gateway pode assinar. Assinatura vinculada conta mesmo
        que o espelho mostre o plano gratuito (preço desconhecido ou plano excluído). O
        `reference_id` do checkout é o id da organização, para que a assinatura criada no gateway
        carregue o vínculo.

        Args:
            organization_id (int): Organização que assina.
            plan (Plan): Plano pago e ativo, com preço no gateway.
            idempotency_key (str): Chave de idempotência repassada ao gateway.
            customer_email (str | None): E-mail de faturamento, quando conhecido.

        Returns:
            str: URL do checkout hospedado pelo gateway.

        Raises:
            APIError: `billing.already_subscribed` (409) se já há assinatura vinculada ao gateway;
                `billing.plan_unavailable` (422) se o plano não é pago, ativo ou assinável.
            CheckoutError: Falha do gateway, traduzida pela view.
        """
        with organization_scope(organization_id):
            cls.ensure_free(organization_id)
            current = cls.get_current(organization_id)
            if current.external_id or not current.is_free:
                raise APIError(BillingErrorCode.ALREADY_SUBSCRIBED, status_code=409)
            cls._validate_assignable_plan(plan)

        checkout = get_client().checkouts.create(
            CheckoutCreate(
                mode=CheckoutMode.SUBSCRIPTION,
                items=(CheckoutItem(price=CatalogPrice(external_id=plan.gateway_price_id)),),
                success_url=settings.FRONTEND_BILLING_CHECKOUT_SUCCESS_URL,
                cancel_url=settings.FRONTEND_BILLING_CHECKOUT_CANCEL_URL,
                reference_id=str(organization_id),
                customer=Customer(email=customer_email) if customer_email else None,
            ),
            idempotency_key=idempotency_key,
        )
        return checkout.url

    @classmethod
    def change_plan(cls, organization_id: int, *, plan: Plan, idempotency_key: str) -> Subscription:
        """Troca o plano da assinatura paga.

        Rank igual ou maior é upgrade: troca imediata com fatura proporcional. Rank menor é
        downgrade: agendado para o próximo ciclo, desde que a organização caiba no limite do
        plano de destino. Alvo gratuito é cancelamento.

        Args:
            organization_id (int): Organização dona da assinatura.
            plan (Plan): Plano de destino.
            idempotency_key (str): Chave de idempotência repassada ao gateway.

        Returns:
            Subscription: O espelho depois da sincronização.

        Raises:
            APIError: `billing.not_subscribed` (409), `billing.same_plan` (422),
                `billing.plan_unavailable` (422), `billing.current_plan_unknown` (409) ou
                `billing.downgrade_blocked` (409).
            CheckoutError: Falha do gateway, traduzida pela view.
        """
        with organization_scope(organization_id):
            current = cls.get_current(organization_id)
            cls._require_paid(current)
            if current.plan_id == plan.pk:
                raise APIError(BillingErrorCode.SAME_PLAN, status_code=422)
            if current.is_free and not plan.is_free:
                # Assinatura vinculada cujo preço não está no catálogo: o espelho ficou no gratuito,
                # e o rank não diz se a troca é upgrade ou downgrade. Recusa a troca para plano pago;
                # cancelar (alvo gratuito) continua permitido.
                raise APIError(BillingErrorCode.CURRENT_PLAN_UNKNOWN, status_code=409)
            upgrade = plan.rank >= current.plan.rank
            if not plan.is_free:
                cls._validate_assignable_plan(plan)
                if not upgrade:
                    cls._block_downgrade_over_limit(organization_id, plan.member_limit)
            external_id = current.external_id

        if plan.is_free:
            return cls.cancel(organization_id, idempotency_key=idempotency_key)

        client = get_client()
        item_id = client.subscriptions.retrieve(external_id).items[0].external_id
        replace = ReplacePrice(item_id=item_id, price=CatalogPrice(external_id=plan.gateway_price_id))
        if upgrade:
            change = ChangeSubscription(
                changes=(replace,),
                timing=ChangeTiming.IMMEDIATELY,
                proration=ProrationBehavior.INVOICE_IMMEDIATELY,
            )
        else:
            change = ChangeSubscription(
                changes=(replace,),
                timing=ChangeTiming.NEXT_CYCLE,
                proration=ProrationBehavior.NONE,
            )
        client.subscriptions.change(external_id, change, idempotency_key=idempotency_key)

        with organization_scope(organization_id), transaction.atomic():
            Subscription.all_objects.filter(organizacao_id=organization_id).update(pending_plan=None if upgrade else plan)
        return cls.sync(organization_id)

    @classmethod
    def cancel(cls, organization_id: int, *, idempotency_key: str) -> Subscription:
        """Agenda o cancelamento para o fim do período, desde que a organização caiba no plano gratuito.

        Args:
            organization_id (int): Organização dona da assinatura.
            idempotency_key (str): Chave de idempotência repassada ao gateway.

        Returns:
            Subscription: O espelho depois da sincronização.

        Raises:
            APIError: `billing.not_subscribed` (409) ou `billing.downgrade_blocked` (409).
            CheckoutError: Falha do gateway, traduzida pela view.
        """
        with organization_scope(organization_id):
            current = cls.get_current(organization_id)
            cls._require_paid(current)
            cls._block_downgrade_over_limit(organization_id, Plan.get_free().member_limit)
            external_id = current.external_id

        get_client().subscriptions.cancel(external_id, timing=CancellationTiming.PERIOD_END, idempotency_key=idempotency_key)
        return cls.sync(organization_id)

    @classmethod
    def cancel_immediately(cls, organization_id: int) -> Subscription | None:
        """Cancela já a assinatura de uma organização encerrada e sincroniza o espelho.

        Basta o vínculo com o gateway: a assinatura pode estar no gratuito pelo espelho (preço
        desconhecido) e ainda cobrar. Sem a barreira de limite de membros do cancelamento comum:
        a organização já foi encerrada.

        Args:
            organization_id (int): Organização encerrada.

        Returns:
            Subscription | None: O espelho depois da sincronização, ou `None` se a organização não
            tem assinatura vinculada ao gateway.

        Raises:
            CheckoutError: Falha do gateway; a tarefa que chama registra o erro.
        """
        with organization_scope(organization_id):
            current = Subscription.all_objects.filter(organizacao_id=organization_id).first()
            if current is None or not current.external_id:
                return None
            external_id = current.external_id

        cls.cancel_remote_for_closed(organization_id, external_id=external_id)
        return cls.sync(organization_id)

    @staticmethod
    def cancel_remote_for_closed(organization_id: int, *, external_id: str) -> None:
        """Cancela já, no gateway, a assinatura remota de uma organização encerrada.

        A chave de idempotência é fixa por organização (`billing:close:<id>`), então repetir não
        cancela duas vezes. Não grava o espelho: quem chama decide se sincroniza.

        Args:
            organization_id (int): Organização encerrada.
            external_id (str): Identificador da assinatura remota a cancelar.

        Raises:
            CheckoutError: Falha do gateway.
        """
        get_client().subscriptions.cancel(
            external_id,
            timing=CancellationTiming.IMMEDIATELY,
            idempotency_key=f"billing:close:{organization_id}",
        )

    @classmethod
    def resume(cls, organization_id: int, *, idempotency_key: str) -> Subscription:
        """Desfaz o cancelamento agendado para o fim do período.

        Args:
            organization_id (int): Organização dona da assinatura.
            idempotency_key (str): Chave de idempotência repassada ao gateway.

        Returns:
            Subscription: O espelho depois da sincronização.

        Raises:
            APIError: `billing.not_subscribed` (409) se não há assinatura paga.
            CheckoutError: Falha do gateway, traduzida pela view.
        """
        with organization_scope(organization_id):
            current = cls.get_current(organization_id)
            cls._require_paid(current)
            external_id = current.external_id

        get_client().subscriptions.resume(external_id, idempotency_key=idempotency_key)
        return cls.sync(organization_id)

    @staticmethod
    def _require_paid(subscription: Subscription) -> None:
        """Exige vínculo com a assinatura no gateway.

        Só o vínculo importa, não o plano do espelho: com preço desconhecido, o espelho mostra o
        gratuito e a assinatura continua cobrando. Assim cancelar e desfazer chegam ao gateway,
        e a troca de plano é recusada à parte (`CURRENT_PLAN_UNKNOWN`).
        """
        if not subscription.external_id:
            raise APIError(BillingErrorCode.NOT_SUBSCRIBED, status_code=409)

    @staticmethod
    def _validate_assignable_plan(plan: Plan) -> None:
        """Só plano pago, ativo e com preço no gateway pode ser assinado ou trocado para."""
        if plan.is_free or not plan.is_active or not plan.gateway_price_id:
            raise APIError(BillingErrorCode.PLAN_UNAVAILABLE, status_code=422)

    @staticmethod
    def _block_downgrade_over_limit(organization_id: int, target_limit: int) -> None:
        """Bloqueia a redução de plano enquanto houver mais vínculos ativos do que o limite do destino."""
        active = MemberLimits.active_count(organization_id)
        if active > target_limit:
            raise APIError(BillingErrorCode.DOWNGRADE_BLOCKED, status_code=409, context={"excess": active - target_limit})

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
