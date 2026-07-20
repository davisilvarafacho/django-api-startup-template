from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.core.mail import EmailMultiAlternatives
from django.test import SimpleTestCase, override_settings

from apps.api.core.email_backends import ResendEmailBackend


@override_settings(RESEND_API_KEY="re_test", DEFAULT_FROM_EMAIL="Base <no-reply@example.com>")
class ResendEmailBackendTests(SimpleTestCase):
    @patch("apps.api.core.email_backends.resend.Emails.send")
    def test_sends_html_message_with_metadata_and_attachment(self, send):
        message = EmailMultiAlternatives(
            subject="Welcome",
            body="Plain text",
            from_email="API <api@example.com>",
            to=["to@example.com"],
            cc=["cc@example.com"],
            bcc=["bcc@example.com"],
            reply_to=["reply@example.com"],
            headers={"X-Request-ID": "request-123"},
        )
        message.attach_alternative("<h1>Welcome</h1>", "text/html")
        message.attach("report.bin", b"report", "application/octet-stream")

        sent_count = ResendEmailBackend().send_messages([message])

        self.assertEqual(sent_count, 1)
        send.assert_called_once_with(
            {
                "from": "API <api@example.com>",
                "to": ["to@example.com"],
                "subject": "Welcome",
                "cc": ["cc@example.com"],
                "bcc": ["bcc@example.com"],
                "reply_to": ["reply@example.com"],
                "headers": {"X-Request-ID": "request-123"},
                "text": "Plain text",
                "html": "<h1>Welcome</h1>",
                "attachments": [
                    {
                        "filename": "report.bin",
                        "content": [114, 101, 112, 111, 114, 116],
                        "content_type": "application/octet-stream",
                    }
                ],
            }
        )

    @override_settings(RESEND_API_KEY=None)
    def test_requires_an_api_key_unless_fail_silently_is_enabled(self):
        message = EmailMultiAlternatives("Subject", "Body", to=["to@example.com"])

        with self.assertRaises(ImproperlyConfigured):
            ResendEmailBackend().send_messages([message])

        self.assertEqual(ResendEmailBackend(fail_silently=True).send_messages([message]), 0)

    @patch("apps.api.core.email_backends.resend.Emails.send", side_effect=RuntimeError("unavailable"))
    def test_respects_fail_silently_for_provider_errors(self, send):
        message = EmailMultiAlternatives("Subject", "Body", to=["to@example.com"])

        self.assertEqual(ResendEmailBackend(fail_silently=True).send_messages([message]), 0)
        send.assert_called_once()
