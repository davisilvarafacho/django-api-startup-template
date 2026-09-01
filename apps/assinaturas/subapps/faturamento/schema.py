"""Contrato OpenAPI dos checkouts de faturamento."""

from drf_spectacular.utils import extend_schema

from apps.api.autenticacao.errors import AuthErrorCode
from apps.api.core.errors import ValidationErrorCode
from apps.api.core.schema import document_error_codes
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
CONFLICT = document_error_codes(ErrosFaturamento.CHECKOUT_CONFLITO)
UNCERTAIN = document_error_codes(ErrosFaturamento.CHECKOUT_INCERTO)


def _create(request):
    return extend_schema(
        request=request,
        responses={
            201: CheckoutResponseSerializer,
            400: document_error_codes(ValidationErrorCode.INVALID, ValidationErrorCode.MALFORMED),
            401: AUTH,
            403: TENANT,
            409: CONFLICT,
            422: document_error_codes(OrganizationErrorCode.HEADER_REQUIRED),
            503: UNCERTAIN,
        },
    )


document_checkout_create = _create(CriarCheckoutRequestSerializer)
document_setup_create = _create(CriarFormaPagamentoCheckoutRequestSerializer)
document_checkout_list = extend_schema(
    responses={200: CheckoutResponseSerializer(many=True), 401: AUTH, 403: TENANT, 422: document_error_codes(OrganizationErrorCode.HEADER_REQUIRED)}
)
