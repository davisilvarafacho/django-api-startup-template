---
title: Padronização dos nomes de env vars por prefixo de domínio
author: Davisilvarafacho
date: 2026-10-22
branch: main
cost: { minutes: 21, tokens: 422869 }
touches:
  - ".devcontainer/docker-compose.yml"
  - ".env.example"
  - ".github/workflows/ci.yml"
  - CLAUDE.md
  - Makefile
  - api/settings.py
  - api/tests/test_database_settings.py
  - apps/api/autenticacao/passwords.py
  - apps/api/autenticacao/tasks.py
  - apps/api/autenticacao/tests/passwords/test_password_reset.py
---

## Decided
- Regra: o primeiro termo diz o dono da variável. `FRONTEND_<DOMÍNIO>_<O QUE É>_URL` para tudo que aponta ao frontend; fornecedor (`STRIPE_`), tecnologia (`DATABASE_<PAPEL>_<CAMPO>`), domínio de negócio (`AUTH_`, `ACCOUNT_`, `SUBSCRIPTION_`) e framework (`DJANGO_`, `MCP_`). Nomes em inglês. Tabela em docs/reference/convencoes.md §8.
- `DJANGO_CSRF_TRUSTED_ORIGINS` e `MCP_ALLOWED_ORIGINS` ficam no prefixo do framework, mesmo listando origens de browser.
- Renomeado o nome da env var e o identificador de setting juntos (o repo já usa o mesmo nome nos dois). Isso inclui `settings.X` lido em migrations aplicadas, que só leem em runtime.
- Nome antigo no ambiente derruba o boot: `LEGACY_ENV_VARS` em utils/env.py, checado no topo do settings. Sem isso o nome antigo é ignorado e cai nos defaults de dev.

## Tried and ruled out
- Renomear só a string da env var e manter o identificador de setting: deixaria nomes diferentes nos dois lados e ainda exigiria tocar nas migrations.
- Exigir que o `Literal` `EnviromentVar` contenha todo nome novo: falha, porque variáveis lidas só por compose, Makefile ou shell (DATABASE_ADMIN_*, *_HOST_PORT) não passam por helper Python. O teste certo checa o `.env.example`.

## Watch out
- Duas renomeações mudam o valor: `PASSWORD_RESET_TIMEOUT_MINUTES` (minutos) virou `AUTH_PASSWORD_RESET_TOKEN_MAX_AGE_SECONDS` (segundos), e `POSTHOG_DISABLED` virou `POSTHOG_ENABLED` com polaridade invertida. Um sed não basta.
- Testes que sobem subprocesso (MCP stdio, uvicorn, ingress) carregam o `.env` do repo via load_dotenv, e o stdio_client repassa só variáveis seguras. Esses testes precisam declarar `DJANGO_ENVIRONMENT` e quebram se o `.env` local tiver nomes antigos. Para provar que é só o `.env`, rode numa cópia do projeto sem ele.
- Variáveis internas do compose (`DATABASE_BILLING_WEB_*`, repassadas ao script init-billing-roles.sh) também seguem o padrão e não estão no `.env.example`.
- `ruff format .` reformata `apps/workspaces/workspaces.py`, que já estava fora de formato no HEAD. Não inclua isso no diff.
- Falhas anteriores a esta mudança: test_start_api_app (2) e test_cached_guardian_backend_replaces_upstream_backend (depende da ordem, passa isolado).
