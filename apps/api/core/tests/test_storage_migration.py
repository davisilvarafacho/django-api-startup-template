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


class WrongSizeStorage(InMemoryStorage):
    def size(self, name):
        return super().size(name) + 1


class WrongNameStorage(InMemoryStorage):
    def save(self, name, content, max_length=None):
        return super().save(f"renamed/{name}", content, max_length=max_length)


class UnsupportedSizeStorage(InMemoryStorage):
    def size(self, name):
        raise NotImplementedError("size unsupported")


class SelectiveOpenFailureStorage(InMemoryStorage):
    def open(self, name, mode="rb"):
        if name == "broken.txt":
            raise OSError("simulated read failure")
        return super().open(name, mode)


def test_existing_file_is_skipped_without_removing_source():
    source = make_storage({"same.txt": b"new"})
    destination = make_storage({"same.txt": b"old"})
    options = MigrationOptions(remove_on_success=True)

    result = migrate_storage_objects(source, destination, options)

    assert result.skipped == 1
    assert result.copied == 0
    assert result.removed == 0
    assert source.exists("same.txt")
    assert read_bytes(destination, "same.txt") == b"old"


def test_overwrite_replaces_existing_destination_file():
    source = make_storage({"same.txt": b"new"})
    destination = make_storage({"same.txt": b"old"})

    result = migrate_storage_objects(source, destination, MigrationOptions(overwrite=True))

    assert result.copied == 1
    assert result.skipped == 0
    assert read_bytes(destination, "same.txt") == b"new"


def test_dry_run_reports_actions_without_opening_or_mutating():
    source = SelectiveOpenFailureStorage()
    source.save("broken.txt", ContentFile(b"source"))
    destination = InMemoryStorage()
    events = []
    options = MigrationOptions(dry_run=True, remove_on_success=True)

    result = migrate_storage_objects(source, destination, options, lambda *event: events.append(event))

    assert result.copied == 1
    assert result.removed == 1
    assert result.errors == []
    assert source.exists("broken.txt")
    assert not destination.exists("broken.txt")
    assert ("copy-planned", "broken.txt", "broken.txt") in events
    assert ("remove-planned", "broken.txt", "broken.txt") in events


def test_remove_on_success_deletes_only_verified_sources():
    source = make_storage({"good.txt": b"good"})
    destination = InMemoryStorage()

    result = migrate_storage_objects(
        source,
        destination,
        MigrationOptions(remove_on_success=True),
    )

    assert result.copied == 1
    assert result.removed == 1
    assert not source.exists("good.txt")
    assert destination.exists("good.txt")


def test_size_mismatch_keeps_source_and_records_error():
    source = make_storage({"different.txt": b"source"})
    destination = WrongSizeStorage()

    result = migrate_storage_objects(
        source,
        destination,
        MigrationOptions(remove_on_success=True),
    )

    assert result.copied == 0
    assert result.removed == 0
    assert len(result.errors) == 1
    assert "Size mismatch" in result.errors[0].message
    assert source.exists("different.txt")


def test_unexpected_saved_name_keeps_source_and_records_error():
    source = make_storage({"renamed.txt": b"source"})
    destination = WrongNameStorage()

    result = migrate_storage_objects(
        source,
        destination,
        MigrationOptions(remove_on_success=True),
    )

    assert result.copied == 0
    assert result.removed == 0
    assert len(result.errors) == 1
    assert "unexpected name" in result.errors[0].message
    assert source.exists("renamed.txt")


def test_storage_without_size_is_reported_as_incompatible():
    source = UnsupportedSizeStorage()
    source.save("file.txt", ContentFile(b"source"))

    result = migrate_storage_objects(source, InMemoryStorage(), MigrationOptions())

    assert result.copied == 0
    assert len(result.errors) == 1
    assert "size unsupported" in result.errors[0].message
    assert source.exists("file.txt")


def test_failure_in_one_file_does_not_stop_following_files():
    source = SelectiveOpenFailureStorage()
    source.save("broken.txt", ContentFile(b"broken"))
    source.save("working.txt", ContentFile(b"working"))
    destination = InMemoryStorage()

    result = migrate_storage_objects(source, destination, MigrationOptions())

    assert result.discovered == 2
    assert result.copied == 1
    assert len(result.errors) == 1
    assert result.errors[0].object_name == "broken.txt"
    assert destination.exists("working.txt")
