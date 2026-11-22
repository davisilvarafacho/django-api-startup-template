"""Testes do limite de membros pelo plano: suspensão, reativação e bloqueio de convites e vínculos."""

from datetime import timedelta

from django.utils import timezone

import pytest

from apps.api.core.errors import APIError
from apps.billing.members import MemberLimits
from apps.organizacoes.context import organizacao_atual
from apps.organizacoes.memberships import Vinculos
from apps.organizacoes.models import Convite, Organizacao, Papel, Vinculo
from tests.support.billing import subscribe_organization
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db

START = timezone.now() - timedelta(days=2)


def _organization(slug, *, limit=3):
    organization = Organizacao.objects.create(nome=slug, slug=slug)
    subscribe_organization(organization, member_limit=limit)
    return organization


def _membership(organization, *, age, papel=Papel.MEMBRO, is_active=True, plan_limit_suspended_at=None):
    """Cria um vínculo com `created_at` fixo: quanto maior `age`, mais recente."""
    membership = Vinculo.objects.create(
        organizacao=organization,
        usuario=criar_usuario(),
        papel=papel,
        is_active=is_active,
        plan_limit_suspended_at=plan_limit_suspended_at,
    )
    Vinculo.all_objects.filter(pk=membership.pk).update(created_at=START + timedelta(minutes=age))
    membership.refresh_from_db()
    return membership


def _reload(membership):
    membership.refresh_from_db()
    return membership


def test_apply_suspends_newest_members_above_limit():
    organization = _organization("acme-excesso")
    members = [_membership(organization, age=age) for age in range(5)]

    MemberLimits.apply(organization.pk)

    older, newer = members[:3], members[3:]
    for membership in older:
        assert _reload(membership).is_active is True
        assert _reload(membership).plan_limit_suspended_at is None
    for membership in newer:
        assert _reload(membership).is_active is False
        assert _reload(membership).plan_limit_suspended_at is not None


def test_apply_never_suspends_owner():
    organization = _organization("acme-proprietario-recente")
    older = [_membership(organization, age=age) for age in range(3)]
    owner = _membership(organization, age=3, papel=Papel.PROPRIETARIO)
    newest = _membership(organization, age=4)

    MemberLimits.apply(organization.pk)

    assert _reload(owner).is_active is True
    assert _reload(newest).is_active is False
    assert _reload(older[2]).is_active is False
    assert _reload(older[1]).is_active is True


def test_apply_reactivates_oldest_suspended_first():
    organization = _organization("acme-reativacao")
    for age in range(2):
        _membership(organization, age=age)
    # Suspenso primeiro, mas criado depois: a ordem é pela suspensão, não pela criação.
    older_suspended = _membership(
        organization,
        age=10,
        is_active=False,
        plan_limit_suspended_at=START + timedelta(hours=1),
    )
    newer_suspended = _membership(
        organization,
        age=2,
        is_active=False,
        plan_limit_suspended_at=START + timedelta(hours=2),
    )

    MemberLimits.apply(organization.pk)

    assert _reload(older_suspended).is_active is True
    assert _reload(older_suspended).plan_limit_suspended_at is None
    assert _reload(newer_suspended).is_active is False
    assert _reload(newer_suspended).plan_limit_suspended_at is not None


def test_apply_does_not_reactivate_manual_deactivation():
    organization = _organization("acme-inativo-manual")
    _membership(organization, age=0)
    _membership(organization, age=1)
    manual_inactive = _membership(organization, age=2, is_active=False)

    MemberLimits.apply(organization.pk)

    assert _reload(manual_inactive).is_active is False
    assert _reload(manual_inactive).plan_limit_suspended_at is None


def test_apply_noop_for_inactive_organization():
    organization = _organization("acme-inativa")
    members = [_membership(organization, age=age) for age in range(5)]
    Organizacao.all_objects.filter(pk=organization.pk).update(is_active=False)

    MemberLimits.apply(organization.pk)

    for membership in members:
        assert _reload(membership).is_active is True
        assert _reload(membership).plan_limit_suspended_at is None


