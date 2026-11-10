# Branch `internal`: remover assinaturas e faturamento

**Branch:** `internal` (derivada de `enterprise` em `9f004c1`).
**Objetivo:** variante para uso interno de uma empresa — tudo da `enterprise`,
menos assinaturas, planos, seats, trial, propostas comerciais e faturamento
(Stripe / `django-checkouts`). Continua **multi-organização com RLS**.

Revisado em 2026-11-10 contra o código em `9f004c1`.

## Decisões

1. **Remoção real, não implementação nula.** `organizacoes` deixa de conhecer o
   conceito de contrato: somem `ContextoOrganizacao.assinatura`,
   `utilizacao_seats`, `situacao_acesso`, `PoliticaComercialTenant` e os
   Protocols `AssinaturasOnboarding`/`CatalogoPlanosOnboarding`/
   `AssinaturasEncerramento`. Custo aceito: merges vindos da `enterprise` que
   tocarem esses pontos exigem resolução manual (ver "Sincronização").
2. **Encerramento de organização imediato.** `_efetivar_bloqueada`
   (`organizations.py`) já desativa vínculos, revoga API keys, apaga convites e
   faz o soft delete; basta tirar a chamada `assinaturas.encerrar` e o parâmetro
   `assinaturas`. Saem `cancelar_encerramento`, `efetivar_encerramento`,
   `efetivar_encerramentos_vencidos` (task + beat), `TermoEncerramento*`,
   `EncerramentoAgendado`, `EncerramentoAgendadoResponseSerializer`, a resposta
   202 e o `DELETE /organizations/{id}/encerramento/` (passa a responder 405 —
   quebra de contrato documentada em `docs/reference/api.md`).
   **Diff mínimo no schema:** `encerramento_solicitado_em`,
   `encerramento_agendado_para`, a `CheckConstraint`
   `organizacao_agendamento_exige_solicitacao` e as verificações
   `CLOSURE_PENDING` (`memberships.py`, `apps/api/autenticacao/services.py`)
   ficam como estão — inofensivas (`encerramento_agendado_para` fica sempre
   `NULL`) e evitam conflito de migration com a `enterprise`.
3. **Onboarding cria só organização + vínculo do proprietário**, sem contrato.
4. **Sem limite de membros**: some a reserva de seat em convites e a validação de
   capacidade em `memberships.py`.
5. **`Organizacao.email_faturamento` é removido** por migration nova
   (`0005_remover_email_faturamento`); 0003/0004 ficam intactas porque
   `workspaces.0001` e `organizacoes.0004` dependem da 0003. Como o
   `PUT/PATCH /organizations/{id}/` só existe para esse campo, o endpoint some.
6. **Banco volta ao modo único.** Sem `DATABASE_BILLING_MODE`, alias
   `billing_migration`, roles `billing_*`, contexto RLS `billing_ingress` nem
   credencial DDL dedicada: `make migrate` volta a ser `manage.py migrate` puro.
7. **Banco existente:** quem já rodou as migrations da `enterprise` recria o
   volume (`docker compose down -v`). Não haverá migration de limpeza; sem isso
   ficam órfãos tabelas, triggers, funções SECURITY DEFINER e roles `billing_*`.

## Fases

### Fase 1 — Remover billing (commit atômico)

`apps/assinaturas` e `organizacoes` se importam mutuamente
(`assinaturas/subscriptions.py` usa `OcupacaoSeats`, `Vinculos.calcular_ocupacao`
e `TermoEncerramento*`; as views de billing leem `request.tenant.assinatura` e
usam o marcador `regularizacao_assinatura`; `organizacoes/schema.py` importa
`BillingErrorCode` no topo). Nenhuma ordem intermediária fica verde, então tudo
abaixo entra **no mesmo commit**.

**Apagar:** `apps/assinaturas/` (inclui `subapps/faturamento`),
`api/billing_ingress_urls.py`, `tests/support/assinaturas.py`,
`tests/architecture/test_billing_boundaries.py`,
`tests/architecture/test_tenant_commercial_policy.py`,
`docker/postgres/init-billing-roles.sh`.

**`apps/organizacoes`:**
- `context.py`: `ContextoOrganizacao` só com organização + vínculo; remover
  `assinatura`, `utilizacao_seats`, `situacao_acesso`, `PoliticaComercialTenant`.
- `middleware.py`: remover `resolver_contexto_comercial` e o parâmetro
  `politica_comercial`; `_aplicar_contexto` monta o contexto direto.
