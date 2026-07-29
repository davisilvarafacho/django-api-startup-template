"""Rotate encrypted model fields to the active Fernet key."""

from django.core.management.base import BaseCommand, CommandError

from utils.sensitive_fields import rotate_sensitive_fields


class Command(BaseCommand):
    help = "Recriptografa campos sensíveis que ainda usam uma chave antiga."

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=500)

    def handle(self, *args, **options):
        try:
            result = rotate_sensitive_fields(batch_size=options["batch_size"])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"Campos sensíveis recriptografados: {result.updated}"))
