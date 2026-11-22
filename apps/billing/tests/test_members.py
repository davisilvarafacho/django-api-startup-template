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

INICIO = timezone.now() - timedelta(days=2)


def _organizacao(slug, *, limite=3):
    organizacao = Organizacao.objects.create(nome=slug, slug=slug)
    subscribe_organization(organizacao, member_limit=limite)
    return organizacao


def _vinculo(organizacao, *, idade, papel=Papel.MEMBRO, is_active=True, plan_limit_suspended_at=None):
    """Cria um vínculo com `created_at` fixo: quanto maior `idade`, mais recente."""
    vinculo = Vinculo.objects.create(
        organizacao=organizacao,
        usuario=criar_usuario(),
        papel=papel,
        is_active=is_active,
        plan_limit_suspended_at=plan_limit_suspended_at,
    )
    Vinculo.all_objects.filter(pk=vinculo.pk).update(created_at=INICIO + timedelta(minutes=idade))
    vinculo.refresh_from_db()
    return vinculo


def _recarregar(vinculo):
    vinculo.refresh_from_db()
    return vinculo


def test_apply_suspends_newest_members_above_limit():
    organizacao = _organizacao("acme-excesso")
    membros = [_vinculo(organizacao, idade=idade) for idade in range(5)]

    MemberLimits.apply(organizacao.pk)

    antigos, recentes = membros[:3], membros[3:]
    for vinculo in antigos:
        assert _recarregar(vinculo).is_active is True
        assert _recarregar(vinculo).plan_limit_suspended_at is None
    for vinculo in recentes:
        assert _recarregar(vinculo).is_active is False
        assert _recarregar(vinculo).plan_limit_suspended_at is not None


def test_apply_never_suspends_owner():
    organizacao = _organizacao("acme-proprietario-recente")
    antigos = [_vinculo(organizacao, idade=idade) for idade in range(3)]
    proprietario = _vinculo(organizacao, idade=3, papel=Papel.PROPRIETARIO)
    recente = _vinculo(organizacao, idade=4)

    MemberLimits.apply(organizacao.pk)

    assert _recarregar(proprietario).is_active is True
    assert _recarregar(recente).is_active is False
    assert _recarregar(antigos[2]).is_active is False
    assert _recarregar(antigos[1]).is_active is True


def test_apply_reactivates_oldest_suspended_first():
    organizacao = _organizacao("acme-reativacao")
    for idade in range(2):
        _vinculo(organizacao, idade=idade)
    # Suspenso primeiro, mas criado depois: a ordem é pela suspensão, não pela criação.
    suspenso_antigo = _vinculo(
        organizacao,
        idade=10,
        is_active=False,
        plan_limit_suspended_at=INICIO + timedelta(hours=1),
    )
    suspenso_recente = _vinculo(
        organizacao,
        idade=2,
        is_active=False,
        plan_limit_suspended_at=INICIO + timedelta(hours=2),
    )

    MemberLimits.apply(organizacao.pk)

    assert _recarregar(suspenso_antigo).is_active is True
    assert _recarregar(suspenso_antigo).plan_limit_suspended_at is None
    assert _recarregar(suspenso_recente).is_active is False
    assert _recarregar(suspenso_recente).plan_limit_suspended_at is not None


def test_apply_does_not_reactivate_manual_deactivation():
    organizacao = _organizacao("acme-inativo-manual")
    _vinculo(organizacao, idade=0)
    _vinculo(organizacao, idade=1)
    inativo_manual = _vinculo(organizacao, idade=2, is_active=False)

    MemberLimits.apply(organizacao.pk)

    assert _recarregar(inativo_manual).is_active is False
    assert _recarregar(inativo_manual).plan_limit_suspended_at is None


def test_apply_noop_for_inactive_organization():
    organizacao = _organizacao("acme-inativa")
    membros = [_vinculo(organizacao, idade=idade) for idade in range(5)]
    Organizacao.all_objects.filter(pk=organizacao.pk).update(is_active=False)

    MemberLimits.apply(organizacao.pk)

    for vinculo in membros:
        assert _recarregar(vinculo).is_active is True
        assert _recarregar(vinculo).plan_limit_suspended_at is None


def test_apply_is_idempotent():
    organizacao = _organizacao("acme-idempotente")
    for idade in range(5):
        _vinculo(organizacao, idade=idade)
    agora = timezone.now()

    MemberLimits.apply(organizacao.pk, now=agora)
    estado_primeira = list(Vinculo.all_objects.filter(organizacao=organizacao).order_by("pk").values_list("is_active", "plan_limit_suspended_at"))
    MemberLimits.apply(organizacao.pk, now=agora)
    estado_segunda = list(Vinculo.all_objects.filter(organizacao=organizacao).order_by("pk").values_list("is_active", "plan_limit_suspended_at"))

    assert estado_segunda == estado_primeira


