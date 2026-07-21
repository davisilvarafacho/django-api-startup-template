"""Email backends used by the project."""

from __future__ import annotations

from email.mime.base import MIMEBase

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.mail.backends.base import BaseEmailBackend

import resend


class ResendEmailBackend(BaseEmailBackend):
    """Send Django ``EmailMessage`` instances through Resend's API."""

    def __init__(self, api_key=None, fail_silently=False, **kwargs):
        super().__init__(fail_silently=fail_silently)
        self.api_key = api_key or getattr(settings, "RESEND_API_KEY", None)

    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        if not self.api_key:
            if self.fail_silently:
                return 0
            raise ImproperlyConfigured("RESEND_API_KEY must be configured to send email through Resend.")

        resend.api_key = self.api_key
        sent_count = 0
        for message in email_messages:
            if not message.recipients():
                continue
            try:
                resend.Emails.send(self._build_payload(message))
            except Exception:
                if not self.fail_silently:
                    raise
            else:
                sent_count += 1
        return sent_count

    def _build_payload(self, message):
        from_email = message.from_email or settings.DEFAULT_FROM_EMAIL
        if not from_email:
            raise ImproperlyConfigured("DEFAULT_FROM_EMAIL or EmailMessage.from_email must be configured.")

        payload = {
            "from": from_email,
            "to": list(message.to),
            "subject": message.subject,
            "cc": list(message.cc),
            "bcc": list(message.bcc),
        }
        if message.reply_to:
            payload["reply_to"] = list(message.reply_to)
        if message.extra_headers:
            payload["headers"] = dict(message.extra_headers)

        if message.content_subtype == "html":
            payload["html"] = message.body
        else:
            payload["text"] = message.body

        for alternative in message.alternatives:
            if alternative.mimetype == "text/html":
                payload["html"] = alternative.content

        attachments = self._build_attachments(message.attachments)
        if attachments:
            payload["attachments"] = attachments
        return {key: value for key, value in payload.items() if value not in (None, [], {})}

    @staticmethod
    def _build_attachments(attachments):
        resend_attachments = []
        for attachment in attachments:
            if isinstance(attachment, MIMEBase):
                raise ValueError("MIMEBase attachments are not supported by the Resend email backend.")

            filename, content, mimetype = attachment
            resend_attachment = {
                "filename": filename,
                "content": content if isinstance(content, str) else list(content),
            }
            if mimetype:
                resend_attachment["content_type"] = mimetype
            resend_attachments.append(resend_attachment)
        return resend_attachments
