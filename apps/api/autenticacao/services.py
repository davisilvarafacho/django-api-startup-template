"""Serviços transacionais para emissão e revogação de credenciais."""

from collections.abc import Iterable
from dataclasses import dataclass

from django.db import transaction

from .models import AuthToken, TokenMetaData, TokenType


@dataclass(frozen=True)
class IssuedToken:
    """Token persistido e seu segredo, disponível somente na emissão."""

    instance: AuthToken
    plain_token: str


def issue_token(*, responsavel, token_type: TokenType, expiry, metadata_input: dict) -> IssuedToken:
    """Emite token e metadados juntos, sem persistência parcial."""
    with transaction.atomic():
        instance, plain_token = AuthToken.objects.create(
            responsavel=responsavel,
            type=token_type,
            expiry=expiry,
        )
        TokenMetaData.objects.create(token=instance, **metadata_input)

    return IssuedToken(instance=instance, plain_token=plain_token)


def revoke_tokens(user, *, types: Iterable[TokenType], exclude_digest: str | None = None) -> int:
    """Remove tokens de tipos selecionados, opcionalmente preservando um digest."""
    queryset = AuthToken.objects.filter(responsavel=user, type__in=types)
    if exclude_digest is not None:
        queryset = queryset.exclude(digest=exclude_digest)

    deleted_count = queryset.count()
    queryset.delete()
    return deleted_count
