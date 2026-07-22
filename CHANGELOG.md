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
