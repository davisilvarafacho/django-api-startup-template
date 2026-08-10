import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_ROOTS = ("api", "apps", "internal_frameworks", "utils", "scripts")
ROOT_PYTHON_FILES = ("conftest.py", "gunicorn.conf.py", "manage.py")
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
QUERYSET_RETURNING_METHODS = {
    "alias",
    "all",
    "annotate",
    "complex_filter",
    "dates",
    "datetimes",
    "defer",
    "difference",
    "distinct",
    "exclude",
    "extra",
    "filter",
    "intersection",
    "none",
    "only",
    "order_by",
    "prefetch_related",
    "reverse",
    "select_for_update",
    "select_related",
    "union",
    "using",
    "values",
    "values_list",
}
QUERYSET_TERMINAL_METHODS = {
    "aggregate",
    "contains",
    "count",
    "create",
    "delete",
    "earliest",
    "exists",
    "explain",
    "first",
    "get",
    "get_or_create",
    "in_bulk",
    "iterator",
    "last",
    "latest",
    "update_or_create",
}
MUTATION_METHODS = {"bulk_create", "bulk_update", "update"}
AUTHORIZATION_STATE_FIELDS = {"is_active", "is_superuser"}


class AuthorizationWriteVisitor(ast.NodeVisitor):
    def __init__(self):
        self.symbols = dict(MODEL_KINDS)
        self.guardian_factories = set(GUARDIAN_MODEL_FACTORIES)
        self.constant_fields: dict[str, frozenset[str]] = {}
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
            if isinstance(node.func, ast.Attribute) and node.func.attr in QUERYSET_RETURNING_METHODS:
                return self.expression_kind(node.func.value)
            if isinstance(node.func, ast.Attribute) and node.func.attr in QUERYSET_TERMINAL_METHODS:
                return None
        return None

    def expression_fields(self, node: ast.AST) -> frozenset[str] | None:
        if isinstance(node, ast.Name):
            return self.constant_fields.get(node.id)
        if isinstance(node, (ast.List, ast.Set, ast.Tuple)):
            if all(isinstance(field, ast.Constant) and isinstance(field.value, str) for field in node.elts):
                return frozenset(field.value for field in node.elts)
            return None
        if isinstance(node, ast.Dict):
            fields: set[str] = set()
            for key, value in zip(node.keys, node.values, strict=True):
                if key is None:
                    inherited = self.expression_fields(value)
                    if inherited is not None:
                        fields.update(inherited)
                elif isinstance(key, ast.Constant) and isinstance(key.value, str):
                    fields.add(key.value)
            return frozenset(fields)
        return None

    def bind(self, target: ast.AST, kind: str | None) -> None:
        if not isinstance(target, ast.Name):
            return
        if kind is None:
            self.symbols.pop(target.id, None)
        else:
            self.symbols[target.id] = kind

    def bind_fields(self, target: ast.AST, fields: frozenset[str] | None) -> None:
        if not isinstance(target, ast.Name):
            return
        if fields is None:
            self.constant_fields.pop(target.id, None)
        else:
            self.constant_fields[target.id] = fields

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
        fields = self.expression_fields(node.value)
        for target in node.targets:
            self.bind(target, kind)
            self.bind_fields(target, fields)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self.visit(node.value)
            self.bind(node.target, self.expression_kind(node.value))
            self.bind_fields(node.target, self.expression_fields(node.value))

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        previous_symbols = self.symbols
        previous_constant_fields = self.constant_fields
        self.symbols = previous_symbols.copy()
        self.constant_fields = previous_constant_fields.copy()
        for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
            self.symbols.pop(argument.arg, None)
            self.constant_fields.pop(argument.arg, None)
        if node.args.vararg is not None:
            self.symbols.pop(node.args.vararg.arg, None)
            self.constant_fields.pop(node.args.vararg.arg, None)
        if node.args.kwarg is not None:
            self.symbols.pop(node.args.kwarg.arg, None)
            self.constant_fields.pop(node.args.kwarg.arg, None)
        for statement in node.body:
            self.visit(statement)
        self.symbols = previous_symbols
        self.constant_fields = previous_constant_fields

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute) and node.func.attr in MUTATION_METHODS:
            kind = self.expression_kind(node.func.value)
            if kind is not None and self.is_authorization_write(kind, node.func.attr, node):
                self.violations.append((kind, node.lineno))
        self.generic_visit(node)

    def is_authorization_write(self, kind: str, method: str, node: ast.Call) -> bool:
        if kind != "user authorization-state update":
            return True
        if method == "update":
            fields = {keyword.arg for keyword in node.keywords if keyword.arg is not None}
            for keyword in node.keywords:
                if keyword.arg is None:
                    fields.update(self.expression_fields(keyword.value) or ())
            return bool(fields.intersection(AUTHORIZATION_STATE_FIELDS))
        if method == "bulk_update":
            fields_node = (
                node.args[1]
                if len(node.args) > 1
                else next(
                    (keyword.value for keyword in node.keywords if keyword.arg == "fields"),
                    None,
                )
            )
            if fields_node is not None:
                fields = self.expression_fields(fields_node)
                return bool(fields and fields.intersection(AUTHORIZATION_STATE_FIELDS))
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
            "queryset = Vinculo.objects.filter(is_active=True)\nqueryset.update(papel=30)\n",
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
            "Vinculo.objects.filter(is_active=True).order_by('pk').update(papel=30)\n",
            "membership bulk mutation",
            1,
        ),
        (
            "guardian.utils.get_user_obj_perms_model().objects.order_by('pk').bulk_create([])\n",
            "guardian object-permission bulk mutation",
            1,
        ),
        (
            "auth_fields = ['is_active']\nfields = auth_fields\nUsuario.objects.bulk_update(users, fields)\n",
            "user authorization-state update",
            3,
        ),
        (
            "auth_changes = {'is_superuser': False}\nchanges = auth_changes\nUsuario.objects.filter(pk=1).update(**changes)\n",
            "user authorization-state update",
            3,
        ),
    ],
)
def test_authorization_write_scan_follows_model_manager_and_queryset_indirection(source, expected_kind, expected_line):
    assert scan_authorization_writes(source) == [(expected_kind, expected_line)]