- `onboarding.py`: remover Protocols, `_carregar_colaboradores_comerciais` e
  `_configuracao_onboarding`.
- `memberships.py`: remover `_bloquear_contrato_corrente`, `_validar_capacidade`,
  `_solicitar_expansao_automatica`, `_convite_reserva_seat` e chamadas;
  `calcular_ocupacao`/`OcupacaoSeats` saem se ficarem sem uso.
- `organizations.py`, `views.py`, `tasks.py`, `serializers.py`: decisão 2.
- `schema.py`: import de `BillingErrorCode`, `ORGANIZATION_RESTRICTED`, 503
  `SUBSCRIPTION_REQUIRED`, 202 de encerramento,
  `document_organization_closure_delete`.

**`apps/api/core`:** `route_markers.py` sem `regularizacao_assinatura` e
`MARCADOR_REGULARIZACAO_ASSINATURA`; ajustar `tests/test_route_markers.py`.

**`api/settings.py`:**
- `INSTALLED_APPS`: `apps.assinaturas`, `apps.assinaturas.subapps.faturamento`,
  `django_checkouts`.
- `DATABASE_BILLING_MODE` (validação e ramo `ingress` que zera
  `ENV_MIDDLEWARES`/`ENV_APPS` e troca `ROOT_URLCONF`), alias
  `billing_migration`, `DATABASE_BILLING_INGRESS_ROLE`/`OWNER_ROLE`.
- `SUBSCRIPTION_*`, `ORGANIZATION_CLOSURE_BATCH_SIZE`, `CHECKOUT_VARIANTS`,
  `FRONTEND_PAYMENT_*`, `BILLING_RECONCILIATION_*`.
- `SILENCED_SYSTEM_CHECKS` de `django_checkouts`/`faturamento`.
- `CELERY_TASK_ROUTES` de `faturamento.*`; beat `assinaturas.*`,
  `faturamento.*` e `close-expired-organizations`.
- `billing_ingress` em `REGISTERED_CONTEXT_KEYS`.
- `SENTRY_BEFORE_SEND`: `/faturamento/webhooks/` e `stripe-signature`.

**Demais:** `api/urls.py` (rotas de billing); `utils/env.py` (`SUBSCRIPTION_*`,
`STRIPE_*`, `DATABASE_BILLING_*`, `ORGANIZATION_CLOSURE_BATCH_SIZE`, aliases
`BILLING_*`/`ASSINATURAS_*`); `.env.example`; `uv remove django-checkouts`
(`pyproject.toml` + `uv.lock`).

**Infra que quebra junto com o settings:**
- `Makefile`: `migrate` volta a ser `manage.py migrate`; remover
  `billing-bootstrap`, `billing-migrate`, `billing-ingress-worker`, variáveis
  `DATABASE_BILLING_*` e `BILLING_COMPOSE_FILE`, entradas no `.PHONY` e o
  `--add-host billing_ingress` do `nginx-test`.
- `docker-compose.yml`, `.devcontainer/docker-compose.yml`,
  `.devcontainer/devcontainer.json` (`runServices`): serviços
  `billing_ingress`/`billing_ingress_worker`, variáveis `DATABASE_BILLING_*` e o
  mount do `init-billing-roles.sh`.
- `docker/nginx/sites/*/default.conf`: upstream `billing_ingress_backend` e
  locations `/faturamento/webhooks`.
- `.github/workflows/ci.yml`: passo "Provisionar roles de faturamento".

**Testes a ajustar:**
- `api/tests/test_database_settings.py`: remover testes de checks silenciados de
  billing, `ROOT_URLCONF` de ingresso, Sentry/webhook, filas/beat,
  `billing_migration` e leitura do Makefile.
- `apps/api/base/tests/test_auditlog.py`: import de models de assinaturas.
- Autenticação (usam `garantir_assinatura_corrente`):
  `passwords/test_recent_auth.py`, `tokens/test_sessions_api.py`,
  `api_keys/test_api_keys.py`, `api_keys/test_api_key_tenancy.py`.
- `apps/workspaces/tests/test_context.py` (monta contexto com assinatura e passa
  `politica_comercial=`), `apps/workspaces/tests/test_rls.py`.
- `apps/organizacoes/tests/`: `test_api` (inclui `billing.seat_limit_reached`),
  `test_schema`, `test_tasks`, `test_closure`, `test_lifecycle_api`,
  `test_memberships`, `test_onboarding`, `test_tenant_context`.