def test_create_invite_counts_pending_invites():
    organizacao = _organizacao("acme-convite-pendente")
    _vinculo(organizacao, idade=0)
    _vinculo(organizacao, idade=1)
    Convite.objects.create(organizacao=organizacao, email="pendente@example.com", papel=Papel.MEMBRO)

    with pytest.raises(APIError) as erro:
        Vinculos.criar_convite(organizacao=organizacao, email="novo@example.com", papel=Papel.MEMBRO, convidado_por=None)

    assert erro.value.code == "billing.member_limit_reached"
    assert erro.value.status_code == 409
    assert erro.value.context == {"limit": 3}


def test_create_invite_ignores_expired_and_accepted_invites():
    organizacao = _organizacao("acme-convite-inerte")
    _vinculo(organizacao, idade=0)
    _vinculo(organizacao, idade=1)
    Convite.objects.create(
        organizacao=organizacao,
        email="expirado@example.com",
        papel=Papel.MEMBRO,
        expira_em=timezone.now() - timedelta(days=1),
    )
    Convite.objects.create(
        organizacao=organizacao,
        email="aceito@example.com",
        papel=Papel.MEMBRO,
        aceito_em=timezone.now(),
    )

    convite = Vinculos.criar_convite(organizacao=organizacao, email="novo@example.com", papel=Papel.MEMBRO, convidado_por=None)

    assert convite.pk is not None


def test_accept_invite_at_limit_is_blocked():
    organizacao = _organizacao("acme-aceite-cheio")
    for idade in range(3):
        _vinculo(organizacao, idade=idade)
    usuario = criar_usuario(email="entrante@example.com", email_verificado_em=timezone.now())
    convite = Convite.objects.create(organizacao=organizacao, email=usuario.email, papel=Papel.MEMBRO)

    with organizacao_atual(organizacao.pk), pytest.raises(APIError) as erro:
        Vinculos.aceitar_convite(convite, usuario)

    assert erro.value.code == "billing.member_limit_reached"
    assert Vinculo.objects.filter(organizacao=organizacao, usuario=usuario).exists() is False


def test_accept_invite_without_active_tenant_checks_capacity():
    organizacao = _organizacao("acme-aceite-sem-tenant")
    for idade in range(3):
        _vinculo(organizacao, idade=idade)
    usuario = criar_usuario(email="sem-tenant@example.com", email_verificado_em=timezone.now())
    convite = Convite.objects.create(organizacao=organizacao, email=usuario.email, papel=Papel.MEMBRO)

    with pytest.raises(APIError) as erro:
        Vinculos.aceitar_convite(convite, usuario)

    assert erro.value.code == "billing.member_limit_reached"


def test_accept_invite_with_inactive_membership_skips_capacity_check():
    organizacao = _organizacao("acme-aceite-inativo")
    for idade in range(3):
        _vinculo(organizacao, idade=idade)
    usuario = criar_usuario(email="inativo-convidado@example.com", email_verificado_em=timezone.now())
    inativo = Vinculo.objects.create(organizacao=organizacao, usuario=usuario, papel=Papel.MEMBRO, is_active=False)
    convite = Convite.objects.create(organizacao=organizacao, email=usuario.email, papel=Papel.MEMBRO)

    with organizacao_atual(organizacao.pk):
        Vinculos.aceitar_convite(convite, usuario)

    assert _recarregar(inativo).is_active is False


def test_manual_reactivation_clears_suspension_mark():
    organizacao = _organizacao("acme-reativacao-manual")
    _vinculo(organizacao, idade=0)
    _vinculo(organizacao, idade=1)
    suspenso = _vinculo(
        organizacao,
        idade=2,
        is_active=False,
        plan_limit_suspended_at=INICIO + timedelta(hours=1),
    )

    Vinculos.atualizar_vinculo(suspenso, dados={"is_active": True})

    suspenso.refresh_from_db()
    assert suspenso.is_active is True
    assert suspenso.plan_limit_suspended_at is None


def test_manual_reactivation_at_limit_is_blocked():
    organizacao = _organizacao("acme-reativacao-cheia")
    for idade in range(3):
        _vinculo(organizacao, idade=idade)
    inativo = _vinculo(organizacao, idade=3, is_active=False)

    with pytest.raises(APIError) as erro:
        Vinculos.atualizar_vinculo(inativo, dados={"is_active": True})

    assert erro.value.code == "billing.member_limit_reached"
    assert _recarregar(inativo).is_active is False
