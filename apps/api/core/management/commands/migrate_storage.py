"""Migrate objects between two configured Django storages."""

from django.core.management.base import BaseCommand, CommandError

from apps.api.core.storage_migration import (
    MigrationOptions,
    migrate_storage_objects,
    resolve_storage,
)


class Command(BaseCommand):
    help = "Copy or move every object from one Django storage to another."

    def add_arguments(self, parser):
        parser.add_argument(
            "--source",
            required=True,
            help="STORAGES alias or storage class path.",
        )
        parser.add_argument(
            "--destination",
            required=True,
            help="STORAGES alias or storage class path.",
        )
        parser.add_argument(
            "--source-prefix",
            default="",
            help="Only migrate this source subtree.",
        )
        parser.add_argument(
            "--destination-prefix",
            default="",
            help="Prefix added to destination names.",
        )
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help="Delete matching destination objects before copying.",
        )
        parser.add_argument(
            "--remove-on-success",
            action="store_true",
            help="Delete each source object after a verified copy.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List intended actions without opening or mutating objects.",
        )

    def handle(self, *args, **options):
        source_identifier = options["source"]
        destination_identifier = options["destination"]
        if source_identifier == destination_identifier:
            raise CommandError("Source and destination identifiers must be different.")

        try:
            source = resolve_storage(source_identifier)
            destination = resolve_storage(destination_identifier)
            migration_options = MigrationOptions(
                source_prefix=options["source_prefix"],
                destination_prefix=options["destination_prefix"],
                overwrite=options["overwrite"],
                remove_on_success=options["remove_on_success"],
                dry_run=options["dry_run"],
            )
            result = migrate_storage_objects(
                source,
                destination,
                migration_options,
                self._write_event,
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            " | ".join(
                [
                    f"Discovered: {result.discovered}",
                    f"Copied: {result.copied}",
                    f"Skipped: {result.skipped}",
                    f"Removed: {result.removed}",
                    f"Errors: {len(result.errors)}",
                ]
            )
        )
        for issue in result.errors:
            self.stderr.write(self.style.ERROR(f"{issue.object_name}: {issue.message}"))
        if result.errors:
            raise CommandError(f"Storage migration finished with {len(result.errors)} error(s).")

    def _write_event(self, action, source_name, destination_name):
        labels = {
            "copied": "COPIED",
            "skipped": "SKIPPED",
            "copy-planned": "WOULD COPY",
            "remove-planned": "WOULD REMOVE",
            "removed": "REMOVED",
        }
        label = labels.get(action)
        if label:
            self.stdout.write(f"{label} {source_name} -> {destination_name}")
