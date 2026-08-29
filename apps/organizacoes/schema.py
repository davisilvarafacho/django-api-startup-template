"""Contrato OpenAPI explícito do ciclo de organizações."""

from drf_spectacular.utils import extend_schema

from apps.api.autenticacao.errors import AuthErrorCode
from apps.api.core.errors import CoreErrorCode
from apps.api.core.schema import document_error_codes
from apps.organizacoes.errors import OrganizationErrorCode
from apps.organizacoes.serializers import EncerramentoAgendadoResponseSerializer

_AUTH_ERRORS = document_error_codes(
    AuthErrorCode.NOT_AUTHENTICATED,
    AuthErrorCode.REAUTHENTICATION_REQUIRED,
)
_AUTHORIZATION_ERRORS = document_error_codes(
    AuthErrorCode.PERMISSION_DENIED,
    OrganizationErrorCode.ROLE_INSUFFICIENT,
)
_NOT_FOUND = document_error_codes(CoreErrorCode.NOT_FOUND)

document_organization_closure_post = extend_schema(
    methods=["POST"],
    request=None,
    responses={
        202: EncerramentoAgendadoResponseSerializer,
        204: None,
        401: _AUTH_ERRORS,
        403: _AUTHORIZATION_ERRORS,
        404: _NOT_FOUND,
    },
)

document_organization_closure_delete = extend_schema(
    methods=["DELETE"],
    request=None,
    responses={
        204: None,
        401: _AUTH_ERRORS,
        403: _AUTHORIZATION_ERRORS,
        404: _NOT_FOUND,
    },
)
