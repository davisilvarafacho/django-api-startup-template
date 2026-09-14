"""Políticas de conta compartilhadas por mutações de domínio."""

from apps.api.core.errors import APIError
from apps.usuarios.errors import AccountErrorCode


def exigir_email_verificado(usuario) -> None:
    """Impede efeitos contratuais ou financeiros antes da confirmação do e-mail."""
    if usuario.email_verificado_em is None:
        raise APIError(AccountErrorCode.EMAIL_NOT_VERIFIED, status_code=403)