@pytest.mark.parametrize(
    "source",
    [
        "Permission.objects.get(codename='view')\naudit.update(status='seen')\n",
        "Vinculo.objects.filter(is_active=True)\nmetrics.update(value=1)\n",
        "users = Usuario.objects.filter(is_active=True)\nusers.update(first_name='Ada')\n",
        "def memberships():\n    rows = Vinculo.objects.all()\n\ndef metrics(rows):\n    rows.update(value=1)\n",
        "summary = Usuario.objects.aggregate(total=Count('pk'))\nsummary.update(is_active=False)\n",
        "Usuario.objects.aggregate(total=Count('pk')).update(is_active=False)\n",
        "Usuario.objects.values('id').first().update(is_active=False)\n",
    ],
)
def test_authorization_write_scan_does_not_join_unrelated_calls(source):
    assert scan_authorization_writes(source) == []


def production_python_files() -> list[Path]:
    files = [
        path
        for root in PRODUCTION_ROOTS
        for path in (REPOSITORY_ROOT / root).rglob("*.py")
        if not any(part in {"migrations", "tests"} for part in path.relative_to(REPOSITORY_ROOT).parts)
    ]
    files.extend(REPOSITORY_ROOT / name for name in ROOT_PYTHON_FILES if (REPOSITORY_ROOT / name).exists())
    excluded = REPOSITORY_ROOT / "internal_frameworks/permission_cache/mutations.py"
    return sorted(path for path in files if path != excluded)


@pytest.mark.parametrize("path", production_python_files(), ids=lambda path: str(path.relative_to(REPOSITORY_ROOT)))
def test_production_code_has_no_direct_bulk_authorization_writes(path):
    relative = str(path.relative_to(REPOSITORY_ROOT))
    violations = [
        (write_name, line)
        for write_name, line in scan_authorization_writes(path.read_text(encoding="utf-8"))
        if (write_name, relative) not in DIRECT_AUTHORIZATION_WRITE_ALLOWLIST
    ]
    assert violations == [], f"Direct authorization writes in {relative} must use internal_frameworks.permission_cache.mutations: {violations}"
