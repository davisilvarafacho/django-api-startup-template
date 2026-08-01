from django.core.checks import run_checks
from django.test import override_settings


def test_backend_de_desenvolvimento_e_recusado_em_producao():
    with override_settings(DEBUG=False, MFA_SMS_ENABLED=True, MFA_SMS_BACKEND="apps.api.autenticacao.mfa_backends.ConsoleSMSBackend"):
        assert any(error.id == "autenticacao.E001" for error in run_checks())
