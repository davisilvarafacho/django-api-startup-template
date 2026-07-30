# Generic Django Storage Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a safe management command that copies or moves every object from one Django storage backend to another.

**Architecture:** A framework-independent service resolves storage identifiers, walks the source recursively, maps prefixes, copies objects, verifies names and sizes, and returns structured counters and errors. A thin Django management command owns argument parsing, human-readable progress, summaries, and nonzero exit behavior.

**Tech Stack:** Python 3.10+, Django 5.2 storage API, pytest 9, pytest-django, Ruff.

## Global Constraints

- Source and destination accept either a `settings.STORAGES` alias or a fully qualified zero-argument storage class.
- Storage aliases are the supported mechanism when constructor options are required.
- Object names are preserved unless `--source-prefix` and/or `--destination-prefix` changes their mapping.
- Existing destination objects are skipped by default.
- `--overwrite` explicitly deletes an existing destination object before saving its replacement and is not atomic.
- Source objects remain untouched by default.
- `--remove-on-success` removes only objects copied and size-verified during the current execution.
- Objects skipped because they already exist are never removed from the source.
- `--dry-run` may list and inspect existence but never opens source objects or calls `save()` or `delete()`.
- One object failure does not stop migration of other discoverable objects.
- Any object or discovery error produces a final nonzero command status.
- Backends must provide functional `listdir()` and `size()` implementations.
- No test contacts Backblaze or another external service.

---

## File structure

| File | Responsibility |
| --- | --- |
| `apps/api/core/storage_migration.py` | Storage resolution, safe POSIX path mapping, recursive discovery, copy/move verification, result types. |
| `apps/api/core/management/commands/migrate_storage.py` | CLI arguments, progress rendering, summary, and command exit status. |
| `apps/api/core/tests/test_storage_migration.py` | Unit coverage of traversal, prefixes, collision policy, dry-run, verification, removal, and failure isolation. |
| `apps/api/core/tests/test_migrate_storage_command.py` | Storage identifier resolution and management-command behavior. |
| `README.md` | Alias configuration, operational examples, and destructive-operation warnings. |

### Task 1: Build recursive discovery and verified copy

**Files:**

- Create: `apps/api/core/storage_migration.py`
- Create: `apps/api/core/tests/test_storage_migration.py`

**Interfaces:**

- Produces: `MigrationOptions`, `MigrationIssue`, `MigrationResult`, and `migrate_storage_objects(source, destination, options, on_event=None)`.
- Callback signature: `on_event(action: str, source_name: str, destination_name: str) -> None`.
- Action names produced in this task: `copied` and `error`.

- [ ] **Step 1: Write failing tests for recursive copying and prefix mapping**

Create `apps/api/core/tests/test_storage_migration.py` with:

```python
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
```

- [ ] **Step 2: Run the focused tests and verify the red state**

Run:

```bash
uv run pytest apps/api/core/tests/test_storage_migration.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'apps.api.core.storage_migration'`.

- [ ] **Step 3: Implement result types, safe paths, traversal, and verified copying**

Create `apps/api/core/storage_migration.py`:

