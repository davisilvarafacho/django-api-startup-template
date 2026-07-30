# Django Anymail with Resend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the project-maintained Resend backend with django-anymail's Resend backend while preserving the existing environment-variable interface and Django email APIs.

**Architecture:** `api.settings` owns all email-provider configuration. Production and development select Anymail's Resend backend; pytest selects Anymail's network-free test backend. The project will remove its provider adapter, leaving callers on Django's `EmailMessage` and `EmailMultiAlternatives` contract.

**Tech Stack:** Python 3.12, Django 5.2, django-anymail with Resend support, uv, pytest-django.

## Global Constraints

- Add `django-anymail[resend]`; remove the direct `resend` SDK dependency.
- Preserve `RESEND_API_KEY` and `RESEND_FROM_EMAIL` exactly as the deployment interface.
- Configure `ANYMAIL["RESEND_API_KEY"]` from `RESEND_API_KEY`.
- Use `anymail.backends.resend.EmailBackend` outside tests and `anymail.backends.test.EmailBackend` while `TESTING` is true.
- Keep text, HTML alternatives, CC, BCC, reply-to, headers, attachments, and explicit `from_email` usable through Django's normal message classes.
- No webhook, inbound-email, status-tracking, dynamic-provider, or project-specific notification abstraction is in scope.

---

### Task 1: Prove the intended email configuration and test-backend contract

**Files:**
- Modify: `apps/api/core/tests/test_email_backends.py`

**Interfaces:**
- Consumes: `settings.ANYMAIL`, `settings.DEFAULT_FROM_EMAIL`, `settings.EMAIL_BACKEND`, and Django's `EmailMultiAlternatives`.
- Produces: tests proving that pytest uses Anymail's test backend and captures a rich email in `django.core.mail.outbox` without a network call.

- [ ] **Step 1: Replace the backend-adapter tests with the desired configuration tests**

```python
from api import settings as project_settings
from django.core import mail
from django.core.mail import EmailMultiAlternatives
from django.test import SimpleTestCase, override_settings


class AnymailConfigurationTests(SimpleTestCase):
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
```

- [ ] **Step 2: Run the focused tests to verify the expected RED failure**

Run: `uv run pytest apps/api/core/tests/test_email_backends.py -q`

Expected: FAIL because `api.settings.ANYMAIL` does not exist and the Anymail test backend is not installed. Django's test runner replaces `django.conf.settings.EMAIL_BACKEND` with locmem, so the configuration assertions intentionally import the project's settings module and the send test explicitly overrides its backend.

- [ ] **Step 3: Commit the failing-test checkpoint**

```bash
git add apps/api/core/tests/test_email_backends.py
git commit -m "test: specify anymail email contract"
```

### Task 2: Replace the provider adapter with Anymail configuration

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `api/settings.py`
- Delete: `apps/api/core/email_backends.py`

**Interfaces:**
- Consumes: `RESEND_API_KEY`, `RESEND_FROM_EMAIL`, and `TESTING`.
- Produces: `ANYMAIL["RESEND_API_KEY"]`, `DEFAULT_FROM_EMAIL`, and a default `EMAIL_BACKEND` that is the Resend backend outside pytest and the Anymail test backend inside pytest.

- [ ] **Step 1: Add Anymail and remove the direct SDK dependency**

Run:

```bash
uv remove resend
uv add "django-anymail[resend]"
```

Expected: `pyproject.toml` lists `django-anymail[resend]` and no longer lists `resend`; `uv.lock` is updated by uv.

- [ ] **Step 2: Configure Anymail centrally in Django settings**

Replace the existing email section with:

```python
RESEND_API_KEY = get_env_var("RESEND_API_KEY")

ANYMAIL = {
    "RESEND_API_KEY": RESEND_API_KEY,
}

DEFAULT_FROM_EMAIL = get_env_var("RESEND_FROM_EMAIL", "nao-responda@base.com.br")

EMAIL_BACKEND = "anymail.backends.test.EmailBackend" if TESTING else "anymail.backends.resend.EmailBackend"
```

Add `"anymail"` to `LIBS_APPS`, and delete `apps/api/core/email_backends.py` because Anymail owns the provider translation.

- [ ] **Step 3: Run the focused tests to verify GREEN**

Run: `uv run pytest apps/api/core/tests/test_email_backends.py -q`

Expected: PASS, with the message in `mail.outbox` and no HTTP request.

- [ ] **Step 4: Commit the implementation**

```bash
git add pyproject.toml uv.lock api/settings.py apps/api/core/email_backends.py apps/api/core/tests/test_email_backends.py
git commit -m "feat: migrate Resend email backend to anymail"
```

### Task 3: Align user-facing documentation and roadmap

**Files:**
- Modify: `README.md`
- Modify: `.env.example`
- Modify: `CHANGELOG.md`
- Modify: `docs/ROADMAP.md`

**Interfaces:**
- Consumes: the existing `RESEND_API_KEY` and `RESEND_FROM_EMAIL` documentation.
- Produces: documentation that identifies Anymail as the email abstraction, retains the same environment variables, and records the Batch 8 delivery.

- [ ] **Step 1: Write tests for the documented configuration contract**

Add to `apps/api/core/tests/test_email_backends.py`:

```python
from pathlib import Path

from django.conf import settings


def test_environment_example_preserves_resend_variables():
    env_example = Path(settings.BASE_DIR, ".env.example").read_text()

    assert "RESEND_API_KEY=" in env_example
    assert "RESEND_FROM_EMAIL=" in env_example
```

- [ ] **Step 2: Run the new documentation-contract test and verify RED only after intentionally removing neither variable**

Run: `uv run pytest apps/api/core/tests/test_email_backends.py -q`

Expected: PASS immediately, because this is a regression guard for the pre-existing deployment interface. Record that it is the explicit documentation-only exception to the TDD RED requirement; no production behavior is introduced in this task.

- [ ] **Step 3: Update documentation**

Change the README email section to say Anymail sends through Resend and keeps the documented capability list. Keep the two variable names unchanged in `.env.example`, but name the section `E-mail (Anymail + Resend)`. Add an Unreleased Changed entry to `CHANGELOG.md`. In `docs/ROADMAP.md`, mark the Anymail item complete and retain Batch 8 as in progress because its remaining items are not delivered.

- [ ] **Step 4: Run documentation and targeted code checks**

Run:

```bash
uv run pytest apps/api/core/tests/test_email_backends.py -q
uv run ruff check api/settings.py apps/api/core/tests/test_email_backends.py
uv run python manage.py check
```

Expected: all commands exit 0.

- [ ] **Step 5: Commit documentation**

```bash
git add README.md .env.example CHANGELOG.md docs/ROADMAP.md apps/api/core/tests/test_email_backends.py
git commit -m "docs: document anymail Resend integration"
```

### Task 4: Run project verification

**Files:**
- Verify only.

**Interfaces:**
- Consumes: the completed configuration, tests, lockfile, and documentation.
- Produces: evidence that the migration does not introduce lint, migration, or focused-test regressions.

- [ ] **Step 1: Check migrations are unchanged**

Run: `uv run python manage.py makemigrations --check --dry-run`

Expected: exit 0 and `No changes detected`.

- [ ] **Step 2: Run the full test suite**

Run: `uv run pytest -q`

Expected: all tests pass. If environment-dependent PostgreSQL/Redis tests cannot connect, report the exact pre-existing infrastructure blocker separately from the focused Anymail result.

- [ ] **Step 3: Review the final diff**

Run:

```bash
git diff origin/main...HEAD --check
git status --short
```

Expected: no whitespace errors and no untracked/generated files to commit.
