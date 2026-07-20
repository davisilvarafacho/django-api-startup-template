from datetime import datetime, timezone
from unittest.mock import MagicMock

from django.core.files.base import ContentFile
from django.test import SimpleTestCase, override_settings

from apps.api.core.b2_storage import BackblazeB2Storage


@override_settings(
    B2_APPLICATION_KEY_ID="key-id",
    B2_APPLICATION_KEY="application-key",
    B2_BUCKET_NAME="project-files",
    B2_LOCATION="media",
    B2_PUBLIC_BASE_URL="https://cdn.example.com",
)
class BackblazeB2StorageTests(SimpleTestCase):
    def setUp(self):
        self.storage = BackblazeB2Storage()
        self.storage._bucket = MagicMock()

    def test_save_uses_location_and_content_type(self):
        content = ContentFile(b"image-data", name="avatar.png")
        content.content_type = "image/png"
        self.storage.exists = MagicMock(return_value=False)

        name = self.storage.save("users/avatar.png", content)

        self.assertEqual(name, "users/avatar.png")
        self.storage.bucket.upload_bytes.assert_called_once_with(
            data_bytes=b"image-data",
            file_name="media/users/avatar.png",
            content_type="image/png",
        )

    def test_url_uses_public_base_url_and_escapes_file_name(self):
        self.assertEqual(
            self.storage.url("users/avatar image.png"),
            "https://cdn.example.com/media/users/avatar%20image.png",
        )

    def test_get_created_time_returns_an_aware_datetime(self):
        self.storage.bucket.get_file_info_by_name.return_value.upload_timestamp = 1_700_000_000_000

        created_at = self.storage.get_created_time("users/avatar.png")

        self.assertEqual(created_at, datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc))

    def test_path_traversal_is_rejected(self):
        with self.assertRaises(ValueError):
            self.storage.exists("../private.txt")
