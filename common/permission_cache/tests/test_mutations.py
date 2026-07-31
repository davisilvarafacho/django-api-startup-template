import re
from pathlib import Path
from unittest.mock import call, patch

from django.contrib.auth.models import Permission
from django.db import transaction

import pytest
from guardian.ctypes import get_content_type

from apps.organizacoes.models import Organizacao, Papel, Vinculo
from apps.usuarios.factories import UsuarioFactory
from apps.usuarios.models import Usuario
from common.permission_cache.mutations import (
    bulk_create_memberships,
    bulk_create_permissions,
    bulk_update_memberships,
    bulk_update_permissions,
    guardian_assign_to_many,
    guardian_bulk_assign,
    guardian_bulk_remove,
    guardian_remove_from_many,
    update_memberships,
    update_user_authorization_state,
)

pytestmark = pytest.mark.django_db(transaction=True)


def create_organization(slug: str) -> Organizacao:
    return Organizacao.objects.create(nome=slug.title(), slug=slug)


def assert_object_bump(bump, *organizations: Organizacao) -> None:
    scopes = tuple(sorted(f"guardian:object:{get_content_type(organization).pk}:{organization.pk}" for organization in organizations))
    bump.assert_called_once_with(scopes, database_alias="default", layer="guardian")


def test_bulk_create_memberships_invalidates_every_user_after_commit():
    first = UsuarioFactory()
    second = UsuarioFactory()
    organization = create_organization("acme")
    memberships = [
        Vinculo(usuario=first, organizacao=organization, papel=Papel.MEMBRO),
        Vinculo(usuario=second, organizacao=organization, papel=Papel.GESTOR),
    ]

    with patch("common.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            created = bulk_create_memberships(memberships)
            bump.assert_not_called()

        assert {row.usuario_id for row in created} == {first.pk, second.pk}
        bump.assert_called_once_with(
            (f"tenant:user:{first.pk}", f"tenant:user:{second.pk}"),
            database_alias="default",
            layer="tenant",
        )


def test_bulk_update_memberships_invalidates_old_and_new_users():
    first = UsuarioFactory()
    second = UsuarioFactory()
    replacement = UsuarioFactory()
    first_membership = Vinculo.objects.create(
        usuario=first,
        organizacao=create_organization("bulk-update-first"),
        papel=Papel.MEMBRO,
    )
    second_membership = Vinculo.objects.create(
        usuario=second,
        organizacao=create_organization("bulk-update-second"),
        papel=Papel.MEMBRO,
    )
    rows = [first_membership, second_membership]
    for row in rows:
        row.usuario = replacement

    with patch("common.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            updated = bulk_update_memberships(rows, ["usuario"])
            bump.assert_not_called()

        assert updated == 2
        bump.assert_called_once_with(
            tuple(sorted(f"tenant:user:{user.pk}" for user in (first, second, replacement))),
            database_alias="default",
            layer="tenant",
        )


def test_update_memberships_reads_users_before_queryset_update():
    first = UsuarioFactory()
    second = UsuarioFactory()
    organization = create_organization("queryset-update")
    Vinculo.objects.create(usuario=first, organizacao=organization, papel=Papel.MEMBRO)
    Vinculo.objects.create(usuario=second, organizacao=organization, papel=Papel.MEMBRO)

    with patch("common.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            updated = update_memberships(Vinculo.objects.filter(organizacao=organization), papel=Papel.GESTOR)
            bump.assert_not_called()

        assert updated == 2
        assert set(Vinculo.objects.filter(organizacao=organization).values_list("papel", flat=True)) == {Papel.GESTOR}
        bump.assert_called_once_with(
            (f"tenant:user:{first.pk}", f"tenant:user:{second.pk}"),
            database_alias="default",
            layer="tenant",
        )


def test_guardian_bulk_assign_and_remove_invalidate_every_object():
    user = UsuarioFactory()
    first = create_organization("guardian-bulk-first")
    second = create_organization("guardian-bulk-second")

    with patch("common.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            assigned = guardian_bulk_assign("view_organizacao", user, [first, second])
            bump.assert_not_called()
        assert {str(row.object_pk) for row in assigned} == {str(first.pk), str(second.pk)}
        assert_object_bump(bump, first, second)

    with patch("common.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            removed = guardian_bulk_remove("view_organizacao", user, [first, second])
            bump.assert_not_called()
        assert removed[0] == 2
        assert sum(removed[1].values()) == 2
        assert_object_bump(bump, first, second)


def test_guardian_assign_to_many_and_remove_from_many_invalidate_once():
    first = UsuarioFactory()
    second = UsuarioFactory()
    organization = create_organization("guardian-many")

    with patch("common.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            assigned = guardian_assign_to_many("view_organizacao", [first, second], organization)
            bump.assert_not_called()
        assert len(assigned) == 2
        assert_object_bump(bump, organization)

    with patch("common.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            removed = guardian_remove_from_many("view_organizacao", [first, second], organization)
            bump.assert_not_called()
        assert removed[0] == 2
        assert sum(removed[1].values()) == 2
        assert_object_bump(bump, organization)


def test_permission_bulk_wrappers_bump_global_layers():
    content_type = get_content_type(create_organization("permission-bulk"))
    permissions = [
        Permission(content_type=content_type, codename="bulk_first", name="Bulk first"),
        Permission(content_type=content_type, codename="bulk_second", name="Bulk second"),
    ]
    expected_bumps = [
        call(("django:global",), database_alias="default", layer="django"),
        call(("guardian:global",), database_alias="default", layer="guardian"),
    ]

    with patch("common.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            created = bulk_create_permissions(permissions)
            bump.assert_not_called()
        assert {permission.codename for permission in created} == {"bulk_first", "bulk_second"}
        assert bump.call_args_list == expected_bumps

    for permission in created:
        permission.name = f"Updated {permission.codename}"
    with patch("common.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            updated = bulk_update_permissions(created, ["name"])
            bump.assert_not_called()
        assert updated == 2
        assert bump.call_args_list == expected_bumps


def test_bulk_user_state_update_bumps_all_layers():
    first = UsuarioFactory()
    second = UsuarioFactory()
    expected = [
        call(
            tuple(f"{layer}:user:{user.pk}" for user in (first, second)),
            database_alias="default",
            layer=layer,
        )
        for layer in ("django", "tenant", "guardian")
    ]

    with patch("common.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with transaction.atomic():
            updated = update_user_authorization_state(Usuario.objects.filter(pk__in=[first.pk, second.pk]), is_active=False)
            bump.assert_not_called()

        assert updated == 2
        assert bump.call_args_list == expected
        assert not Usuario.objects.filter(pk__in=[first.pk, second.pk], is_active=True).exists()


def test_bulk_user_state_update_rejects_non_authorization_fields(django_assert_num_queries):
    user = UsuarioFactory()

    with patch("common.permission_cache.invalidation.bump_epoch_scopes") as bump:
        with django_assert_num_queries(0):
            with pytest.raises(ValueError, match="first_name"):
                update_user_authorization_state(Usuario.objects.filter(pk=user.pk), first_name="x")
        bump.assert_not_called()


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DIRECT_AUTHORIZATION_WRITES = (
    (
        "membership bulk mutation",
        re.compile(r"\bVinculo\s*\.\s*(?:objects|_base_manager|_default_manager).*?\.\s*(?:bulk_create|bulk_update|update)\s*\(", re.DOTALL),
    ),
    (
        "guardian object-permission bulk mutation",
        re.compile(
            r"(?:\b(?:UserObjectPermission|GroupObjectPermission)\s*\.\s*(?:objects|_base_manager|_default_manager)|"
            r"get_(?:user|group)_obj_perms_model\s*\([^)]*\)\s*\.\s*(?:objects|_base_manager|_default_manager))"
            r".*?\.\s*(?:bulk_create|bulk_update|update)\s*\(",
            re.DOTALL,
        ),
    ),
    (
        "permission bulk mutation",
        re.compile(r"\bPermission\s*\.\s*(?:objects|_base_manager|_default_manager).*?\.\s*(?:bulk_create|bulk_update|update)\s*\(", re.DOTALL),
    ),
    (
        "user authorization-state update",
        re.compile(
            r"\bUsuario\s*\.\s*(?:objects|_base_manager|_default_manager).*?\.\s*update\s*\([^)]*\b(?:is_active|is_superuser)\s*=",
            re.DOTALL,
        ),
    ),
)
DIRECT_AUTHORIZATION_WRITE_ALLOWLIST: set[tuple[str, str]] = set()


def production_python_files() -> list[Path]:
    files = []
    for path in REPOSITORY_ROOT.rglob("*.py"):
        relative = path.relative_to(REPOSITORY_ROOT)
        if any(part in {"migrations", "tests", ".venv", ".worktrees"} for part in relative.parts):
            continue
        if relative == Path("common/permission_cache/mutations.py"):
            continue
        files.append(path)
    return files


@pytest.mark.parametrize(("write_name", "pattern"), DIRECT_AUTHORIZATION_WRITES)
def test_production_code_has_no_direct_bulk_authorization_writes(write_name, pattern):
    violations = []
    for path in production_python_files():
        relative = str(path.relative_to(REPOSITORY_ROOT))
        if pattern.search(path.read_text(encoding="utf-8")) and (write_name, relative) not in DIRECT_AUTHORIZATION_WRITE_ALLOWLIST:
            violations.append(relative)

    assert violations == [], f"Direct {write_name} must use common.permission_cache.mutations: {violations}"
