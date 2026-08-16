# Arquitetura

A aplicação usa Django REST Framework com PostgreSQL como banco principal, Redis
para cache e broker, e Celery para trabalho assíncrono. A especificação é gerada
por drf-spectacular e apresentada pelo Scalar.

A documentação segue Diátaxis para separar aprendizado, procedimentos, referência
e decisões arquiteturais.

## Erros

Toda falha HTTP — vinda do DRF, de middleware ou dos handlers de status do
Django — converge para o mesmo envelope de erro, com código estável tipado,
mensagem traduzível e `request_id` de correlação. Ver `docs/reference/api.md`
para o contrato e `.ai/brainstorming/spec/2026-07-28-api-errors-design.md`
para a spec completa.
