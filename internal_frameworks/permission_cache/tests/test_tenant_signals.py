from unittest.mock import call, patch

from django.db import transaction

import pytest

from apps.organizacoes.models import Organizacao, Papel, Vinculo
from internal_frameworks.permission_cache.invalidation import bump_epoch_scopes
from internal_frameworks.permission_cache.resolvers.tenant import TenantAccessResolver
from internal_frameworks.permission_cache.types import TenantAccess
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db(transaction=True)


def user_scope(user):
    return f"tenant:user:{user.pk}"


def assert_user_bump(bump, *users):
    assert bump.call_args == call(
        tuple(sorted(user_scope(user) for user in users)),
        database_alias="default",
        layer="tenant",
    )


def test_membership_role_change_bumps_user_scope_after_commit():
    user = criar_usuario()
    organization = Organizacao.objects.create(nome="Acme", slug="acme")
    membership = Vinculo.objects.create(usuario=user, organizacao=organization, papel=Papel.MEMBRO)

    with patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            membership.papel = Papel.GESTOR
            membership.save(update_fields=["papel"])
            bump.assert_not_called()

        bump.assert_called_once_with(
            (f"tenant:user:{user.pk}",),
            database_alias="default",
            layer="tenant",
        )


def test_membership_create_and_delete_bump_user_scope():
    organization = Organizacao.objects.create(nome="Acme", slug="acme")
    created_user = criar_usuario()

    with patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            Vinculo.objects.create(usuario=created_user, organizacao=organization, papel=Papel.MEMBRO)
            bump.assert_not_called()
        assert_user_bump(bump, created_user)

    deleted_user = criar_usuario()
    membership = Vinculo.objects.create(usuario=deleted_user, organizacao=organization, papel=Papel.MEMBRO)
    with patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            membership.delete()
            bump.assert_not_called()
        assert_user_bump(bump, deleted_user)


def test_membership_user_change_bumps_old_and_new_users():
    old_user = criar_usuario()
    new_user = criar_usuario()
    organization = Organizacao.objects.create(nome="Acme", slug="acme")
    membership = Vinculo.objects.create(usuario=old_user, organizacao=organization, papel=Papel.MEMBRO)

    with patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            membership.usuario = new_user
            membership.save(update_fields=["usuario"])
            bump.assert_not_called()
        assert_user_bump(bump, old_user, new_user)


def test_membership_user_id_change_bumps_old_and_new_users():
    old_user = criar_usuario()
    new_user = criar_usuario()
    organization = Organizacao.objects.create(nome="Acme", slug="acme")
    membership = Vinculo.objects.create(usuario=old_user, organizacao=organization, papel=Papel.MEMBRO)

    with patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            membership.usuario_id = new_user.pk
            membership.save(update_fields=["usuario_id"])
            bump.assert_not_called()
        assert_user_bump(bump, old_user, new_user)


def test_membership_organization_change_keeps_user_invalidation():
    user = criar_usuario()
    old_organization = Organizacao.objects.create(nome="Acme", slug="acme")
    new_organization = Organizacao.objects.create(nome="Beta", slug="beta")
    membership = Vinculo.objects.create(usuario=user, organizacao=old_organization, papel=Papel.MEMBRO)

    with patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            membership.organizacao = new_organization
            membership.save(update_fields=["organizacao"])
            bump.assert_not_called()
        assert_user_bump(bump, user)


def test_membership_organization_id_change_keeps_user_invalidation():
    user = criar_usuario()
    old_organization = Organizacao.objects.create(nome="Acme", slug="acme")
    new_organization = Organizacao.objects.create(nome="Beta", slug="beta")
    membership = Vinculo.objects.create(usuario=user, organizacao=old_organization, papel=Papel.MEMBRO)

    with patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            membership.organizacao_id = new_organization.pk
            membership.save(update_fields=["organizacao_id"])
            bump.assert_not_called()
        assert_user_bump(bump, user)


def test_membership_active_change_invalidates_positive_and_negative_snapshots():
    user = criar_usuario()
    organization = Organizacao.objects.create(nome="Acme", slug="acme")
    membership = Vinculo.objects.create(usuario=user, organizacao=organization, papel=Papel.GESTOR)
    resolver = TenantAccessResolver()

    assert resolver.by_organization_id(user.pk, organization.pk) == TenantAccess(organization.pk, "acme", membership.pk, Papel.GESTOR)

    with patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes", wraps=bump_epoch_scopes) as bump:
        with transaction.atomic():
            membership.is_active = False
            membership.save(update_fields=["is_active"])
            bump.assert_not_called()
        assert_user_bump(bump, user)
    assert resolver.by_organization_id(user.pk, organization.pk) is None

    with patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes", wraps=bump_epoch_scopes) as bump:
        with transaction.atomic():
            membership.is_active = True
            membership.save(update_fields=["is_active"])
            bump.assert_not_called()
        assert_user_bump(bump, user)
    assert resolver.by_organization_id(user.pk, organization.pk) == TenantAccess(organization.pk, "acme", membership.pk, Papel.GESTOR)


def test_organization_slug_active_and_delete_bump_tenant_global():
    organization = Organizacao.objects.create(nome="Acme", slug="acme")
    with patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            organization.slug = "acme-renamed"
            organization.save(update_fields=["slug"])
            bump.assert_not_called()
        bump.assert_called_once_with(("tenant:global",), database_alias="default", layer="tenant")

    with patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            organization.is_active = False
            organization.save(update_fields=["is_active"])
            bump.assert_not_called()
        bump.assert_called_once_with(("tenant:global",), database_alias="default", layer="tenant")

    deleted_organization = Organizacao.objects.create(nome="Delete", slug="delete")
    with patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            deleted_organization.delete()
            bump.assert_not_called()
        bump.assert_called_once_with(("tenant:global",), database_alias="default", layer="tenant")


def test_organization_create_bumps_tenant_global():
    with patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            Organizacao.objects.create(nome="Acme", slug="acme")
            bump.assert_not_called()
        bump.assert_called_once_with(("tenant:global",), database_alias="default", layer="tenant")


def test_nested_atomic_rollback_does_not_invalidate():
    user = criar_usuario()
    organization = Organizacao.objects.create(nome="Acme", slug="acme")
    membership = Vinculo.objects.create(usuario=user, organizacao=organization, papel=Papel.MEMBRO)

    def change_role_then_rollback():
        with transaction.atomic():
            with transaction.atomic():
                membership.papel = Papel.GESTOR
                membership.save(update_fields=["papel"])
            raise RuntimeError("rollback")

    with patch("internal_frameworks.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with pytest.raises(RuntimeError, match="rollback"):
            change_role_then_rollback()
        bump.assert_not_called()
