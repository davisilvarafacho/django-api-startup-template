from datetime import timedelta
from io import StringIO
from uuid import uuid4

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

import pytest
from celery.schedules import crontab

from apps.api.autenticacao.models import AuthToken, TokenType
from apps.api.autenticacao.tasks import cleanup_expired_tokens as cleanup_expired_tokens_task
from apps.api.autenticacao.token_cleanup import cleanup_expired_tokens
from apps.organizacoes.models import Organizacao, Vinculo


@pytest.fixture
def token_factory(usuario):
    def create_token(*, type, expiry):
        # API key é o único tipo com campos obrigatórios próprios; os demais
        # nascem só com responsável e tipo.
        extras = {}
        if type == TokenType.API_KEY:
            organizacao = Organizacao.objects.create(nome="Org", slug=f"org-cleanup-{uuid4().hex[:8]}")
            Vinculo.objects.create(usuario=usuario, organizacao=organizacao)
            extras = {"created_by": usuario, "name": "Integração", "organization": organizacao}

        # O manager trata `expiry` como relativo (`now() + delta`); aqui os testes
        # precisam de instantes absolutos no passado, então gravamos depois.
        token, _ = AuthToken.objects.create(user=usuario, type=type, expiry=None, **extras)
        if expiry is not None:
            token.expiry = expiry
            token.save(update_fields=["expiry"])
        return token

    return create_token


@pytest.mark.django_db
def test_cleanup_removes_ephemeral_tokens_and_preserves_api_keys(token_factory):
    now = timezone.now()
    pre_auth = token_factory(type=TokenType.PRE_AUTH, expiry=now - timedelta(seconds=1))
    reset = token_factory(type=TokenType.RESET_PASSWORD, expiry=now - timedelta(seconds=1))
    api_key = token_factory(type=TokenType.API_KEY, expiry=now - timedelta(days=365))

    result = cleanup_expired_tokens(now=now, batch_size=50, session_retention=timedelta(days=90))

    assert result.examined[TokenType.PRE_AUTH] == 1
    assert result.examined[TokenType.RESET_PASSWORD] == 1
    assert result.deleted[TokenType.PRE_AUTH] == 1
    assert result.deleted[TokenType.RESET_PASSWORD] == 1
    assert not AuthToken.objects.filter(pk=pre_auth.pk).exists()
    assert not AuthToken.objects.filter(pk=reset.pk).exists()
    assert AuthToken.objects.filter(pk=api_key.pk).exists()


@pytest.mark.django_db
def test_cleanup_applies_session_retention_and_preserves_non_expiring_tokens(token_factory):
    now = timezone.now()
    kept = token_factory(type=TokenType.TOKEN, expiry=now - timedelta(days=89))
    removed = token_factory(type=TokenType.TOKEN, expiry=now - timedelta(days=90))
    never_expires = token_factory(type=TokenType.TOKEN, expiry=None)

    result = cleanup_expired_tokens(now=now, batch_size=50, session_retention=timedelta(days=90))

    assert result.examined[TokenType.TOKEN] == 1
    assert result.deleted[TokenType.TOKEN] == 1
    assert AuthToken.objects.filter(pk=kept.pk).exists()
    assert not AuthToken.objects.filter(pk=removed.pk).exists()
    assert AuthToken.objects.filter(pk=never_expires.pk).exists()


@pytest.mark.django_db
def test_cleanup_dry_run_reports_candidates_without_deleting(token_factory):
    now = timezone.now()
    token = token_factory(type=TokenType.PRE_AUTH, expiry=now - timedelta(seconds=1))

    result = cleanup_expired_tokens(now=now, batch_size=50, session_retention=timedelta(days=90), dry_run=True)

    assert result.examined[TokenType.PRE_AUTH] == 1
    assert result.deleted[TokenType.PRE_AUTH] == 0
    assert AuthToken.objects.filter(pk=token.pk).exists()


