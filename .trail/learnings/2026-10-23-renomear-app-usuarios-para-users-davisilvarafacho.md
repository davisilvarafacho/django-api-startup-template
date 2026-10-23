---
title: Renomear app usuarios para users
author: Davisilvarafacho
date: 2026-10-23
branch: main
cost: { minutes: 14, tokens: 80281 }
touches:
  - api/settings.py
  - apps/api/autenticacao/identities.py
  - apps/api/autenticacao/passwords.py
  - apps/api/autenticacao/schema.py
  - apps/api/autenticacao/services.py
  - apps/api/autenticacao/tasks.py
  - apps/api/autenticacao/tests/google/test_login.py
  - apps/api/autenticacao/tests/google/test_race.py
  - apps/api/autenticacao/tests/mfa/test_trusted_devices.py
  - apps/api/autenticacao/tests/test_permission_cache.py
---

## Decided
- App `apps/usuarios` virou `apps/users` (dir, label, AUTH_USER_MODEL, migrations, imports, tasks Celery `users.*`, docs). `db_table = "usuario"` é explícito, então nenhuma tabela muda. Model `Usuario` e `tests/support/usuarios.py` ficaram como estavam.
- Salts de assinatura em `apps/users/emails.py` (`"usuarios.email-verification"` etc.) mantidos: trocar invalida links de e-mail já enviados.
- Histórico (`.ai/brainstorming`, `CHANGELOG.md`, `docs/research`) não foi reescrito.

## Tried and ruled out
- Nada de migração de dados no código: bancos existentes precisam de `UPDATE django_migrations SET app='users' WHERE app='usuarios'` e o mesmo em `django_content_type.app_label`.

## Watch out
- `make up` falha se já houver outro container Postgres em 5432; os defaults do Makefile (postgres/postgres) funcionaram contra ele.
- Falhas pré-existentes, não ligadas à renomeação: `test_start_api_app` (template gera `router_v1`, teste espera `router`) e `test_cached_guardian_backend_replaces_upstream_backend` (só falha na suíte completa, depende da ordem).
- A suíte completa leva ~13 min.
- O beat com DatabaseScheduler pode manter entrada com o nome antigo da task.
