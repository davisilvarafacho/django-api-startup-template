"""Validação de senha contra vazamentos públicos (HaveIBeenPwned).

A consulta usa *k-anonymity*: o SHA-1 da senha é calculado localmente e só os
cinco primeiros caracteres do digest saem da aplicação. A API devolve todos os
sufixos daquele prefixo e a comparação acontece aqui — a senha, o digest
completo e o sufixo nunca trafegam.

O cliente é **fail-open**: se a API estiver fora do ar, lenta ou responder algo
inesperado, a senha é aceita. Indisponibilidade de um serviço externo não pode
impedir alguém de trocar a própria senha; o risco de aceitar uma senha vazada
nessa janela é menor que o de travar o fluxo de recuperação de conta.
"""

import hashlib
import logging
import time

from django.conf import settings
from django.core.exceptions import ValidationError

import httpx

from apps.api.autenticacao.errors import AuthErrorCode

logger = logging.getLogger(__name__)

PREFIX_LENGTH = 5


class PwnedPasswordsClient:
    """Consulta o range da API do HaveIBeenPwned para um prefixo de SHA-1."""

    def __init__(self, base_url=None, timeout=None):
        self.base_url = (base_url or settings.HIBP_PASSWORDS_URL).rstrip("/")
        self.timeout = timeout if timeout is not None else settings.HIBP_TIMEOUT_SECONDS

    def is_pwned(self, password: str) -> bool:
        """Informa se a senha aparece em algum vazamento conhecido.

        Args:
            password: Senha em texto puro, usada apenas para gerar o digest local.

        Returns:
            `True` somente quando a API confirma pelo menos uma ocorrência.
            Qualquer falha resulta em `False` (fail-open).
        """
        digest = hashlib.sha1(password.encode("utf-8"), usedforsecurity=False).hexdigest().upper()
        prefix, suffix = digest[:PREFIX_LENGTH], digest[PREFIX_LENGTH:]

        started_at = time.monotonic()
        try:
            # `Add-Padding` faz a API devolver sufixos falsos com contagem zero,
            # para que o tamanho da resposta não denuncie o prefixo consultado.
            response = httpx.get(
                f"{self.base_url}/{prefix}",
                headers={"Add-Padding": "true"},
                timeout=self.timeout,
            )
            response.raise_for_status()
            return self._count_for(response.text, suffix) > 0
        except (httpx.HTTPError, ValueError) as exc:
            # Nunca registrar o prefixo: junto com a latência ele estreitaria o
            # espaço de busca da senha em quem tiver acesso aos logs.
            logger.warning(
                "Consulta ao HaveIBeenPwned falhou; senha aceita por fail-open.",
                extra={
                    "error_class": type(exc).__name__,
                    "duration_seconds": round(time.monotonic() - started_at, 3),
                },
            )
            return False

    def _count_for(self, body: str, suffix: str) -> int:
        """Extrai a contagem de vazamentos do sufixo dentro do range devolvido.

        Raises:
            ValueError: Se alguma linha não seguir o formato `SUFIXO:CONTAGEM`.
        """
        for line in body.splitlines():
            if not line.strip():
                continue
            found, _, count = line.strip().partition(":")
            if not count:
                raise ValueError("linha sem contagem no range do HaveIBeenPwned")
            if found.upper() == suffix:
                return int(count)
        return 0


class PwnedPasswordValidator:
    """Validador Django que recusa senhas presentes em vazamentos públicos.

    Bloqueia qualquer contagem maior que zero: uma senha vazada uma única vez já
    está em listas de ataque por dicionário.
    """

    def __init__(self, enabled=None, client=None):
        self._enabled = enabled
        self._client = client

    @property
    def enabled(self) -> bool:
        if self._enabled is None:
            return settings.HIBP_PASSWORD_CHECK_ENABLED
        return self._enabled

    @property
    def client(self) -> PwnedPasswordsClient:
        if self._client is None:
            self._client = PwnedPasswordsClient()
        return self._client

    def validate(self, password, user=None):
        """Rejeita a senha se ela aparecer em vazamentos conhecidos."""
        if not self.enabled:
            return
        if self.client.is_pwned(password):
            raise ValidationError(
                AuthErrorCode.PWNED_PASSWORD.label,
                code=AuthErrorCode.PWNED_PASSWORD.value,
            )

    def get_help_text(self):
        return AuthErrorCode.PWNED_PASSWORD.label