**Nova guarda:** `tests/architecture/test_internal_sem_billing.py` falha se
existir `apps/assinaturas`; houver import de `apps.assinaturas` ou
`django_checkouts`; houver uso de `tenant.assinatura`, `utilizacao_seats` ou
`situacao_acesso`; alguma migration depender de `assinaturas`/`faturamento`.

**Verificação:** `make lint`, `make test`, `make nginx-test`,
`makemigrations --check --dry-run` e `manage.py migrate` num banco **recriado**
(o `--check` sozinho não pega `NodeNotFoundError`).

### Fase 2 — `email_faturamento` e endpoint de update

- Migration `organizacoes/0005_remover_email_faturamento`.
- `OrganizacaoViewSet`: remover `UpdateModelMixin`, `update`/`partial_update`,
  `get_serializer_class` e o `extend_schema_view`; tirar `update`/
  `partial_update` de `session_only_actions`.
- Remover `OrganizacaoEmailFaturamentoSerializer`,
  `document_organization_billing_email_update`,
  `Organizacoes.atualizar_email_faturamento`, o `email_faturamento` em
  `Organizacoes.criar`, `admin.readonly_fields` e `mask_fields`.
- Ajustar `test_auditlog.py` (asserção de `mask_fields`).
- Guarda da Fase 1 passa a falhar também se aparecer `email_faturamento` fora das
  migrations 0003/0005.
- Verificação: igual à Fase 1.

### Fase 3 — Bruno e documentação

- `.bruno/Assinaturas/`, `.bruno/Organizacoes/update-email-faturamento.bru`,
  `.bruno/Organizacoes/encerramento-cancelar.bru`.
- Apagar `docs/how-to/operar-assinaturas.md` e
  `docs/reference/catalogo-de-planos.md` (e do `nav` do `mkdocs.yml`).
- Revisar `README.md`, `docs/explanation/arquitetura.md`,
  `docs/explanation/autenticacao.md`, `docs/how-to/desenvolvimento-local.md`,
  `docs/tutorial/primeiro-ambiente.md`, `docs/reference/api.md` (endpoints
  removidos, 405 do encerramento), `docs/reference/convencoes.md`,
  `docs/reference/glossario.md`, `docs/how-to/resetar-migrations.md`,
  `docs/how-to/usar-servidor-mcp.md`, `docs/ROADMAP.md`.
- Registrar que esta é a variante `internal` (README + CLAUDE.md), com a nota do
  banco existente (decisão 7).
- Verificação: `make docs`.

### Fase 4 — Verificação final

- `grep -riE "assinatur|faturament|billing|stripe|checkout|seat|trial"` fora das
  exceções deve voltar vazio. **Exceções:** `.ai/`, `docs/superpowers/`,
  `docs/CHANGELOG.md`, `docs/research/`, `docs/relatorio-base-gente-grande.md`,
  `.superpowers/`, `.trail/`. **Falsos positivos aceitos:**
  `actions/checkout@v4` (`.github/workflows/*`, `.ci/Jenkinsfile`),
  "assinatura" no sentido de *signature* (`apps/api/autenticacao/models.py`,
  `google/test_login.py`), `"apps.faturamento"` em `test_start_api_app.py`.
- `make lint`, `make test`, `make docs`, `make nginx-test`, `make check`,
  `makemigrations --check --dry-run`, e `make stack` subindo sem os serviços de
  billing.

## Sincronização com a `enterprise`

- Fluxo de mão única: `git merge enterprise` dentro de `internal`.
- Conflito *modify/delete* em `apps/assinaturas/**`: resolver com `git rm`.
- **Arquivos novos** da `enterprise` em `apps/assinaturas/**`,
  `.bruno/Assinaturas/**` ou docs de billing entram **sem conflito**. Depois de
  todo merge: `git diff --name-only --diff-filter=A HEAD^1 HEAD -- apps/assinaturas .bruno/Assinaturas`
  e apagar o que aparecer. A guarda de arquitetura pega o que escapar.
- Conflitos em `organizacoes` (sobretudo `memberships.py` e `middleware.py`),
  `settings.py`, `Makefile` e compose: resolver à mão, mantendo a ausência de
  billing.
- Migrations: `organizacoes.0005_*` vinda da `enterprise` exige
  `makemigrations --merge`. Migrations que dependam de `assinaturas`/
  `faturamento` ou alterem `email_faturamento` exigem edição manual
  (`--merge` não resolve).
- Código novo que leia `request.tenant.assinatura` importa sem erro e só falha em
  runtime/teste — a guarda de arquitetura cobre.
- CI e release da `internal`: fora do escopo (hoje a CI só roda em push para
  `enterprise`; em PR roda normalmente).
