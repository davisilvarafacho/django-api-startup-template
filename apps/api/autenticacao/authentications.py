from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from knox.auth import TokenAuthentication
from knox.settings import knox_settings

from apps.api.core.errors import APIError

from .constants import REQUEST_ATTR_RESOLVED, RESOLVED_PUBLIC
from .errors import AuthErrorCode
from .models import TokenType


class PassthroughAuthentication(BaseAuthentication):
    """Reaproveita o usuário já resolvido pelo `AuthenticationMiddleware`.

    Não valida token nenhum: a autenticação de verdade acontece no middleware,
    antes de qualquer outro middleware que dependa de `request.user`. Aqui só
    devolvemos o resultado para o DRF, de modo que `IsAuthenticated` e as demais
    permissões continuem funcionando normalmente.
    """

    def authenticate(self, request):
        django_request = getattr(request, "_request", request)
        resolved = getattr(django_request, REQUEST_ATTR_RESOLVED, None)

        if resolved is None:
            # Trava de segurança: sem o middleware na cadeia, falhamos alto em vez
            # de silenciosamente liberar a request.
            raise RuntimeError(
                "Request não processada pelo AuthenticationMiddleware. "
                "Verifique se 'apps.api.autenticacao.middleware.AuthenticationMiddleware' "
                "está em settings.MIDDLEWARE."
            )

        if resolved == RESOLVED_PUBLIC:
            return None

        user = getattr(django_request, "user", None)

        if user is None or not user.is_active:
            raise AuthenticationFailed("Usuário inativo ou inválido.")

        return (user, getattr(django_request, "auth", None))

    def authenticate_header(self, request):
        # Sem isso o DRF responde 403 (e não 401) quando a permissão barra um anônimo.
        return knox_settings.AUTH_HEADER_PREFIX


class TypedTokenAuthentication(TokenAuthentication):
    """Autenticador Knox que respeita tipo e estado do token, sem apagar nada.

    O Knox padrão apaga tokens expirados do banco (`_cleanup_token`) — ruim
    para API keys, que precisam permanecer para auditoria mesmo inválidas.
    Aqui a limpeza automática é desligada e o estado (expirado, revogado,
    suspenso, responsável inativo) vira `APIError` tipado em `validate_user`.
    """

    allowed_token_types = (TokenType.TOKEN, TokenType.API_KEY)

    def _cleanup_token(self, auth_token) -> bool:
        return False

    def validate_user(self, auth_token):
        token_type = getattr(auth_token, "type", TokenType.TOKEN)

        if token_type not in self.allowed_token_types:
            raise AuthenticationFailed(_("Este token não permite acesso à API."))

        if auth_token.expiry is not None and auth_token.expiry < timezone.now():
            raise APIError(AuthErrorCode.EXPIRED_TOKEN, status_code=401)

        if auth_token.revoked_at is not None:
            raise APIError(AuthErrorCode.REVOKED_TOKEN, status_code=401)

        if auth_token.suspended_at is not None:
            raise APIError(AuthErrorCode.API_KEY_SUSPENDED, status_code=401)

        if not auth_token.responsavel.is_active:
            raise APIError(AuthErrorCode.RESPONSIBLE_INACTIVE, status_code=401)

        return (auth_token.responsavel, auth_token)


class PreAuthTokenAuthentication(TypedTokenAuthentication):
    """Aceita credenciais efêmeras somente nas views de conclusão MFA."""

    allowed_token_types = (TokenType.PRE_AUTH,)

    def authenticate(self, request):
        header = request.META.get("HTTP_AUTHORIZATION", "")
        if not header.startswith("PreAuth "):
            return None
        token = header.removeprefix("PreAuth ").strip()
        if not token:
            raise AuthenticationFailed(_("Token de pré-autenticação inválido."))
        user, auth_token = self.authenticate_credentials(token.encode())
        if auth_token.is_expired:
            raise AuthenticationFailed(_("Token de pré-autenticação expirado."))
        return user, auth_token


class QueryParamTokenAuthentication(TypedTokenAuthentication):
    """Autentica via `?token=`, restrito aos e-mails em `settings.ADMINS_EMAILS`.

    Serve para abrir links autenticados direto no navegador (relatórios, exports),
    onde não dá para mandar o header `Authorization`.
    """

    def authenticate(self, request):
        # Aceita tanto a Request do DRF (`query_params`) quanto a HttpRequest do
        # Django (`GET`), já que o middleware chama esta classe direto.
        query_params = getattr(request, "query_params", None)
        if query_params is None:
            query_params = request.GET

        token = query_params.get("token")
        if not token:
            return None

        user, auth_token = self.authenticate_credentials(token.encode())

        if user.email not in settings.ADMINS_EMAILS:
            return None

        return (user, auth_token)
