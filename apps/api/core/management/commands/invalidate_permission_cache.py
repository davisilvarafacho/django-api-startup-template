from django.core.management.base import BaseCommand, CommandError
from django.db import DEFAULT_DB_ALIAS

from common.permission_cache.invalidation import bump_epoch_scopes
from common.permission_cache.keys import global_scope


class Command(BaseCommand):
    help = "Invalida globalmente o cache de permissões."

    def handle(self, *args, **options):
        try:
            changed = bump_epoch_scopes(
                (global_scope(),),
                database_alias=DEFAULT_DB_ALIAS,
                layer="django",
                raise_errors=True,
            )
        except Exception:
            raise CommandError("Não foi possível invalidar o cache de permissões.") from None

        self.stdout.write(str(changed[global_scope()]))
