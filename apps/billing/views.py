"""Endpoints da cobrança: catálogo de planos, assinatura da organização e suas operações.

A assinatura é dado de tenant, então as rotas dela exigem `X-Organization` e sessão
humana. As mutações chamam o gateway, por isso ficam fora da transação da request
(`@io_externo_sem_transacao`): a leitura e a validação rodam dentro de
`organization_scope`, e a chamada ao gateway roda fora dele.
"""

from django.conf import settings
from django.db import transaction

from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from django_checkouts.exceptions import WebhookVerificationError

from apps.api.autenticacao.permissions import TokenScopePermission
from apps.api.core.errors import APIError, CoreErrorCode
from apps.api.core.route_markers import io_externo_sem_transacao, no_tenancy, public
from apps.billing.context import organization_scope
from apps.billing.docs import (
    document_plan_list,
    document_subscription_cancel,
    document_subscription_change,
    document_subscription_checkout,
    document_subscription_get,
    document_webhook,
)
from apps.billing.errors import BillingErrorCode
from apps.billing.events import record_event
from apps.billing.filters import PlanFilter
from apps.billing.gateway import get_client, translate_gateway_errors
from apps.billing.members import MemberLimits
from apps.billing.models import Plan
from apps.billing.serializers import PlanSelectionSerializer, PlanSerpySerializer, SubscriptionSerpySerializer
from apps.billing.subscriptions import Subscriptions
from apps.billing.tasks import process_billing_event
from apps.organizacoes.models import Papel
from apps.organizacoes.permissions import PapelMinimoPermission, TenantPermission

IDEMPOTENCY_HEADER = "Idempotency-Key"


def _idempotency_key(request) -> str:
    """Lê o `Idempotency-Key` da request; sem ele a mutação não roda."""
    key = request.headers.get(IDEMPOTENCY_HEADER, "").strip()
    if not key:
        raise APIError(BillingErrorCode.IDEMPOTENCY_KEY_REQUIRED, status_code=422, field=IDEMPOTENCY_HEADER)
    return key


def _plan_by_slug(request) -> Plan:
    """Resolve o plano escolhido no corpo; inexistente ou inativo não está disponível."""
    serializer = PlanSelectionSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    plan = Plan.objects.filter(slug=serializer.validated_data["plan"], is_active=True).first()
    if plan is None:
        raise APIError(BillingErrorCode.PLAN_UNAVAILABLE, status_code=422, field="plan")
    return plan


def _representation(organization_id: int, subscription) -> dict:
    """Representação da assinatura com o uso de membros da organização."""
    with organization_scope(organization_id):
        active = MemberLimits.active_count(organization_id)
    data = SubscriptionSerpySerializer(subscription).data
    data["members"] = {"active": active, "limit": subscription.member_limit}
    return data


class SubscriptionViewMixin:
    """Permissões comuns às rotas da assinatura: tenant obrigatório e sessão humana."""

    permission_classes = [IsAuthenticated, TenantPermission, TokenScopePermission, PapelMinimoPermission]
    session_only = True


@document_plan_list
@no_tenancy
class PlanListView(ListAPIView):
    """Catálogo de planos ativos, em ordem de rank. Exige sessão humana, mas não organização."""

    permission_classes = [IsAuthenticated, TokenScopePermission]
    # Sessão humana apenas: `session_only` recusa API keys na `TokenScopePermission` e o
    # middleware a deixa passar sem tenant nesta rota livre de organização.
    session_only = True
    serializer_class = PlanSerpySerializer
    filterset_class = PlanFilter
    queryset = Plan.objects.filter(is_active=True).order_by("rank")


@document_subscription_get
class SubscriptionView(SubscriptionViewMixin, APIView):
    """Assinatura da organização do header, com uso de membros. Papel mínimo: administrador."""

    papel_minimo = Papel.ADMINISTRADOR

    def get(self, request):
        organization_id = request.tenant.organization_id
        Subscriptions.ensure_free(organization_id)
        subscription = Subscriptions.get_current(organization_id)
        return Response(_representation(organization_id, subscription))


@document_subscription_checkout
@io_externo_sem_transacao
class SubscriptionCheckoutView(SubscriptionViewMixin, APIView):
    """Inicia a assinatura de um plano pago e devolve a URL do checkout. Papel mínimo: proprietário."""

    papel_minimo = Papel.PROPRIETARIO

    def post(self, request):
        key = _idempotency_key(request)
        plan = _plan_by_slug(request)
        with translate_gateway_errors():
            url = Subscriptions.subscribe(
                request.tenant.organization_id,
                plan=plan,
                idempotency_key=key,
                customer_email=request.tenant.organizacao.email_faturamento or None,
            )
        return Response({"url": url}, status=status.HTTP_201_CREATED)


@document_subscription_change
@io_externo_sem_transacao
class SubscriptionChangeView(SubscriptionViewMixin, APIView):
    """Troca o plano da assinatura paga: upgrade imediato ou downgrade no próximo ciclo. Papel mínimo: proprietário."""

    papel_minimo = Papel.PROPRIETARIO

    def post(self, request):
        key = _idempotency_key(request)
        plan = _plan_by_slug(request)
        organization_id = request.tenant.organization_id
        with translate_gateway_errors():
            subscription = Subscriptions.change_plan(organization_id, plan=plan, idempotency_key=key)
        return Response(_representation(organization_id, subscription))


@document_webhook
@public
class WebhookView(APIView):
    """Recebe eventos do gateway: verifica a assinatura, registra o evento e enfileira o processamento após o commit.

    Pública e sem token ou organização: a autenticidade vem da assinatura do gateway. Nenhuma
    outra lógica roda na request, e o processamento é adiado para depois do commit do registro.
    Não é marcada `@io_externo_sem_transacao`: a verificação é local e a única escrita é a do
    registro, que deve ficar na mesma transação do enfileiramento.
    """

    authentication_classes = []
    permission_classes = []

    def post(self, request, variant: str):
        if variant != settings.BILLING_GATEWAY_VARIANT:
            raise APIError(CoreErrorCode.NOT_FOUND, status_code=404)
        try:
            event = get_client().webhooks.verify(request.body, request.headers)
        except WebhookVerificationError as error:
            raise APIError(BillingErrorCode.WEBHOOK_INVALID, status_code=400) from error
        record, created = record_event(event, variant=variant)
        if created:
            transaction.on_commit(lambda: process_billing_event.delay(record.pk))
        return Response(status=status.HTTP_200_OK)


@document_subscription_cancel
@io_externo_sem_transacao
class SubscriptionCancelView(SubscriptionViewMixin, APIView):
    """Agenda (`POST`) ou desfaz (`DELETE`) o cancelamento no fim do período. Papel mínimo: proprietário."""

    papel_minimo = Papel.PROPRIETARIO

    def post(self, request):
        key = _idempotency_key(request)
        organization_id = request.tenant.organization_id
        with translate_gateway_errors():
            subscription = Subscriptions.cancel(organization_id, idempotency_key=key)
        return Response(_representation(organization_id, subscription))

    def delete(self, request):
        key = _idempotency_key(request)
        organization_id = request.tenant.organization_id
        with translate_gateway_errors():
            subscription = Subscriptions.resume(organization_id, idempotency_key=key)
        return Response(_representation(organization_id, subscription))