@pytest.mark.django_db
def test_cleanup_deletes_all_candidates_in_bounded_batches(token_factory):
    now = timezone.now()
    tokens = [token_factory(type=TokenType.PRE_AUTH, expiry=now - timedelta(seconds=1)) for _ in range(5)]

    result = cleanup_expired_tokens(now=now, batch_size=2, session_retention=timedelta(days=90))

    assert result.examined[TokenType.PRE_AUTH] == 5
    assert result.deleted[TokenType.PRE_AUTH] == 5
    assert not AuthToken.objects.filter(pk__in=[token.pk for token in tokens]).exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("batch_size", "session_retention", "message"),
    [
        (0, timedelta(days=90), "batch_size deve ser maior que zero"),
        (50, timedelta(days=-1), "session_retention não pode ser negativa"),
    ],
)
def test_cleanup_rejects_invalid_parameters_without_deleting_tokens(token_factory, batch_size, session_retention, message):
    now = timezone.now()
    token = token_factory(type=TokenType.PRE_AUTH, expiry=now - timedelta(seconds=1))

    with pytest.raises(ValueError, match=message):
        cleanup_expired_tokens(now=now, batch_size=batch_size, session_retention=session_retention)

    assert AuthToken.objects.filter(pk=token.pk).exists()


@pytest.mark.django_db
def test_cleanup_command_converts_arguments_and_supports_dry_run(token_factory):
    now = timezone.now()
    token = token_factory(type=TokenType.PRE_AUTH, expiry=now - timedelta(seconds=1))
    output = StringIO()

    call_command("cleanup_expired_auth_tokens", "--dry-run", "--batch-size", "2", "--session-retention-days", "30", stdout=output)

    assert AuthToken.objects.filter(pk=token.pk).exists()
    assert "PRE_AUTH" in output.getvalue()
    assert "examinados=1" in output.getvalue()
    assert "removidos=0" in output.getvalue()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("option", "value", "message"),
    [
        ("--batch-size", "0", "batch_size deve ser maior que zero"),
        ("--session-retention-days", "-1", "session_retention não pode ser negativa"),
    ],
)
def test_cleanup_command_rejects_invalid_parameters_without_deleting_tokens(token_factory, option, value, message):
    now = timezone.now()
    token = token_factory(type=TokenType.PRE_AUTH, expiry=now - timedelta(seconds=1))

    with pytest.raises(CommandError, match=message):
        call_command("cleanup_expired_auth_tokens", option, value)

    assert AuthToken.objects.filter(pk=token.pk).exists()


@pytest.mark.django_db
def test_cleanup_task_reuses_service_with_configured_retention(token_factory):
    now = timezone.now()
    token = token_factory(type=TokenType.PRE_AUTH, expiry=now - timedelta(seconds=1))

    result = cleanup_expired_tokens_task.run()

    assert result is None
    assert not AuthToken.objects.filter(pk=token.pk).exists()
    assert cleanup_expired_tokens_task.name == "autenticacao.cleanup_expired_tokens"
    assert cleanup_expired_tokens_task.ignore_result is True
    assert cleanup_expired_tokens_task.autoretry_for
    assert cleanup_expired_tokens_task.retry_backoff is True
    assert cleanup_expired_tokens_task.retry_kwargs == {"max_retries": 3}


def test_cleanup_beat_runs_at_midnight_in_project_timezone():
    entry = settings.CELERY_BEAT_SCHEDULE["cleanup-expired-auth-tokens"]

    assert entry["task"] == "autenticacao.cleanup_expired_tokens"
    assert isinstance(entry["schedule"], crontab)
    assert entry["schedule"].hour == {0}
    assert entry["schedule"].minute == {0}
    assert settings.CELERY_TIMEZONE == settings.TIME_ZONE == "America/Sao_Paulo"
    assert settings.AUTH_TOKEN_SESSION_RETENTION_DAYS == 90
