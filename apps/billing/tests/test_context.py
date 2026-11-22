"""Testes do escopo de organização usado por código fora do ciclo de request."""

import pytest
from django_rls.context import get_active_rls_context
from django_rls.exceptions import RLSContextRequiredError

from apps.billing import context as billing_context
from apps.billing.context import organization_scope
from apps.billing.models import Subscription
from apps.organizacoes.context import CHAVE_TENANT, organizacao_atual
from apps.organizacoes.models import Organizacao


@pytest.mark.django_db
def test_organization_scope_noop_when_same_tenant_active(monkeypatch):
    organization = Organizacao.objects.create(nome="Acme", slug="acme")

    def must_not_open_context(_organization_id):
        pytest.fail("organization_scope não deveria abrir um contexto novo")

    with organizacao_atual(organization.id):
        monkeypatch.setattr(billing_context, "organizacao_atual", must_not_open_context)
        with organization_scope(organization.id):
            assert Subscription.objects.count() == 0


@pytest.mark.django_db
def test_organization_scope_opens_context_when_none_active():
    organization = Organizacao.objects.create(nome="Acme", slug="acme")

    with pytest.raises(RLSContextRequiredError):
        Subscription.objects.count()

    with organization_scope(organization.id):
        assert Subscription.objects.count() == 0

    assert get_active_rls_context().get(CHAVE_TENANT) is None
