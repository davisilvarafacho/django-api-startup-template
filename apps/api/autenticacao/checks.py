"""Checks de deploy para impedir backends MFA inseguros em produção."""

from django.conf import settings
from django.core.checks import Error, register


@register()
def mfa_backend_check(app_configs, **kwargs):
    backend = getattr(settings, "MFA_SMS_BACKEND", "")
    insecure = backend.endswith(("ConsoleSMSBackend", "InMemorySMSBackend"))
    if getattr(settings, "MFA_SMS_ENABLED", False) and not settings.DEBUG and insecure:
        return [Error("MFA_SMS_BACKEND não pode usar backend de desenvolvimento em produção.", id="autenticacao.E001")]
    return []