```python
"""Copy or move objects between Django storage backends."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from django.core.files.storage import Storage


EventCallback = Callable[[str, str, str], None]


@dataclass(frozen=True)
class MigrationOptions:
    source_prefix: str = ""
    destination_prefix: str = ""
    overwrite: bool = False
    remove_on_success: bool = False
    dry_run: bool = False


@dataclass(frozen=True)
class MigrationIssue:
    object_name: str
    message: str


@dataclass
class MigrationResult:
    discovered: int = 0
    copied: int = 0
    skipped: int = 0
    removed: int = 0
    errors: list[MigrationIssue] = field(default_factory=list)


def _normalise_path(value: str, *, label: str) -> str:
    raw_value = str(value or "")
    path = PurePosixPath(raw_value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{label} must be a relative path without '..'.")
    return "/".join(part for part in path.parts if part not in {"", "."})


def _join_child(directory: str, child: str) -> str:
    normalised_child = _normalise_path(child, label="Storage listdir entry")
    if not normalised_child or "/" in normalised_child:
        raise ValueError(f"Storage listdir returned an invalid child name: {child!r}.")
    return f"{directory}/{normalised_child}" if directory else normalised_child


def _iter_storage_files(
    storage: Storage,
    directory: str,
    result: MigrationResult,
    on_event: EventCallback | None,
) -> Iterator[str]:
    visited: set[str] = set()

    def walk(current_directory: str) -> Iterator[str]:
        if current_directory in visited:
            issue = MigrationIssue(current_directory or ".", "Storage listdir returned a directory cycle.")
            result.errors.append(issue)
            if on_event:
                on_event("error", issue.object_name, "")
            return
        visited.add(current_directory)

        try:
            directories, files = storage.listdir(current_directory)
        except Exception as exc:
            issue = MigrationIssue(current_directory or ".", f"Could not list directory: {exc}")
            result.errors.append(issue)
            if on_event:
                on_event("error", issue.object_name, "")
            return

        for file_name in sorted(files):
            try:
                yield _join_child(current_directory, file_name)
            except ValueError as exc:
                issue = MigrationIssue(str(file_name), str(exc))
                result.errors.append(issue)
                if on_event:
                    on_event("error", issue.object_name, "")

        for directory_name in sorted(directories):
            try:
                child_directory = _join_child(current_directory, directory_name)
            except ValueError as exc:
                issue = MigrationIssue(str(directory_name), str(exc))
                result.errors.append(issue)
                if on_event:
                    on_event("error", issue.object_name, "")
                continue
            yield from walk(child_directory)

    yield from walk(directory)


def _destination_name(source_name: str, source_prefix: str, destination_prefix: str) -> str:
    relative_name = source_name
    if source_prefix:
        expected_prefix = f"{source_prefix}/"
        if not source_name.startswith(expected_prefix):
            raise ValueError(f"Object {source_name!r} is outside source prefix {source_prefix!r}.")
        relative_name = source_name[len(expected_prefix) :]
    destination_name = f"{destination_prefix}/{relative_name}" if destination_prefix else relative_name
    return _normalise_path(destination_name, label="Destination name")


def migrate_storage_objects(
    source: Storage,
    destination: Storage,
    options: MigrationOptions,
    on_event: EventCallback | None = None,
) -> MigrationResult:
    source_prefix = _normalise_path(options.source_prefix, label="Source prefix")
    destination_prefix = _normalise_path(options.destination_prefix, label="Destination prefix")
    result = MigrationResult()

    for source_name in _iter_storage_files(source, source_prefix, result, on_event):
        result.discovered += 1
        destination_name = _destination_name(source_name, source_prefix, destination_prefix)
        try:
            source_size = source.size(source_name)
            with source.open(source_name, "rb") as source_file:
                saved_name = destination.save(destination_name, source_file)
            if saved_name != destination_name:
                raise OSError(
                    f"Destination saved {destination_name!r} as unexpected name {saved_name!r}."
                )
            destination_size = destination.size(saved_name)
            if destination_size != source_size:
                raise OSError(
                    f"Size mismatch for {destination_name!r}: source={source_size}, "
                    f"destination={destination_size}."
                )
            result.copied += 1
            if on_event:
                on_event("copied", source_name, destination_name)
        except Exception as exc:
            issue = MigrationIssue(source_name, str(exc))
            result.errors.append(issue)
            if on_event:
                on_event("error", source_name, destination_name)

    return result
```

- [ ] **Step 4: Run the focused tests and verify the green state**

Run:

```bash
uv run pytest apps/api/core/tests/test_storage_migration.py -q
```

Expected: `2 passed`.

- [ ] **Step 5: Commit the recursive verified-copy foundation**

```bash
git add apps/api/core/storage_migration.py apps/api/core/tests/test_storage_migration.py
git commit -m "feat: add verified storage copy service"
```

### Task 2: Add collision, dry-run, move, and failure policies

**Files:**

- Modify: `apps/api/core/storage_migration.py`
- Modify: `apps/api/core/tests/test_storage_migration.py`

**Interfaces:**

- Consumes: Task 1 result types and `migrate_storage_objects`.
- Produces: complete `MigrationOptions` behavior.
- Adds callback actions: `skipped`, `copy-planned`, `remove-planned`, and `removed`.

- [ ] **Step 1: Add failing policy and failure-isolation tests**

