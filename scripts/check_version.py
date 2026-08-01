"""Garante que a versão pública da API acompanha a versão do pacote."""

import ast
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


ROOT_DIR = Path(__file__).resolve().parents[1]


def get_openapi_version(settings_path: Path) -> str:
    """Extrai a versão do dicionário SPECTACULAR_SETTINGS sem carregar Django."""
    module = ast.parse(settings_path.read_text(encoding="utf-8"))
    for statement in module.body:
        if not isinstance(statement, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "SPECTACULAR_SETTINGS" for target in statement.targets):
            continue

        settings = ast.literal_eval(statement.value)
        return settings["VERSION"]

    raise RuntimeError("SPECTACULAR_SETTINGS não foi encontrado em api/settings.py.")


def main() -> None:
    """Compara a versão declarada do pacote com a versão do schema OpenAPI."""
    project = tomllib.loads((ROOT_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    package_version = project["project"]["version"]
    openapi_version = get_openapi_version(ROOT_DIR / "api/settings.py")

    if package_version != openapi_version:
        raise SystemExit(f"Versões divergentes: pyproject.toml={package_version}; SPECTACULAR_SETTINGS[VERSION]={openapi_version}.")

    print(f"Versões alinhadas: {package_version}")


if __name__ == "__main__":
    main()
