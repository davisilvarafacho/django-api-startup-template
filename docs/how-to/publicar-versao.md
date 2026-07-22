# Publicar uma versão

1. Atualize `project.version` em `pyproject.toml` e `SPECTACULAR_SETTINGS["VERSION"]`
   em `api/settings.py` para a mesma versão SemVer.
2. Mova as entradas relevantes de **Unreleased** para uma nova seção no
   `CHANGELOG.md`.
3. Valide com `make version-check`, `make lint`, `make test` e `make docs`.
4. Crie a tag `vX.Y.Z` e publique a release.
