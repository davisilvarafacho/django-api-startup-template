## Unreleased

### Feat

- **base**: adiciona action bulk_update no BaseModelViewSet
- endpoint global de lookup (autocomplete de FK)
- cache versionado por viewset e endpoint de invalidação
- aplicação Celery e stack de infra local
- configuração por ambiente com Postgres, cache Redis, Celery e throttling
- remover arquivo __init__.py da pasta api
- pasta para context em formato .md para agentes de IA
- remover arquivos não utilizados da estrutura do projeto
- add OpenAPI and Scalar documentation routes
- add Backblaze B2 and Resend backends

### Fix

- **base**: padroniza codename de permissão para can_toggle_<model>
- importa debug_toolbar apenas em desenvolvimento
# Changelog

Todas as mudanças relevantes deste projeto são registradas neste arquivo.

O formato segue [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e o
projeto adota [Versionamento Semântico](https://semver.org/lang/pt-BR/).

## [Unreleased]

### Added

- Portal de documentação MkDocs estruturado por Diátaxis.
- Validação de Conventional Commits em hooks e CI.

## [0.1.0] - 2026-07-21

### Added

- Base Django REST Framework com PostgreSQL, Redis, Celery, cache, throttling,
  documentação OpenAPI e automações de qualidade.

[Unreleased]: https://github.com/forteplus/drf-base-api-project/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/forteplus/drf-base-api-project/releases/tag/v0.1.0
