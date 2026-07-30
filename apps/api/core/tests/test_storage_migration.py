from django.core.files.base import ContentFile
from django.core.files.storage import InMemoryStorage

from apps.api.core.storage_migration import MigrationOptions, migrate_storage_objects


def make_storage(files):
    storage = InMemoryStorage()
    for name, content in files.items():
        storage.save(name, ContentFile(content, name=name))
    return storage


def read_bytes(storage, name):
    with storage.open(name, "rb") as stored_file:
        return stored_file.read()


def test_copies_files_recursively_and_preserves_names():
    source = make_storage(
        {
            "avatar.png": b"avatar",
            "documents/report.pdf": b"report",
            "documents/archive/data.csv": b"data",
        }
    )
    destination = InMemoryStorage()

    result = migrate_storage_objects(source, destination, MigrationOptions())

    assert result.discovered == 3
    assert result.copied == 3
    assert result.skipped == 0
    assert result.removed == 0
    assert result.errors == []
    assert read_bytes(destination, "avatar.png") == b"avatar"
    assert read_bytes(destination, "documents/report.pdf") == b"report"
    assert read_bytes(destination, "documents/archive/data.csv") == b"data"


def test_replaces_source_prefix_with_destination_prefix():
    source = make_storage(
        {
            "uploads/users/avatar.png": b"avatar",
            "uploads/reports/annual.pdf": b"annual",
            "private/secret.txt": b"secret",
        }
    )
    destination = InMemoryStorage()
    options = MigrationOptions(source_prefix="uploads", destination_prefix="media")

    result = migrate_storage_objects(source, destination, options)

    assert result.discovered == 2
    assert result.copied == 2
    assert destination.exists("media/users/avatar.png")
    assert destination.exists("media/reports/annual.pdf")
    assert not destination.exists("private/secret.txt")
