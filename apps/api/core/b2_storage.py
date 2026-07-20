"""Django storage backend backed by Backblaze B2."""

from __future__ import annotations

import mimetypes
from datetime import datetime, timezone
from io import BytesIO
from pathlib import PurePosixPath
from urllib.parse import quote

from b2sdk.v2 import B2Api, B2HttpApiConfig, InMemoryAccountInfo
from b2sdk.v2.exception import B2Error, FileNotPresent
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import File
from django.core.files.storage import Storage
from django.utils.deconstruct import deconstructible


@deconstructible
class BackblazeB2Storage(Storage):
    """Store Django files in a Backblaze B2 bucket using ``b2sdk``.

    The backend accepts the same keys in ``STORAGES[...]["OPTIONS"]`` as the
    settings below. ``location`` is a prefix in the bucket, useful for keeping
    database backups separate from uploaded media.
    """

    def __init__(
        self,
        application_key_id=None,
        application_key=None,
        bucket_name=None,
        bucket_id=None,
        location=None,
        public_base_url=None,
        **kwargs,
    ):
        super().__init__()
        self.application_key_id = application_key_id or getattr(settings, "B2_APPLICATION_KEY_ID", None)
        self.application_key = application_key or getattr(settings, "B2_APPLICATION_KEY", None)
        self.bucket_name = bucket_name or getattr(settings, "B2_BUCKET_NAME", None)
        self.bucket_id = bucket_id or getattr(settings, "B2_BUCKET_ID", None)
        self.location = self._clean_location(location if location is not None else getattr(settings, "B2_LOCATION", ""))
        self.public_base_url = (
            public_base_url or getattr(settings, "B2_PUBLIC_BASE_URL", None) or getattr(settings, "B2_ENDPOINT_URL", None)
        )
        self._api = None
        self._bucket = None

        missing = [
            setting_name
            for setting_name, value in {
                "B2_APPLICATION_KEY_ID": self.application_key_id,
                "B2_APPLICATION_KEY": self.application_key,
                "B2_BUCKET_NAME": self.bucket_name,
            }.items()
            if not value
        ]
        if missing:
            raise ImproperlyConfigured(f"Backblaze B2 requires: {', '.join(missing)}.")

    @staticmethod
    def _clean_location(location):
        return str(location or "").strip("/")

    @staticmethod
    def _clean_name(name):
        return str(name).lstrip("/")

    def _full_name(self, name):
        name = self._clean_name(name)
        if ".." in PurePosixPath(name).parts:
            raise ValueError("Backblaze B2 file names cannot contain path traversal segments.")
        return f"{self.location}/{name}" if self.location else name

    def _relative_name(self, name):
        name = self._clean_name(name)
        if self.location and name.startswith(f"{self.location}/"):
            return name[len(self.location) + 1 :]
        return name

    @property
    def api(self):
        """Create and authorize the B2 client only when storage is used."""
        if self._api is None:
            self._api = B2Api(InMemoryAccountInfo(), api_config=B2HttpApiConfig())
            self._api.authorize_account("production", self.application_key_id, self.application_key)
        return self._api

    @property
    def bucket(self):
        if self._bucket is None:
            self._bucket = self.api.get_bucket_by_id(self.bucket_id) if self.bucket_id else self.api.get_bucket_by_name(self.bucket_name)
        return self._bucket

    def _open(self, name, mode="rb"):
        if "r" not in mode:
            raise ValueError("Backblaze B2 storage only supports opening files for reading.")

        full_name = self._full_name(name)
        try:
            destination = BytesIO()
            self.bucket.download_file_by_name(full_name).save(destination)
            destination.seek(0)
            return File(destination, name=self._relative_name(full_name))
        except FileNotPresent as exc:
            raise FileNotFoundError(f"File not found: {name}") from exc
        except B2Error as exc:
            raise OSError(f"Could not open B2 file '{name}'.") from exc

    def _save(self, name, content):
        full_name = self._full_name(name)
        content_type = getattr(content, "content_type", None) or mimetypes.guess_type(full_name)[0]

        try:
            if hasattr(content, "seek"):
                content.seek(0)
            data = content.read() if hasattr(content, "read") else content
            self.bucket.upload_bytes(
                data_bytes=data,
                file_name=full_name,
                content_type=content_type or "application/octet-stream",
            )
            return self._relative_name(full_name)
        except B2Error as exc:
            raise OSError(f"Could not save B2 file '{name}'.") from exc

    def delete(self, name):
        full_name = self._full_name(name)
        try:
            for file_version, _ in self.bucket.ls(full_name, latest_only=False, recursive=False):
                if file_version.file_name == full_name:
                    self.api.delete_file_version(file_version.id_, file_version.file_name)
        except FileNotPresent:
            return
        except B2Error as exc:
            raise OSError(f"Could not delete B2 file '{name}'.") from exc

    def exists(self, name):
        try:
            self.bucket.get_file_info_by_name(self._full_name(name))
        except (B2Error, FileNotPresent):
            return False
        return True

    def listdir(self, path):
        full_path = self._full_name(path).rstrip("/")
        if full_path:
            full_path += "/"

        directories, files = set(), []
        try:
            for file_version, folder_name in self.bucket.ls(full_path, latest_only=True, recursive=False):
                if folder_name:
                    directories.add(folder_name.rstrip("/").rsplit("/", 1)[-1])
                else:
                    files.append(self._relative_name(file_version.file_name).rsplit("/", 1)[-1])
        except B2Error as exc:
            raise OSError(f"Could not list B2 directory '{path}'.") from exc
        return sorted(directories), sorted(files)

    def size(self, name):
        try:
            return self.bucket.get_file_info_by_name(self._full_name(name)).size
        except FileNotPresent as exc:
            raise FileNotFoundError(f"File not found: {name}") from exc
        except B2Error as exc:
            raise OSError(f"Could not get the size of B2 file '{name}'.") from exc

    def url(self, name):
        full_name = self._full_name(name)
        if self.public_base_url:
            return f"{self.public_base_url.rstrip('/')}/{quote(full_name, safe='/')}"
        return self.bucket.get_download_url(full_name)

    def get_accessed_time(self, name):
        raise NotImplementedError("Backblaze B2 does not expose an access time.")

    def get_created_time(self, name):
        try:
            timestamp = self.bucket.get_file_info_by_name(self._full_name(name)).upload_timestamp
        except FileNotPresent as exc:
            raise FileNotFoundError(f"File not found: {name}") from exc
        except B2Error as exc:
            raise OSError(f"Could not get the creation time of B2 file '{name}'.") from exc
        return datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)

    def get_modified_time(self, name):
        return self.get_created_time(name)
