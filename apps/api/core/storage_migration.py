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
