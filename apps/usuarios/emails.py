"""Tokens assinados para ações de confiança no e-mail."""

from dataclasses import dataclass

from django.conf import settings
from django.core import signing

EMAIL_VERIFICATION_PURPOSE = "email_verification"
EMAIL_CHANGE_PURPOSE = "email_change"
ACCOUNT_REACTIVATION_PURPOSE = "account_reactivation"

_SALTS = {
    EMAIL_VERIFICATION_PURPOSE: "usuarios.email-verification",
    EMAIL_CHANGE_PURPOSE: "usuarios.email-change",
    ACCOUNT_REACTIVATION_PURPOSE: "usuarios.account-reactivation",
}


@dataclass(frozen=True)
class SignedEmailToken:
    usuario_id: int
    email: str
    purpose: str


def normalizar_email(email: str) -> str:
    """Normaliza pelo manager do usuário, a fonte da regra de contas."""
    from .models import Usuario

    return Usuario.objects.normalize_email(email)


def emitir_token_email(*, usuario, email: str, purpose: str) -> str:
    """Assina o payload mínimo de um fluxo de e-mail, sem persistir estado."""
    if purpose not in _SALTS:
        raise ValueError("Finalidade de token de e-mail inválida.")
    payload = {"usuario_id": usuario.pk, "email": normalizar_email(email), "purpose": purpose}
    return signing.TimestampSigner(salt=_SALTS[purpose]).sign_object(payload)


def emitir_token_verificacao(usuario) -> str:
    return emitir_token_email(usuario=usuario, email=usuario.email, purpose=EMAIL_VERIFICATION_PURPOSE)


def emitir_token_troca_email(usuario, email: str) -> str:
    return emitir_token_email(usuario=usuario, email=email, purpose=EMAIL_CHANGE_PURPOSE)


def emitir_token_reativacao(usuario) -> str:
    return emitir_token_email(usuario=usuario, email=usuario.email, purpose=ACCOUNT_REACTIVATION_PURPOSE)


def carregar_token_email(token: str, *, purpose: str) -> SignedEmailToken | None:
    """Lê um token do propósito esperado sem distinguir formas de falha."""
    if purpose not in _SALTS:
        raise ValueError("Finalidade de token de e-mail inválida.")
    max_ages = {
        EMAIL_VERIFICATION_PURPOSE: settings.EMAIL_VERIFICATION_TOKEN_MAX_AGE_SECONDS,
        EMAIL_CHANGE_PURPOSE: settings.EMAIL_CHANGE_TOKEN_MAX_AGE_SECONDS,
        ACCOUNT_REACTIVATION_PURPOSE: settings.ACCOUNT_REACTIVATION_TOKEN_MAX_AGE_SECONDS,
    }
    max_age = max_ages[purpose]
    try:
        payload = signing.TimestampSigner(salt=_SALTS[purpose]).unsign_object(token, max_age=max_age)
        signed_token = SignedEmailToken(
            usuario_id=int(payload["usuario_id"]),
            email=normalizar_email(payload["email"]),
            purpose=payload["purpose"],
        )
    except (KeyError, TypeError, ValueError, signing.BadSignature):
        return None

    return signed_token if signed_token.purpose == purpose else None
