"""Contrato OpenAPI da cobrança: espelhos dos serializers serpy e os erros de cada rota."""

from rest_framework import serializers

from drf_spectacular.utils import extend_schema, inline_serializer

from apps.api.autenticacao.errors import AuthErrorCode
from apps.api.core.errors import ValidationErrorCode
from apps.api.core.schema import document_error_codes
from apps.billing.errors import BillingErrorCode
from apps.billing.serializers import PlanSelectionSerializer
from apps.organizacoes.errors import OrganizationErrorCode


class PlanSchema(serializers.Serializer):
    """Espelho de `PlanSerpySerializer` para o schema OpenAPI."""

    id = serializers.IntegerField()
    slug = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField()
    rank = serializers.IntegerField(help_text="Posição do plano; o rank maior é o superior.")
    price_amount = serializers.IntegerField(allow_null=True, help_text="Valor em centavos, apenas para exibição.")
    currency = serializers.CharField()
    billing_cycle = serializers.CharField(allow_null=True)
    member_limit = serializers.IntegerField(help_text="Máximo de vínculos ativos neste plano.")
    is_free = serializers.BooleanField()


class SubscriptionSchema(serializers.Serializer):
    """Espelho de `SubscriptionSerpySerializer`, com o uso de membros que a view acrescenta."""

    plan = PlanSchema()
    pending_plan = PlanSchema(allow_null=True, help_text="Plano para o qual haverá downgrade no próximo ciclo.")
    status = serializers.CharField()
    has_access = serializers.BooleanField()
    effective_plan = PlanSchema(help_text="Plano que de fato vale: o pago se há acesso, senão o gratuito.")
    current_period_end = serializers.CharField(allow_null=True, help_text="Fim do período corrente, em ISO 8601.")
    cancel_at = serializers.CharField(allow_null=True, help_text="Cancelamento agendado, em ISO 8601.")
    is_cancel_scheduled = serializers.BooleanField()
    members = inline_serializer(
        name="BillingMembersSchema",
        fields={
            "active": serializers.IntegerField(help_text="Vínculos ativos da organização."),
            "limit": serializers.IntegerField(help_text="Limite de membros do plano efetivo."),
        },
    )


class CheckoutSchema(serializers.Serializer):
    """Resposta de `POST /cobranca/assinatura/checkout/`."""

    url = serializers.URLField(help_text="URL do checkout hospedado pelo gateway.")


# Webhook é contrato com o gateway, não com clientes da API: fica fora do OpenAPI.
document_webhook = extend_schema(exclude=True)

_AUTH_ERRORS = document_error_codes(AuthErrorCode.NOT_AUTHENTICATED)
_ROLE_ERRORS = document_error_codes(OrganizationErrorCode.ROLE_INSUFFICIENT)
_IDEMPOTENCY_ERRORS = document_error_codes(BillingErrorCode.IDEMPOTENCY_KEY_REQUIRED)
_GATEWAY_ERRORS = document_error_codes(BillingErrorCode.GATEWAY_UNCERTAIN, BillingErrorCode.GATEWAY_ERROR)

document_plan_list = extend_schema(
    responses={200: PlanSchema(many=True), 401: _AUTH_ERRORS, 403: document_error_codes(AuthErrorCode.PERMISSION_DENIED)},
    description="Catálogo de planos ativos, em ordem de rank. Só sessão humana (API key recebe 403); não exige `X-Organization`.",
)

document_subscription_get = extend_schema(
    responses={200: SubscriptionSchema, 401: _AUTH_ERRORS, 403: _ROLE_ERRORS},
)

document_subscription_checkout = extend_schema(
    request=PlanSelectionSerializer,
    responses={
        201: CheckoutSchema,
        401: _AUTH_ERRORS,
        403: _ROLE_ERRORS,
        409: document_error_codes(BillingErrorCode.ALREADY_SUBSCRIBED),
        422: document_error_codes(BillingErrorCode.PLAN_UNAVAILABLE, BillingErrorCode.IDEMPOTENCY_KEY_REQUIRED, ValidationErrorCode.INVALID),
        502: document_error_codes(BillingErrorCode.GATEWAY_ERROR),
        503: document_error_codes(BillingErrorCode.GATEWAY_UNCERTAIN),
    },
)

document_subscription_change = extend_schema(
    request=PlanSelectionSerializer,
    responses={
        200: SubscriptionSchema,
        401: _AUTH_ERRORS,
        403: _ROLE_ERRORS,
        409: document_error_codes(
            BillingErrorCode.NOT_SUBSCRIBED,
            BillingErrorCode.DOWNGRADE_BLOCKED,
            BillingErrorCode.CURRENT_PLAN_UNKNOWN,
        ),
        422: document_error_codes(
            BillingErrorCode.PLAN_UNAVAILABLE,
            BillingErrorCode.SAME_PLAN,
            BillingErrorCode.IDEMPOTENCY_KEY_REQUIRED,
            ValidationErrorCode.INVALID,
        ),
        502: document_error_codes(BillingErrorCode.GATEWAY_ERROR),
        503: document_error_codes(BillingErrorCode.GATEWAY_UNCERTAIN),
    },
)

document_subscription_cancel = extend_schema(
    request=None,
    responses={
        200: SubscriptionSchema,
        401: _AUTH_ERRORS,
        403: _ROLE_ERRORS,
        409: document_error_codes(BillingErrorCode.NOT_SUBSCRIBED, BillingErrorCode.DOWNGRADE_BLOCKED),
        422: _IDEMPOTENCY_ERRORS,
        502: document_error_codes(BillingErrorCode.GATEWAY_ERROR),
        503: document_error_codes(BillingErrorCode.GATEWAY_UNCERTAIN),
    },
)
