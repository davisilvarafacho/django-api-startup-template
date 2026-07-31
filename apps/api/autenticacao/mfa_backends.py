"""Backends mínimos e intercambiáveis para entrega de OTP por SMS."""

from django.conf import settings
from django.utils.module_loading import import_string


class BaseSMSBackend:
    def send_otp(self, *, destination: str, code: str, context: str) -> None:
        raise NotImplementedError


class ConsoleSMSBackend(BaseSMSBackend):
    def send_otp(self, *, destination: str, code: str, context: str) -> None:
        # Não registra código ou telefone: o backend de console é apenas um no-op seguro.
        return None


class InMemorySMSBackend(BaseSMSBackend):
    sent_messages: list[dict[str, str]] = []

    def send_otp(self, *, destination: str, code: str, context: str) -> None:
        self.sent_messages.append({"destination": destination, "code": code, "context": context})


def get_sms_backend() -> BaseSMSBackend:
    return import_string(getattr(settings, "MFA_SMS_BACKEND", "apps.api.autenticacao.mfa_backends.ConsoleSMSBackend"))()
