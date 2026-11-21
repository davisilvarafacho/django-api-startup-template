"""Testes do handler que garante o plano gratuito após as migrations."""

from django.apps import apps as django_apps
from django.conf import settings
from django.db import connection

import pytest

from apps.billing.handlers import ensure_free_plan
from apps.billing.models import Plan


def _apagar_plano_gratuito():
    """Remove fisicamente o gratuito semeado pelo `post_migrate` da suíte."""
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM billing_plan WHERE is_free")


@pytest.mark.django_db
def test_ensure_free_plan_is_idempotent(settings):
    settings.BILLING_FREE_PLAN_MEMBER_LIMIT = 3
    sender = django_apps.get_app_config("billing")

    ensure_free_plan(sender=sender, using="default")
    ensure_free_plan(sender=sender, using="default")

    gratuitos = Plan.all_objects.filter(is_free=True)
    assert gratuitos.count() == 1
    assert gratuitos.get().member_limit == 3


@pytest.mark.django_db
def test_ensure_free_plan_creates_plan_when_missing():
    _apagar_plano_gratuito()

    ensure_free_plan(sender=django_apps.get_app_config("billing"), using="default")

    plano = Plan.objects.get(is_free=True)
    assert plano.slug == "gratuito"
    assert plano.member_limit == settings.BILLING_FREE_PLAN_MEMBER_LIMIT


@pytest.mark.django_db
def test_ensure_free_plan_ignores_other_apps():
    _apagar_plano_gratuito()

    ensure_free_plan(sender=django_apps.get_app_config("organizacoes"), using="default")

    assert not Plan.all_objects.filter(is_free=True).exists()
