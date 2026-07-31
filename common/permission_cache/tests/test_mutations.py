import ast
import copy
from pathlib import Path
from unittest.mock import call, patch

from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType, ContentTypeManager
from django.db import connections, transaction
from django.test.utils import CaptureQueriesContext

import pytest
from guardian.ctypes import get_content_type
from guardian.utils import get_user_obj_perms_model

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

REPLICA_DATABASE_ALIAS = "permission_cache_replica"
if REPLICA_DATABASE_ALIAS not in connections.databases:
    replica_database = copy.deepcopy(connections.databases["default"])
    replica_database["TEST"] = {**replica_database.get("TEST", {}), "MIRROR": "default"}
    connections.databases[REPLICA_DATABASE_ALIAS] = replica_database


def create_organization(slug: str) -> Organizacao:
    return Organizacao.objects.create(nome=slug.title(), slug=slug)


def assert_object_bump(bump, *organizations: Organizacao) -> None:
    scopes = tuple(sorted(f"guardian:object:{get_content_type(organization).pk}:{organization.pk}" for organization in organizations))
    bump.assert_called_once_with(scopes, database_alias="default", layer="guardian")


@pytest.fixture
def replica_alias():
    try:
        yield REPLICA_DATABASE_ALIAS
    finally:
        connections[REPLICA_DATABASE_ALIAS].close()


def guardian_inserts(queries) -> list[str]:
    return [query["sql"] for query in queries if 'INSERT INTO "guardian_userobjectpermission"' in query["sql"]]


def guardian_deletes(queries) -> list[str]:
    return [query["sql"] for query in queries if 'DELETE FROM "guardian_userobjectpermission"' in query["sql"]]


def create_divergent_guardian_permission(replica_alias, users, organization):
    content_type = ContentType.objects.using(replica_alias).create(app_label="replica_only", model="organizacao")
    permission = Permission.objects.using(replica_alias).create(
        content_type_id=content_type.pk,
        codename="view_organizacao",
        name="Can view replica organization",
    )
    permission_model = get_user_obj_perms_model()
    rows = permission_model.objects.using(replica_alias).bulk_create(
        [
            permission_model(
                user_id=user.pk,
                permission_id=permission.pk,
                content_type_id=content_type.pk,
                object_pk=organization.pk,
            )
            for user in users
        ]
    )
    return content_type, permission_model, rows


def alias_content_type_lookup(replica_alias, divergent_content_type):
    original_get_for_model = ContentTypeManager.get_for_model

    def get_for_model(manager, model, for_concrete_model=True):
        if manager.db == replica_alias:
            return divergent_content_type
        return original_get_for_model(manager, model, for_concrete_model=for_concrete_model)

    return patch.object(ContentTypeManager, "get_for_model", get_for_model)


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


@pytest.mark.django_db(transaction=True, databases="__all__")
def test_guardian_bulk_assign_writes_on_object_database_alias(replica_alias):
    user = UsuarioFactory()
    organization = create_organization("guardian-replica-bulk")
    user._state.db = replica_alias
    organization._state.db = replica_alias

    with (
        patch("common.permission_cache.invalidation.bump_epoch_scopes") as bump,
        CaptureQueriesContext(connections["default"]) as default_queries,
        CaptureQueriesContext(connections[replica_alias]) as replica_queries,
    ):
        with transaction.atomic(using=replica_alias):
            assigned = guardian_bulk_assign("view_organizacao", user, [organization])
            bump.assert_not_called()

        assert len(assigned) == 1
        assert guardian_inserts(default_queries) == []
        assert len(guardian_inserts(replica_queries)) == 1
        bump.assert_called_once_with(
            (f"guardian:object:{get_content_type(organization).pk}:{organization.pk}",),
            database_alias=replica_alias,
            layer="guardian",
        )


@pytest.mark.django_db(transaction=True, databases="__all__")
def test_guardian_assign_to_many_writes_on_object_database_alias(replica_alias):
    first = UsuarioFactory()
    second = UsuarioFactory()
    organization = create_organization("guardian-replica-many")
    for instance in (first, second, organization):
        instance._state.db = replica_alias

    with (
        patch("common.permission_cache.invalidation.bump_epoch_scopes") as bump,
        CaptureQueriesContext(connections["default"]) as default_queries,
        CaptureQueriesContext(connections[replica_alias]) as replica_queries,
    ):
        with transaction.atomic(using=replica_alias):
            assigned = guardian_assign_to_many("view_organizacao", [first, second], organization)
            bump.assert_not_called()

        assert len(assigned) == 2
        assert guardian_inserts(default_queries) == []
        assert len(guardian_inserts(replica_queries)) == 1
        bump.assert_called_once_with(
            (f"guardian:object:{get_content_type(organization).pk}:{organization.pk}",),
            database_alias=replica_alias,
            layer="guardian",
        )


