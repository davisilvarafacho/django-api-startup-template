"""Step-up authentication: exige confirmação recente de identidade.

`@require_recent_auth()` só marca a exigência (em método, action ou classe);
`RecentAuthenticationPermission`, incluída nas permissions globais, é quem
de fato valida. Sem o decorator em lugar nenhum, a permission é um no-op.
"""

from django.utils import timezone

from rest_framework.permissions import BasePermission

from apps.api.core.errors import APIError

from .errors import AuthErrorCode
from .models import TokenType

DEFAULT_MAX_AGE_SECONDS = 300


def require_recent_auth(max_age=DEFAULT_MAX_AGE_SECONDS, require_mfa=None):
    """Declara que a action/método/classe decorada exige reautenticação recente.

    `require_mfa=None` (default) exige MFA somente se o usuário tiver MFA
    ativo; `True`/`False` força a exigência independentemente disso.
    """

    def decorator(target):
        target._recent_auth_required = {"max_age": max_age, "require_mfa": require_mfa}
        return target

    return decorator


def user_has_mfa_enabled(user):
    """Indica se o usuário tem ao menos um fator MFA confirmado e habilitado.

    Usuário sem `pk` (anônimo, ou dublê de teste) nunca tem fator: respondemos
    sem tocar o banco, que é o que permite manter DB-less quem só depende desta
    checagem.
    """
    from .mfa import active_factors

    if getattr(user, "pk", None) is None:
        return False

    return active_factors(user).exists()


class RecentAuthenticationPermission(BasePermission):
    """Aplica `@require_recent_auth` quando declarado na action, método ou view."""

    message = "Reautenticação recente necessária."

    def has_permission(self, request, view):
        config = self._resolve_config(request, view)
        if config is None:
            return True

        auth_token = getattr(request, "auth", None)
        token_type = getattr(auth_token, "type", TokenType.TOKEN)

        if token_type != TokenType.TOKEN:
            # Só sessão pode reautenticar; API key nunca satisfaz o requisito.
            raise APIError(AuthErrorCode.REAUTHENTICATION_REQUIRED, status_code=401)

        metadata = getattr(auth_token, "metadata", None)
        reauthenticated_at = getattr(metadata, "reauthenticated_at", None)

        if reauthenticated_at is None:
            raise APIError(AuthErrorCode.REAUTHENTICATION_REQUIRED, status_code=401)

        idade_segundos = (timezone.now() - reauthenticated_at).total_seconds()
        if idade_segundos > config["max_age"]:
            raise APIError(AuthErrorCode.REAUTHENTICATION_REQUIRED, status_code=401)

        require_mfa = config["require_mfa"]
        exige_mfa = require_mfa if require_mfa is not None else user_has_mfa_enabled(request.user)
        if exige_mfa and not user_has_mfa_enabled(request.user):
            # MFA forçado num usuário sem MFA configurado: sem como satisfazer.
            raise APIError(AuthErrorCode.REAUTHENTICATION_REQUIRED, status_code=401)

        return True

    def _resolve_config(self, request, view):
        action_name = getattr(view, "action", None)
        if action_name:
            action_method = getattr(view, action_name, None)
            config = getattr(action_method, "_recent_auth_required", None)
            if config is not None:
                return config

        method_name = (request.method or "").lower()
        handler = getattr(view, method_name, None) if method_name else None
        config = getattr(handler, "_recent_auth_required", None)
        if config is not None:
            return config

        return getattr(view, "_recent_auth_required", None)
