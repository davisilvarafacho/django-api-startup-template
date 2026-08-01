"""Política compartilhada para remoção idempotente de tokens expirados."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.db.models import QuerySet

from .models import AuthToken, TokenType


@dataclass(frozen=True)
class CleanupResult:
    """Contagens de tokens elegíveis e removidos, agrupadas por tipo."""

    examined: dict[int, int]
    deleted: dict[int, int]


def _empty_counts() -> dict[int, int]:
    return {int(token_type): 0 for token_type in TokenType}


def _delete_in_batches(*, token_type: int, queryset: QuerySet, batch_size: int, examined: dict[int, int], deleted: dict[int, int]) -> None:
    """Remove lotes pequenos sob lock, preservando transações curtas."""
    while True:
        with transaction.atomic():
            token_ids = list(queryset.select_for_update().order_by("expiry", "digest").values_list("pk", flat=True)[:batch_size])
            if not token_ids:
                return

            examined[token_type] += len(token_ids)
            AuthToken.objects.filter(pk__in=token_ids).delete()
            deleted[token_type] += len(token_ids)


def _count_candidates(*, querysets: Iterable[tuple[int, QuerySet]], examined: dict[int, int]) -> None:
    for token_type, queryset in querysets:
        examined[token_type] += queryset.count()


def cleanup_expired_tokens(*, now, batch_size: int, session_retention: timedelta, dry_run: bool = False) -> CleanupResult:
    """Remove tokens expirados conforme a política de retenção configurada.

    Credenciais efêmeras são removidas no instante da expiração; sessões Knox
    normais permanecem até o fim da retenção. API keys e tokens sem expiração
    não integram a seleção.
    """
    if batch_size < 1:
        raise ValueError("batch_size deve ser maior que zero.")
    if session_retention < timedelta(0):
        raise ValueError("session_retention não pode ser negativa.")

    ephemeral_querysets = [(int(token_type), AuthToken.objects.filter(type=token_type, expiry__lte=now)) for token_type in AuthToken.EPHEMERAL_TYPES]
    session_queryset = (int(TokenType.TOKEN), AuthToken.objects.filter(type=TokenType.TOKEN, expiry__lte=now - session_retention))
    querysets = [*ephemeral_querysets, session_queryset]
    examined = _empty_counts()
    deleted = _empty_counts()

    if dry_run:
        _count_candidates(querysets=querysets, examined=examined)
        return CleanupResult(examined=examined, deleted=deleted)

    for token_type, queryset in querysets:
        _delete_in_batches(token_type=token_type, queryset=queryset, batch_size=batch_size, examined=examined, deleted=deleted)

    return CleanupResult(examined=examined, deleted=deleted)