@pytest.mark.django_db(transaction=True, databases="__all__")
def test_guardian_bulk_remove_uses_alias_content_type_when_ids_diverge(replica_alias):
    user = UsuarioFactory()
    organization = create_organization("guardian-replica-remove-bulk")
    content_type, permission_model, rows = create_divergent_guardian_permission(replica_alias, [user], organization)
    user._state.db = replica_alias
    organization._state.db = replica_alias

    with (
        alias_content_type_lookup(replica_alias, content_type),
        patch("common.permission_cache.invalidation.bump_epoch_scopes"),
        CaptureQueriesContext(connections["default"]) as default_queries,
        CaptureQueriesContext(connections[replica_alias]) as replica_queries,
    ):
        removed = guardian_bulk_remove("view_organizacao", user, [organization])

    assert removed[0] == 1
    assert not permission_model.objects.using(replica_alias).filter(pk=rows[0].pk).exists()
    assert guardian_deletes(default_queries) == []
    assert len(guardian_deletes(replica_queries)) == 1


@pytest.mark.django_db(transaction=True, databases="__all__")
def test_guardian_remove_from_many_uses_alias_content_type_when_ids_diverge(replica_alias):
    first = UsuarioFactory()
    second = UsuarioFactory()
    organization = create_organization("guardian-replica-remove-many")
    users = [first, second]
    content_type, permission_model, rows = create_divergent_guardian_permission(replica_alias, users, organization)
    for instance in (*users, organization):
        instance._state.db = replica_alias

    with (
        alias_content_type_lookup(replica_alias, content_type),
        patch("common.permission_cache.invalidation.bump_epoch_scopes"),
        CaptureQueriesContext(connections["default"]) as default_queries,
        CaptureQueriesContext(connections[replica_alias]) as replica_queries,
    ):
        removed = guardian_remove_from_many("view_organizacao", users, organization)

    assert removed[0] == 2
    assert not permission_model.objects.using(replica_alias).filter(pk__in=[row.pk for row in rows]).exists()
    assert guardian_deletes(default_queries) == []
    assert len(guardian_deletes(replica_queries)) == 1


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
DIRECT_AUTHORIZATION_WRITE_ALLOWLIST: set[tuple[str, str]] = set()
MODEL_KINDS = {
    "Vinculo": "membership bulk mutation",
    "Permission": "permission bulk mutation",
    "Usuario": "user authorization-state update",
    "UserObjectPermission": "guardian object-permission bulk mutation",
    "GroupObjectPermission": "guardian object-permission bulk mutation",
}
GUARDIAN_MODEL_FACTORIES = {"get_user_obj_perms_model", "get_group_obj_perms_model"}
MANAGER_ATTRIBUTES = {"objects", "_base_manager", "_default_manager"}
MUTATION_METHODS = {"bulk_create", "bulk_update", "update"}
AUTHORIZATION_STATE_FIELDS = {"is_active", "is_superuser"}


