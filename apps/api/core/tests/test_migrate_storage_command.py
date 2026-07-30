from io import StringIO
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.core.files.storage import InMemoryStorage
from django.core.management import CommandError, call_command
from django.test import override_settings

import pytest

from apps.api.core.storage_migration import resolve_storage


@override_settings(
    STORAGES={
        "temporary": {
            "BACKEND": "django.core.files.storage.InMemoryStorage",
        }
    }
)
def test_resolve_storage_accepts_settings_alias():
    assert isinstance(resolve_storage("temporary"), InMemoryStorage)


def test_resolve_storage_accepts_zero_argument_class_path():
    storage = resolve_storage("django.core.files.storage.InMemoryStorage")

    assert isinstance(storage, InMemoryStorage)


def test_command_rejects_identical_identifiers():
    with pytest.raises(CommandError, match="must be different"):
        call_command("migrate_storage", source="default", destination="default")


@patch("apps.api.core.management.commands.migrate_storage.resolve_storage")
def test_command_prints_progress_and_summary(resolve_storage_mock):
    source = InMemoryStorage()
    source.save("picture.jpg", ContentFile(b"picture"))
    destination = InMemoryStorage()
    resolve_storage_mock.side_effect = [source, destination]
    stdout = StringIO()

    call_command(
        "migrate_storage",
        source="legacy",
        destination="archive",
        stdout=stdout,
    )

    output = stdout.getvalue()
    assert "COPIED picture.jpg -> picture.jpg" in output
    assert "Discovered: 1" in output
    assert "Copied: 1" in output
    assert "Errors: 0" in output


@patch("apps.api.core.management.commands.migrate_storage.resolve_storage")
def test_command_prints_summary_then_exits_nonzero_on_errors(resolve_storage_mock):
    class BrokenListStorage(InMemoryStorage):
        def listdir(self, path):
            raise NotImplementedError("listing unsupported")

    resolve_storage_mock.side_effect = [BrokenListStorage(), InMemoryStorage()]
    stdout = StringIO()
    stderr = StringIO()

    with pytest.raises(CommandError, match="finished with 1 error"):
        call_command(
            "migrate_storage",
            source="legacy",
            destination="archive",
            stdout=stdout,
            stderr=stderr,
        )

    assert "Discovered: 0" in stdout.getvalue()
    assert "Errors: 1" in stdout.getvalue()
    assert "listing unsupported" in stderr.getvalue()
