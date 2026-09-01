"""Contrato OpenAPI dos checkouts de faturamento."""

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema

from apps.api.autenticacao.errors import AuthErrorCode
from apps.api.core.errors import CoreErrorCode, ValidationErrorCode
from apps.api.core.schema import document_error_codes
from apps.assinaturas.errors import BillingErrorCode
from apps.assinaturas.serializers import AceitarPropostaRequestSerializer, AceitarPropostaResponseSerializer
from apps.assinaturas.subapps.faturamento.errors import ErrosFaturamento
from apps.assinaturas.subapps.faturamento.serializers import (
    CheckoutResponseSerializer,
    CriarCheckoutRequestSerializer,
    CriarFormaPagamentoCheckoutRequestSerializer,
)
from apps.organizacoes.errors import OrganizationErrorCode

AUTH = document_error_codes(AuthErrorCode.TOKEN_NOT_PROVIDED, AuthErrorCode.INVALID_TOKEN, AuthErrorCode.REAUTHENTICATION_REQUIRED)
TENANT = document_error_codes(
    OrganizationErrorCode.MEMBERSHIP_REQUIRED,
    OrganizationErrorCode.ORGANIZATION_INACTIVE,
    OrganizationErrorCode.ROLE_INSUFFICIENT,
)
CONFLICT = document_error_codes(ErrosFaturamento.CHECKOUT_CONFLITO, ErrosFaturamento.CHECKOUT_PENDENTE)
UNCERTAIN = document_error_codes(ErrosFaturamento.CHECKOUT_INCERTO)

document_proposal_accept = extend_schema(
    request=AceitarPropostaRequestSerializer,
    responses={
        200: AceitarPropostaResponseSerializer,
        400: document_error_codes(ValidationErrorCode.INVALID, ValidationErrorCode.MALFORMED),
        401: document_error_codes(
            AuthErrorCode.TOKEN_NOT_PROVIDED,
            AuthErrorCode.INVALID_TOKEN,
            AuthErrorCode.EXPIRED_TOKEN,
            AuthErrorCode.REVOKED_TOKEN,
            AuthErrorCode.API_KEY_SUSPENDED,
            AuthErrorCode.RESPONSIBLE_INACTIVE,
            AuthErrorCode.REAUTHENTICATION_REQUIRED,
        ),
        403: document_error_codes(
            AuthErrorCode.PERMISSION_DENIED,
            OrganizationErrorCode.MEMBERSHIP_REQUIRED,
            OrganizationErrorCode.MEMBERSHIP_INACTIVE,
            OrganizationErrorCode.ORGANIZATION_INACTIVE,
            OrganizationErrorCode.ROLE_INSUFFICIENT,
            BillingErrorCode.ORGANIZATION_RESTRICTED,
        ),
        404: document_error_codes(CoreErrorCode.NOT_FOUND),
        409: document_error_codes(
            BillingErrorCode.PROPOSAL_INVALID,
            OrganizationErrorCode.TENANT_MISMATCH,
            ErrosFaturamento.CHECKOUT_PENDENTE,
            ErrosFaturamento.CHECKOUT_CONFLITO,
        ),
        422: document_error_codes(OrganizationErrorCode.HEADER_REQUIRED, ErrosFaturamento.CHECKOUT_INDISPONIVEL),
        503: document_error_codes(BillingErrorCode.SUBSCRIPTION_REQUIRED, ErrosFaturamento.CHECKOUT_INCERTO),
    },
)


def _create(request):
    return extend_schema(
        request=request,
        responses={
            201: CheckoutResponseSerializer,
            400: document_error_codes(ValidationErrorCode.INVALID, ValidationErrorCode.MALFORMED),
            401: AUTH,
            403: TENANT,
            409: CONFLICT,
            422: document_error_codes(OrganizationErrorCode.HEADER_REQUIRED, ErrosFaturamento.CHECKOUT_INDISPONIVEL),
            503: UNCERTAIN,
        },
    )


document_checkout_create = _create(CriarCheckoutRequestSerializer)
document_setup_create = _create(CriarFormaPagamentoCheckoutRequestSerializer)
document_checkout_list = extend_schema(
    responses={200: CheckoutResponseSerializer(many=True), 401: AUTH, 403: TENANT, 422: document_error_codes(OrganizationErrorCode.HEADER_REQUIRED)}
)

document_webhook = extend_schema(
    request=None,
    parameters=[OpenApiParameter("variante", str, OpenApiParameter.PATH, description="Variante configurada do gateway.")],
    responses={
        200: OpenApiResponse(description="Evento autenticado recebido ou duplicata idempotente."),
        400: document_error_codes(
            ErrosFaturamento.WEBHOOK_SIGNATURE_INVALID,
            ErrosFaturamento.WEBHOOK_PROTOCOL_INVALID,
            ErrosFaturamento.WEBHOOK_VARIANT_INVALID,
        ),
        409: document_error_codes(ErrosFaturamento.WEBHOOK_COLLISION),
    },
    auth=[],
)