def test_apply_is_idempotent():
    organization = _organization("acme-idempotente")
    for age in range(5):
        _membership(organization, age=age)
    moment = timezone.now()

    MemberLimits.apply(organization.pk, now=moment)
    first_state = list(Vinculo.all_objects.filter(organizacao=organization).order_by("pk").values_list("is_active", "plan_limit_suspended_at"))
    MemberLimits.apply(organization.pk, now=moment)
    second_state = list(Vinculo.all_objects.filter(organizacao=organization).order_by("pk").values_list("is_active", "plan_limit_suspended_at"))

    assert second_state == first_state


def test_create_invite_counts_pending_invites():
    organization = _organization("acme-convite-pendente")
    _membership(organization, age=0)
    _membership(organization, age=1)
    Convite.objects.create(organizacao=organization, email="pendente@example.com", papel=Papel.MEMBRO)

    with pytest.raises(APIError) as error:
        Vinculos.criar_convite(organizacao=organization, email="novo@example.com", papel=Papel.MEMBRO, convidado_por=None)

    assert error.value.code == "billing.member_limit_reached"
    assert error.value.status_code == 409
    assert error.value.context == {"limit": 3}


def test_create_invite_ignores_expired_and_accepted_invites():
    organization = _organization("acme-convite-inerte")
    _membership(organization, age=0)
    _membership(organization, age=1)
    Convite.objects.create(
        organizacao=organization,
        email="expirado@example.com",
        papel=Papel.MEMBRO,
        expira_em=timezone.now() - timedelta(days=1),
    )
    Convite.objects.create(
        organizacao=organization,
        email="aceito@example.com",
        papel=Papel.MEMBRO,
        aceito_em=timezone.now(),
    )

    invite = Vinculos.criar_convite(organizacao=organization, email="novo@example.com", papel=Papel.MEMBRO, convidado_por=None)

    assert invite.pk is not None


def test_accept_invite_at_limit_is_blocked():
    organization = _organization("acme-aceite-cheio")
    for age in range(3):
        _membership(organization, age=age)
    user = criar_usuario(email="entrante@example.com", email_verificado_em=timezone.now())
    invite = Convite.objects.create(organizacao=organization, email=user.email, papel=Papel.MEMBRO)

    with organizacao_atual(organization.pk), pytest.raises(APIError) as error:
        Vinculos.aceitar_convite(invite, user)

    assert error.value.code == "billing.member_limit_reached"
    assert Vinculo.objects.filter(organizacao=organization, usuario=user).exists() is False


def test_accept_invite_without_active_tenant_checks_capacity():
    organization = _organization("acme-aceite-sem-tenant")
    for age in range(3):
        _membership(organization, age=age)
    user = criar_usuario(email="sem-tenant@example.com", email_verificado_em=timezone.now())
    invite = Convite.objects.create(organizacao=organization, email=user.email, papel=Papel.MEMBRO)

    with pytest.raises(APIError) as error:
        Vinculos.aceitar_convite(invite, user)

    assert error.value.code == "billing.member_limit_reached"


def test_accept_invite_with_inactive_membership_skips_capacity_check():
    organization = _organization("acme-aceite-inativo")
    for age in range(3):
        _membership(organization, age=age)
    user = criar_usuario(email="inativo-convidado@example.com", email_verificado_em=timezone.now())
    inactive = Vinculo.objects.create(organizacao=organization, usuario=user, papel=Papel.MEMBRO, is_active=False)
    invite = Convite.objects.create(organizacao=organization, email=user.email, papel=Papel.MEMBRO)

    with organizacao_atual(organization.pk):
        Vinculos.aceitar_convite(invite, user)

    assert _reload(inactive).is_active is False


def test_manual_reactivation_clears_suspension_mark():
    organization = _organization("acme-reativacao-manual")
    _membership(organization, age=0)
    _membership(organization, age=1)
    suspended = _membership(
        organization,
        age=2,
        is_active=False,
        plan_limit_suspended_at=START + timedelta(hours=1),
    )

    Vinculos.atualizar_vinculo(suspended, dados={"is_active": True})

    suspended.refresh_from_db()
    assert suspended.is_active is True
    assert suspended.plan_limit_suspended_at is None


def test_manual_reactivation_at_limit_is_blocked():
    organization = _organization("acme-reativacao-cheia")
    for age in range(3):
        _membership(organization, age=age)
    inactive = _membership(organization, age=3, is_active=False)

    with pytest.raises(APIError) as error:
        Vinculos.atualizar_vinculo(inactive, dados={"is_active": True})

    assert error.value.code == "billing.member_limit_reached"
    assert _reload(inactive).is_active is False
