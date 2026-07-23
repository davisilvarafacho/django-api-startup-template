from django.conf import settings

from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from knox.auth import TokenAuthentication
from knox.settings import knox_settings

from .constants import REQUEST_ATTR_RESOLVED, RESOLVED_PUBLIC


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


class QueryParamTokenAuthentication(TokenAuthentication):
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