class AuthorizationWriteVisitor(ast.NodeVisitor):
    def __init__(self):
        self.symbols = dict(MODEL_KINDS)
        self.guardian_factories = set(GUARDIAN_MODEL_FACTORIES)
        self.violations: list[tuple[str, int]] = []

    def expression_kind(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return self.symbols.get(node.id)
        if isinstance(node, ast.Attribute):
            if node.attr in MODEL_KINDS:
                return MODEL_KINDS[node.attr]
            if node.attr in MANAGER_ATTRIBUTES:
                return self.expression_kind(node.value)
            return None
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in self.guardian_factories:
                return "guardian object-permission bulk mutation"
            if isinstance(node.func, ast.Attribute) and node.func.attr in self.guardian_factories:
                return "guardian object-permission bulk mutation"
            if isinstance(node.func, ast.Attribute):
                return self.expression_kind(node.func.value)
        return None

    def bind(self, target: ast.AST, kind: str | None) -> None:
        if not isinstance(target, ast.Name):
            return
        if kind is None:
            self.symbols.pop(target.id, None)
        else:
            self.symbols[target.id] = kind

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for imported in node.names:
            local_name = imported.asname or imported.name
            if imported.name in MODEL_KINDS:
                self.symbols[local_name] = MODEL_KINDS[imported.name]
            if imported.name in GUARDIAN_MODEL_FACTORIES:
                self.guardian_factories.add(local_name)

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        kind = self.expression_kind(node.value)
        for target in node.targets:
            self.bind(target, kind)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self.visit(node.value)
            self.bind(node.target, self.expression_kind(node.value))

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        previous_symbols = self.symbols
        self.symbols = previous_symbols.copy()
        for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
            self.symbols.pop(argument.arg, None)
        if node.args.vararg is not None:
            self.symbols.pop(node.args.vararg.arg, None)
        if node.args.kwarg is not None:
            self.symbols.pop(node.args.kwarg.arg, None)
        for statement in node.body:
            self.visit(statement)
        self.symbols = previous_symbols

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute) and node.func.attr in MUTATION_METHODS:
            kind = self.expression_kind(node.func.value)
            if kind is not None and self.is_authorization_write(kind, node.func.attr, node):
                self.violations.append((kind, node.lineno))
        self.generic_visit(node)

    @staticmethod
    def is_authorization_write(kind: str, method: str, node: ast.Call) -> bool:
        if kind != "user authorization-state update":
            return True
        if method == "update":
            return any(keyword.arg in AUTHORIZATION_STATE_FIELDS for keyword in node.keywords)
        if method == "bulk_update":
            fields_node = (
                node.args[1]
                if len(node.args) > 1
                else next(
                    (keyword.value for keyword in node.keywords if keyword.arg == "fields"),
                    None,
                )
            )
            if isinstance(fields_node, (ast.List, ast.Tuple, ast.Set)):
                return any(isinstance(field, ast.Constant) and field.value in AUTHORIZATION_STATE_FIELDS for field in fields_node.elts)
        return False


def scan_authorization_writes(source: str) -> list[tuple[str, int]]:
    visitor = AuthorizationWriteVisitor()
    visitor.visit(ast.parse(source))
    return visitor.violations


@pytest.mark.parametrize(
    ("source", "expected_kind", "expected_line"),
    [
        (
            "model = get_user_obj_perms_model()\nmanager = model.objects\nmanager.bulk_create([])\n",
            "guardian object-permission bulk mutation",
            3,
        ),
        (
            "queryset = Vinculo.objects.filter(ativo=True)\nqueryset.update(papel=30)\n",
            "membership bulk mutation",
            2,
        ),
        (
            "manager = Permission.objects\nmanager.bulk_update([], ['name'])\n",
            "permission bulk mutation",
            2,
        ),
        (
            "users = Usuario.objects.filter(is_active=True)\nusers.update(is_superuser=False)\n",
            "user authorization-state update",
            2,
        ),
        (
            "Usuario.objects.bulk_update(users, ['is_active'])\n",
            "user authorization-state update",
            1,
        ),
        (
            "Vinculo.objects.filter(ativo=True).order_by('pk').update(papel=30)\n",
            "membership bulk mutation",
            1,
        ),
        (
            "guardian.utils.get_user_obj_perms_model().objects.order_by('pk').bulk_create([])\n",
            "guardian object-permission bulk mutation",
            1,
        ),
    ],
)
def test_authorization_write_scan_follows_model_manager_and_queryset_indirection(source, expected_kind, expected_line):
    assert scan_authorization_writes(source) == [(expected_kind, expected_line)]


@pytest.mark.parametrize(
    "source",
    [
        "Permission.objects.get(codename='view')\naudit.update(status='seen')\n",
        "Vinculo.objects.filter(ativo=True)\nmetrics.update(value=1)\n",
        "users = Usuario.objects.filter(is_active=True)\nusers.update(first_name='Ada')\n",
        "def memberships():\n    rows = Vinculo.objects.all()\n\ndef metrics(rows):\n    rows.update(value=1)\n",
    ],
)
def test_authorization_write_scan_does_not_join_unrelated_calls(source):
    assert scan_authorization_writes(source) == []


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


@pytest.mark.parametrize("path", production_python_files(), ids=lambda path: str(path.relative_to(REPOSITORY_ROOT)))
def test_production_code_has_no_direct_bulk_authorization_writes(path):
    relative = str(path.relative_to(REPOSITORY_ROOT))
    violations = [
        (write_name, line)
        for write_name, line in scan_authorization_writes(path.read_text(encoding="utf-8"))
        if (write_name, relative) not in DIRECT_AUTHORIZATION_WRITE_ALLOWLIST
    ]
    assert violations == [], f"Direct authorization writes in {relative} must use common.permission_cache.mutations: {violations}"
