# Publicar uma versão

1. Atualize `project.version` em `pyproject.toml` e `SPECTACULAR_SETTINGS["VERSION"]`
   em `api/settings.py` para a mesma versão SemVer.
2. Mova as entradas relevantes de **Unreleased** para uma nova seção no
   `CHANGELOG.md`.
3. Valide com `make version-check`, `make lint`, `make test` e `make docs`.
4. Crie a tag `vX.Y.Z` e publique a release.

## Remover endpoint depreciado

Antes de remover uma rota, confirme que a data `sunset` chegou, que passaram ao menos 90 dias desde `since` e que a release é SemVer incompatível. Registre a retirada em `Removed` e preserve permanentemente o guia de migração, anotando nele a data e a versão da remoção.