Append to `apps/api/core/tests/test_storage_migration.py`:

```python
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
```

- [ ] **Step 2: Run the policy tests and verify the red state**

Run:

```bash
uv run pytest apps/api/core/tests/test_storage_migration.py -q
```

Expected: the new tests fail because Task 1 always saves, opens files during dry-run, and never removes sources.

- [ ] **Step 3: Implement the complete per-object policy**

Replace the body of the `for source_name ...` loop in `migrate_storage_objects` with:

```python
    for source_name in _iter_storage_files(source, source_prefix, result, on_event):
        result.discovered += 1
        destination_name = _destination_name(source_name, source_prefix, destination_prefix)
        try:
            destination_exists = destination.exists(destination_name)
            if destination_exists and not options.overwrite:
                result.skipped += 1
                if on_event:
                    on_event("skipped", source_name, destination_name)
                continue

            if options.dry_run:
                result.copied += 1
                if on_event:
                    on_event("copy-planned", source_name, destination_name)
                if options.remove_on_success:
                    result.removed += 1
                    if on_event:
                        on_event("remove-planned", source_name, destination_name)
                continue

            if destination_exists:
                destination.delete(destination_name)

            source_size = source.size(source_name)
            with source.open(source_name, "rb") as source_file:
                saved_name = destination.save(destination_name, source_file)
            if saved_name != destination_name:
                raise OSError(
                    f"Destination saved {destination_name!r} as unexpected name {saved_name!r}."
                )
            destination_size = destination.size(saved_name)
            if destination_size != source_size:
                raise OSError(
                    f"Size mismatch for {destination_name!r}: source={source_size}, "
                    f"destination={destination_size}."
                )

            result.copied += 1
            if on_event:
                on_event("copied", source_name, destination_name)

            if options.remove_on_success:
                source.delete(source_name)
                result.removed += 1
                if on_event:
                    on_event("removed", source_name, destination_name)
        except Exception as exc:
            issue = MigrationIssue(source_name, str(exc))
            result.errors.append(issue)
            if on_event:
                on_event("error", source_name, destination_name)
```

- [ ] **Step 4: Run service tests and lint**

Run:

```bash
uv run pytest apps/api/core/tests/test_storage_migration.py -q
uv run ruff check apps/api/core/storage_migration.py apps/api/core/tests/test_storage_migration.py
```

Expected: all storage migration tests pass and Ruff reports `All checks passed!`.

- [ ] **Step 5: Commit migration policies**

```bash
git add apps/api/core/storage_migration.py apps/api/core/tests/test_storage_migration.py
git commit -m "feat: add safe storage migration policies"
```

### Task 3: Expose storage resolution and the management command

**Files:**

- Modify: `apps/api/core/storage_migration.py`
- Create: `apps/api/core/management/commands/migrate_storage.py`
- Create: `apps/api/core/tests/test_migrate_storage_command.py`

**Interfaces:**

- Produces: `resolve_storage(identifier: str) -> Storage`.
- Consumes: `MigrationOptions`, `MigrationResult`, and `migrate_storage_objects`.
- Command: `manage.py migrate_storage --source IDENTIFIER --destination IDENTIFIER [options]`.

- [ ] **Step 1: Add failing resolver and command tests**

Create `apps/api/core/tests/test_migrate_storage_command.py`:

```python
from io import StringIO
from unittest.mock import patch

import pytest

from django.core.files.base import ContentFile
from django.core.files.storage import InMemoryStorage
from django.core.management import CommandError, call_command
from django.test import override_settings

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
```

- [ ] **Step 2: Run command tests and verify the red state**

Run:

```bash
uv run pytest apps/api/core/tests/test_migrate_storage_command.py -q
```

Expected: collection fails because `resolve_storage` and the `migrate_storage` command do not exist.

- [ ] **Step 3: Add alias-or-class resolution**

Add these imports to `apps/api/core/storage_migration.py`:

```python
from django.conf import settings
from django.core.files.storage import Storage, storages
from django.utils.module_loading import import_string
```

Replace the existing single `Storage` import, then add:

