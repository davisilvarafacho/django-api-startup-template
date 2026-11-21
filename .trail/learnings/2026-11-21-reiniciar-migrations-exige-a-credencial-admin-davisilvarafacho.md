---
title: "Reiniciar migrations exige a credencial admin, e três falhas da suíte não vêm do billing"
author: Davisilvarafacho
date: 2026-11-21
branch: refactor/billing-enxuto
cost: { minutes: 17, tokens: 98362 }
id: l-f99960c7
scope: repo
status: active
src: agent
touches:
  - manage.py
  - apps/api/base/tests/test_model_checks.py
  - apps/api/core/tests/management_commands/test_start_api_app.py
  - apps/api/autenticacao/migrations/0001_initial.py
  - apps/api/autenticacao/migrations/0002_initial.py
  - apps/api/metadata/migrations/0001_initial.py
  - apps/api/metadata/migrations/0002_initial.py
  - apps/logs/migrations/0001_initial.py
  - apps/logs/migrations/0002_initial.py
  - apps/organizacoes/migrations/0001_initial.py
---

## Decided
- O reset das migrations roda com a credencial admin do Postgres, porque o login web (app_web) não tem DDL; logo depois rode `make db-roles`, porque recriar o schema public apaga os grants do login web.
- A migration de backfill de workspaces e o teste dela foram removidos no reset, porque só migravam dados anteriores aos workspaces e não há base em produção a preservar.
- O billing antigo (assinaturas + faturamento) foi removido porque custava ~9 mil linhas de produção (34% do código), 55 funções/triggers SQL, 4 logins de banco e 2 processos extras para algo que o gateway já resolve.
- O substituto é um app pequeno sobre o django-checkouts (lib do próprio autor, voltada ao Brasil), porque ela já normaliza checkout, troca de plano com timing/proration, cancelamento no fim do período, verificação de webhook, listagem de eventos e retry_advice.
- Regras de produto escolhidas pelo dono: preço fixo por plano com limite de membros (não seats), plano gratuito permanente, upgrade imediato, downgrade no próximo ciclo e barrado se houver membros acima do limite; quando o acesso cai, os membros mais recentes acima do limite ficam inativos.
- Abordagem escolhida: o evento só dispara a sincronização, que busca o estado atual no gateway e sobrescreve o espelho, porque assim ordem e duplicidade de eventos deixam de importar.
- O acesso é derivado do status do espelho por property, nunca gravado, porque um campo gravado pode divergir e exigiria proteção (o que inflou a versão antiga).
- A assinatura de billing fica sob RLS e a organização de cada evento vem do próprio gateway (reference_id na assinatura), porque o contexto privilegiado não permite buscar um registro sem saber o tenant.

## Tried and ruled out
- `make reset-migrations` como está: falha com "DATABASES['default']['NAME'] não pode ser vazio" fora do Makefile, e dentro dele usa app_web, que não consegue recriar o schema.
- Aplicar o payload do evento direto: exigiria comparar occurred_at para não regredir estado, a mesma complexidade da versão antiga.
- Só polling: o cliente pagaria e esperaria minutos pelo acesso.
- Stripe SDK direto: mais curto, mas o dono quer manter a troca de gateway via django-checkouts.
- Models de billing globais: rejeitado porque deixaria dado de tenant fora do RLS.
- "Bypass" com o contexto privilegiado: não funciona, porque ele só autoriza trocar de tenant; cada query continua filtrada por uma organização. Ler atravessando tenants exigiria SECURITY DEFINER ou BYPASSRLS, o que inflou o billing antigo.

## Watch out
- Três falhas não vêm do billing: as duas de test_start_api_app já falhavam na branch startup, e test_cached_guardian_backend_replaces_upstream_backend só falha na suíte completa porque os models de teste de test_model_checks vazam para o `check` (rodando sozinho, passa).
- pytest com app_web falha com "permission denied to create database"; use a credencial admin, como `make test` faz.

In the code:
- `DJANGO_ENVIRONMENT=development DATABASE_NAME=base DATABASE_HOST=127.0.0.1 DATABASE_PORT=5432 DATABASE_USER=postgres DATABASE_PASSWORD=postgres uv run python manage.py reset_migrations --apply --confirm-database base`, depois `make db-roles`.
- O alvo `reset-migrations` do Makefile não sobrescreve DATABASE_USER, diferente de `migrate` e `test`.
- Poluidor: apps/api/base/tests/test_model_checks.py. Pré-existente: apps/api/core/tests/management_commands/test_start_api_app.py.
- A assinatura criada pelo checkout Stripe na django-checkouts 1.0.1 não carrega reference_id (o client_reference_id fica só na sessão de checkout), então eventos de assinatura/fatura chegam só com o external_id; achar a organização exige ler a assinatura local antes de haver contexto de tenant.

- Remoção: commit 97d0166 na branch refactor/billing-enxuto. Design: .ai/brainstorming/spec/2026-11-21-billing-enxuto-design.md.
- Stripe: reference_id vem de subscription.metadata["reference_id"] (gateways/stripe/mapping.py); checkout grava só client_reference_id e metadata da sessão.
- Para buscar dado de tenant por um id externo sem saber a organização, traga a organização junto com o dado externo em vez de procurar fora do RLS.

- organizacao_atual_privilegiada (apps/organizacoes/context.py) usa set_rls_context(..., system=True), que só libera a troca de tenant.
