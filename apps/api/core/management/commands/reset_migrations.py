"""Expõe o reset protegido de migrations próprias como comando Django."""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.api.core.migration_resets import (
    MIGRATION_RESET_STEPS,
    MigrationResetError,
    apply_migration_reset,
    build_migration_reset_plan,
)


class Command(BaseCommand):
    help = "Reconstrói as migrations dos apps próprios e o schema public com confirmação explícita."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Executa as alterações exibidas pelo dry-run.")
        parser.add_argument("--confirm-database", help="Nome exato do banco descartável.")

    def handle(self, *args, **options):
        if options["confirm_database"] and not options["apply"]:
            raise CommandError("--confirm-database só pode ser usado com --apply.")

        try:
            plan = build_migration_reset_plan()
        except MigrationResetError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(f"Ambiente: {settings.ENVIROMENT or 'development'}")
        self.stdout.write(f"Banco: {plan.database_name}")
        self.stdout.write(f"Apps: {', '.join(plan.app_labels)}")
        for path in plan.remove:
            self.stdout.write(f"REMOVER {path.relative_to(plan.base_dir)}")
        for path in plan.preserve:
            self.stdout.write(f"PRESERVAR {path.relative_to(plan.base_dir)}")
        self.stdout.write("ETAPAS:")
        for index, step in enumerate(MIGRATION_RESET_STEPS, start=1):
            self.stdout.write(f"{index}. {step}")

        if not options["apply"]:
            self.stdout.write(self.style.WARNING("DRY-RUN: nenhuma alteração foi aplicada."))
            return

        try:
            apply_migration_reset(plan, confirmed_database=options["confirm_database"])
        except MigrationResetError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS("Migrations e schema reconstruídos com sucesso."))
