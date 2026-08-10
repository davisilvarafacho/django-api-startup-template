import ast
from pathlib import Path


def _metadata_meta_assignments():
    source = Path(__file__).parents[1].joinpath("models.py").read_text(encoding="utf-8")
    module = ast.parse(source)
    metadata = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "Metadata")
    meta = next(node for node in metadata.body if isinstance(node, ast.ClassDef) and node.name == "Meta")
    return {target.id: node.value for node in meta.body if isinstance(node, ast.Assign) for target in node.targets if isinstance(target, ast.Name)}


def test_metadata_has_unique_composite_constraint():
    assignments = _metadata_meta_assignments()
    constraints = assignments["constraints"]
    constraint = constraints.elts[0]

    assert isinstance(constraint.func, ast.Attribute)
    assert constraint.func.attr == "UniqueConstraint"
    assert ast.literal_eval(next(keyword.value for keyword in constraint.keywords if keyword.arg == "fields")) == ["content_type", "object_id"]
    assert (
        ast.literal_eval(next(keyword.value for keyword in constraint.keywords if keyword.arg == "name")) == "metadata_content_type_object_id_unique"
    )