```python
def resolve_storage(identifier: str) -> Storage:
    if identifier in settings.STORAGES:
        return storages[identifier]

    try:
        storage_class = import_string(identifier)
    except (ImportError, AttributeError) as exc:
        raise ValueError(
            f"{identifier!r} is neither a STORAGES alias nor an importable storage class."
        ) from exc

    if not isinstance(storage_class, type) or not issubclass(storage_class, Storage):
        raise ValueError(f"{identifier!r} does not identify a Django Storage class.")

    try:
        return storage_class()
    except Exception as exc:
        raise ValueError(
            f"Could not instantiate storage class {identifier!r} without arguments: {exc}"
        ) from exc
```

- [ ] **Step 4: Implement the management command**

Create `apps/api/core/management/commands/migrate_storage.py`:

```python
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
        parser.add_argument("--source", required=True, help="STORAGES alias or storage class path.")
        parser.add_argument("--destination", required=True, help="STORAGES alias or storage class path.")
        parser.add_argument("--source-prefix", default="", help="Only migrate this source subtree.")
        parser.add_argument("--destination-prefix", default="", help="Prefix added to destination names.")
        parser.add_argument("--overwrite", action="store_true", help="Delete matching destination objects before copying.")
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
```

- [ ] **Step 5: Run command tests and the complete focused suite**

Run:

```bash
uv run pytest apps/api/core/tests/test_migrate_storage_command.py apps/api/core/tests/test_storage_migration.py -q
uv run ruff check apps/api/core/storage_migration.py apps/api/core/management/commands/migrate_storage.py apps/api/core/tests/test_storage_migration.py apps/api/core/tests/test_migrate_storage_command.py
```

Expected: all focused tests pass and Ruff reports `All checks passed!`.

- [ ] **Step 6: Commit the CLI**

```bash
git add apps/api/core/storage_migration.py apps/api/core/management/commands/migrate_storage.py apps/api/core/tests/test_migrate_storage_command.py
git commit -m "feat: add generic storage migration command"
```

### Task 4: Document operation and verify the repository

**Files:**

- Modify: `README.md`

**Interfaces:**

- Consumes: the final `migrate_storage` command from Task 3.
- Produces: copy-pasteable setup and operational safety guidance.

- [ ] **Step 1: Add storage aliases and command examples to the README**

Append this subsection after “Arquivos no Backblaze B2”:

````markdown
### Migrar arquivos entre storages

O comando `migrate_storage` copia qualquer storage Django para outro. Para
backends que recebem opções, configure aliases em `STORAGES`:

```python
STORAGES = {
    # ... aliases existentes ...
    "legacy_media": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": "/dados/media-legada"},
    },
    "backblaze": {
        "BACKEND": "apps.api.core.b2_storage.BackblazeB2Storage",
    },
}
```

Simule a migração antes de gravar:

```bash
uv run python manage.py migrate_storage \
  --source legacy_media \
  --destination backblaze \
  --dry-run
```

Copie uma subárvore e troque o prefixo:

```bash
uv run python manage.py migrate_storage \
  --source legacy_media \
  --destination backblaze \
  --source-prefix uploads \
  --destination-prefix media
```

Objetos existentes são ignorados. Use `--overwrite` para substituí-los ou
`--remove-on-success` para apagar cada origem somente após confirmar nome e
tamanho no destino.

> `--overwrite` pode apagar o objeto anterior antes do upload e não é atômico
> para todos os backends. Faça backup e use `--dry-run` primeiro. Não use aliases
> diferentes que apontem para a mesma localização física.
````

- [ ] **Step 2: Run focused and full verification**

Run:

```bash
uv run pytest apps/api/core/tests/test_migrate_storage_command.py apps/api/core/tests/test_storage_migration.py apps/api/core/tests/test_b2_storage.py -q
uv run ruff check .
uv run pytest -q
uv run python manage.py check
```

Expected: all focused and repository tests pass, Ruff reports no errors, and Django reports `System check identified no issues`.

- [ ] **Step 3: Commit documentation**

```bash
git add README.md
git commit -m "docs: explain storage migration workflow"
```

- [ ] **Step 4: Inspect final scope**

Run:

```bash
git status --short
git log -5 --oneline
```

Expected: only the user's pre-existing untracked files remain; the latest commits contain the service, policies, command, tests, and documentation.
