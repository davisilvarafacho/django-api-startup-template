import tomllib
from pathlib import Path

from django.conf import settings

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[2]


def test_mcp_app_is_installed():
    assert "apps.api.mcp_server" in settings.BUSINESS_APPS


def test_mcp_dependencies_are_runtime_dependencies():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    names = {Requirement(value).name.lower() for value in project["dependencies"]}

    assert {"mcp", "pyjwt"} <= names
