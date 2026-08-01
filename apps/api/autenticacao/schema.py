"""Documentação OpenAPI de autenticação: contrato de erro por endpoint.

CRUD simples de sessões/API keys já é inferido pelo `AutoSchema` a partir do
serializer da view; só os endpoints com efeito colateral ou plain token na
resposta (login, criação/rotação de API key) precisam do detalhe manual
abaixo.
"""

from drf_spectacular.utils import extend_schema

from apps.api.core.schema import document_error_codes
from apps.organizacoes.errors import OrganizationErrorCode

from .errors import AuthErrorCode
from .serializers import APIKeySerializer, LoginResponseSerializer

TOKEN_ONCE_DESCRIPTION = (
    "`token` é o segredo em texto plano: só aparece nesta resposta, não é "
    "recuperável depois e nunca aparece em listagem, detalhe ou qualquer "
    "outra resposta."
)

document_login = extend_schema(
    responses={
        200: LoginResponseSerializer,
        401: document_error_codes(AuthErrorCode.INVALID_CREDENTIALS, AuthErrorCode.USER_INACTIVE),
    },
    description=TOKEN_ONCE_DESCRIPTION,
)

document_reauthenticate = extend_schema(
    responses={
        204: None,
        401: document_error_codes(
            AuthErrorCode.NOT_AUTHENTICATED,
            AuthErrorCode.INVALID_CREDENTIALS,
        ),
        403: document_error_codes(AuthErrorCode.PERMISSION_DENIED),
    },
)

document_api_key_create = extend_schema(
    responses={
        201: APIKeySerializer,
        401: document_error_codes(AuthErrorCode.REAUTHENTICATION_REQUIRED),
        403: document_error_codes(AuthErrorCode.SCOPE_NOT_DELEGABLE),
        422: document_error_codes(OrganizationErrorCode.MEMBERSHIP_REQUIRED),
    },
    description=TOKEN_ONCE_DESCRIPTION,
)

document_api_key_rotate = extend_schema(
    responses={
        201: APIKeySerializer,
        401: document_error_codes(AuthErrorCode.REAUTHENTICATION_REQUIRED),
        409: document_error_codes(AuthErrorCode.REVOKED_TOKEN),
    },
    description=TOKEN_ONCE_DESCRIPTION,
)

document_api_key_suspend = extend_schema(responses={200: APIKeySerializer})

document_api_key_resume = extend_schema(
    responses={
        200: APIKeySerializer,
        409: document_error_codes(AuthErrorCode.REVOKED_TOKEN, AuthErrorCode.RESPONSIBLE_INACTIVE),
    },
)
