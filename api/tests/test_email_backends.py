from pathlib import Path

from django.core import mail
from django.core.mail import EmailMultiAlternatives
from django.test import SimpleTestCase, override_settings

from api import settings as project_settings


class AnymailConfigurationTests(SimpleTestCase):
    def test_environment_example_preserves_resend_variables(self):
        env_example = Path(project_settings.BASE_DIR, ".env.example").read_text()

        self.assertIn("RESEND_API_KEY=", env_example)
        self.assertIn("RESEND_FROM_EMAIL=", env_example)

    def test_configures_resend_key_and_default_sender(self):
        self.assertEqual(project_settings.ANYMAIL["RESEND_API_KEY"], project_settings.RESEND_API_KEY)
        self.assertEqual(project_settings.DEFAULT_FROM_EMAIL, "nao-responda@base.com.br")

    def test_selects_anymail_test_backend_in_project_settings(self):
        self.assertEqual(project_settings.EMAIL_BACKEND, "anymail.backends.test.EmailBackend")

    @override_settings(EMAIL_BACKEND="anymail.backends.test.EmailBackend")
    def test_test_backend_captures_rich_django_email_without_network(self):
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

        self.assertEqual(message.send(), 1)

        sent = mail.outbox[0]
        self.assertEqual(sent.from_email, "API <api@example.com>")
        self.assertEqual(sent.to, ["to@example.com"])
        self.assertEqual(sent.cc, ["cc@example.com"])
        self.assertEqual(sent.bcc, ["bcc@example.com"])
        self.assertEqual(sent.reply_to, ["reply@example.com"])
        self.assertEqual(sent.extra_headers["X-Request-ID"], "request-123")
        self.assertEqual(sent.alternatives[0].content, "<h1>Welcome</h1>")
        self.assertEqual(sent.attachments[0][0], "report.bin")
