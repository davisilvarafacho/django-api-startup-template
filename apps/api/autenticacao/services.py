"""Serviços transacionais de emissão de credenciais (`AuthToken`).

Views não devem chamar `AuthToken.objects.create()`/`TokenMetaData.objects.create()`
diretamente: `issue_token()` é o único ponto de entrada, garantindo que token e
metadata nascem juntos ou não nascem.
"""
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from knox.models import get_token_model

from .models import TokenMetaData, TokenType


@dataclass(frozen=True)
class IssuedToken:
    instance: object
    plain_token: str


def issue_token(
    *,
    responsavel,
    token_type,
    created_by,
    expiry,
    metadata_input,
    organization=None,
    name="",
    scopes=(),
):
    """Cria o `AuthToken` e seu `TokenMetaData` na mesma transação.

    Se a criação do metadata falhar, o token também não persiste.
    """
    auth_token_model = get_token_model()

    with transaction.atomic():
        instance, plain_token = auth_token_model.objects.create(
            responsavel=responsavel,
            type=token_type,
            created_by=created_by,
            expiry=expiry,
            organization=organization,
            name=name,
            scopes=list(scopes),
        )
        TokenMetaData.objects.create(token=instance, **metadata_input)

    return IssuedToken(instance=instance, plain_token=plain_token)


def revoke_session(token, *, actor):
    """Revoga logicamente uma sessão. Nunca apaga: o registro fica para auditoria."""
    token.revoked_at = timezone.now()
    token.revoked_by = actor
    token.save(update_fields=["revoked_at", "revoked_by"])
    return token


def revoke_all_sessions(user, *, actor, exclude_uuid=None):
    """Revoga todas as sessões (nunca API keys/reset) de `user`.

    `exclude_uuid`, quando informado, preserva aquela sessão intacta (ex.:
    logout_all preserva nenhuma; revoke_all_except_current preserva a atual).
    """
    auth_token_model = get_token_model()
    queryset = auth_token_model.objects.filter(
        responsavel=user, type=TokenType.TOKEN, revoked_at__isnull=True
    )
    if exclude_uuid is not None:
        queryset = queryset.exclude(uuid=exclude_uuid)

    return queryset.update(revoked_at=timezone.now(), revoked_by=actor)
