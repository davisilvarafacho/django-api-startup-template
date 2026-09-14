import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
APPS_ROOT = REPOSITORY_ROOT / "apps"
COMMERCIAL_MARKER = "MARCADOR_REGULARIZACAO_ASSINATURA"


def _is_path_literal(node):
    return isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith("/")


def _uses_commercial_marker(tree):
    return any(
        (isinstance(node, ast.Name) and node.id == COMMERCIAL_MARKER)
        or (isinstance(node, ast.Attribute) and node.attr == COMMERCIAL_MARKER)
        or (isinstance(node, ast.alias) and node.name.endswith(COMMERCIAL_MARKER))
        for node in ast.walk(tree)
    )


class TextualRouteAllowlistVisitor(ast.NodeVisitor):
    def __init__(self):
        self.lines = []

    def _record_if_path_literal(self, node, values):
        if any(_is_path_literal(value) for value in values):
            self.lines.append(node.lineno)

    def visit_List(self, node):
        self._record_if_path_literal(node, node.elts)
        self.generic_visit(node)

    visit_Set = visit_List
    visit_Tuple = visit_List

    def visit_Dict(self, node):
        self._record_if_path_literal(node, [*node.keys, *node.values])
        self.generic_visit(node)

    def visit_Compare(self, node):
        self._record_if_path_literal(node, [node.left, *node.comparators])
        self.generic_visit(node)

    def visit_Call(self, node):
        if isinstance(node.func, ast.Attribute) and node.func.attr in {"startswith", "endswith"}:
            self._record_if_path_literal(node, node.args)
        self.generic_visit(node)


def test_consumidores_do_marcador_comercial_nao_usam_allowlist_textual_de_url():
    violations = []
    for path in APPS_ROOT.rglob("*.py"):
        if "tests" in path.parts or "migrations" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if not _uses_commercial_marker(tree):
            continue

        visitor = TextualRouteAllowlistVisitor()
        visitor.visit(tree)
        violations.extend(f"{path.relative_to(REPOSITORY_ROOT)}:{line}" for line in visitor.lines)

    assert violations == [], f"Regularização comercial deve usar marcador declarativo, não allowlist textual: {violations}"
