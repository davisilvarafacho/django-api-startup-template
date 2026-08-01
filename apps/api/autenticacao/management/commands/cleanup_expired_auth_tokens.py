"""Management command para remoção segura de tokens expirados."""

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.api.autenticacao.models import TokenType
from apps.api.autenticacao.token_cleanup import cleanup_expired_tokens


class Command(BaseCommand):
    """Remove tokens expirados conforme a política operacional de autenticação."""

    help = "Remove tokens de autenticação expirados em lotes curtos."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Exibe as contagens sem remover tokens.")
        parser.add_argument(
            "--batch-size",
            type=int,
            default=settings.AUTH_TOKEN_CLEANUP_BATCH_SIZE,
            help="Quantidade máxima de tokens removidos por transação.",
        )
        parser.add_argument(
            "--session-retention-days",
            type=int,
            default=settings.AUTH_TOKEN_SESSION_RETENTION_DAYS,
            help="Dias que uma sessão expirada permanece retida.",
        )

    def handle(self, *args, **options):
        try:
            result = cleanup_expired_tokens(
                now=timezone.now(),
                batch_size=options["batch_size"],
                session_retention=timedelta(days=options["session_retention_days"]),
                dry_run=options["dry_run"],
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        for token_type in TokenType:
            self.stdout.write(f"{token_type.name}: examinados={result.examined[int(token_type)]} removidos={result.deleted[int(token_type)]}")
