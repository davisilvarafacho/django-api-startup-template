"""Marcadores declarativos para endpoints que exigem step-up recente."""


def require_recent_auth(max_age: int = 300, require_mfa: bool | None = None):
    """Marca uma action como dependente de reautenticação recente.

    Args:
        max_age: Idade máxima, em segundos, da confirmação de senha.
        require_mfa: Reserva o requisito de segundo fator para a integração MFA.

    Views que sobrescrevem ``permission_classes`` deixam de receber a permissão
    global automaticamente e devem incluir ``RecentAuthenticationPermission``
    de forma explícita.
    """

    def decorator(target):
        target._recent_auth_required = {"max_age": max_age, "require_mfa": require_mfa}
        return target

    return decorator
