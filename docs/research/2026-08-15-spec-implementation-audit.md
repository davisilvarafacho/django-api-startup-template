# Auditoria de implementação das specs do repositório

Data da auditoria: 2026-08-15  
Escopo principal: `.ai/brainstorming/spec/`, planos relacionados, histórico
Git, código, testes, CI e configuração local necessária pela spec. Como
recorte secundário, as 31 specs legadas de `docs/superpowers/specs/` também
foram confrontadas com o repositório atual para identificar pendências que
ainda não foram promovidas para a convenção nova.

## Conclusão

O inventário tem **32 specs**: 1 atual e 31 históricas. A classificação é:

- **25 implementadas**;
- **5 não implementadas**;
- **1 parcial**;
- **1 substituída/obsoleta**;
- **0 inconclusivas**.

O diretório atual de brainstorming contém **uma spec, ainda não
implementada**:

| Spec | Classificação | Evidência decisiva |
| --- | --- | --- |
| `.ai/brainstorming/spec/2026-08-15-migration-reset-and-test-database.md` | **Não implementada** | Foi adicionada sozinha pelo commit `5567407d76aef4946564f794317972c720602357`, que ainda é o `HEAD`; não existe commit posterior em nenhuma ref local. Os entregáveis centrais continuam ausentes e o repositório conserva explicitamente o estado anterior ao reset. |

Não há specs **parciais**, **implementadas**, **substituídas/obsoletas** ou
**inconclusivas** nesse diretório atual. As demais classificações abaixo são do
acervo histórico; as diretrizes do repositório deixam claro que novas specs não
devem voltar para esse local.

## Pendências encontradas

| Spec | Classificação | Evidência decisiva |
| --- | --- | --- |
| `.ai/brainstorming/spec/2026-08-15-migration-reset-and-test-database.md` | **Não implementada** | Commit documental `5567407`; comando, testes e alvo Make ausentes; `Makefile:91-100` e `.github/workflows/ci.yml:78` ainda usam `--nomigrations`; `api/settings.py:224` ainda usa o nome legado. |
| `docs/superpowers/specs/2026-08-10-model-permission-policy-design.md` | **Não implementada** | O commit `208ed75` alterou somente a spec. Não existem `ResourcePolicy`, `ActionPolicy`, `ModelPermissionMixin` nem `authorization_policy`; `apps/api/base/views.py` ainda usa `PermissionsViewSetMixin` e `apps/api/core/scope_mixins.py` mantém o mapeamento legado por action. |
| `docs/superpowers/specs/2026-08-13-context-variables-framework-design.md` | **Não implementada** | O commit `033cfad` adicionou somente a spec. Não existe `internal_frameworks/context/`; `pyproject.toml:34`, `api/settings.py:164`, `api/logging_config.py:71` e consumidores em `apps/api/base/` continuam usando `django-threadlocals`. |
| `docs/superpowers/specs/2026-08-13-field-write-permissions-design.md` | **Não implementada** | O commit `023ede6` foi documental. Não existem `FieldWritePolicy`, `FieldGrant` ou `FieldAccessManager`; `docs/ROADMAP.md:128-136` ainda marca field-level permissions como trabalho para fechar o batch. |
| `docs/superpowers/specs/2026-08-13-mini-mcp-django-health-design.md` | **Não implementada** | O commit `81e1d30` adicionou somente a spec. Não há pacote `mcp_server/`, dependência `mcp` em `pyproject.toml`, testes ou configuração de execução. |
| `docs/superpowers/specs/2026-08-13-account-organization-subscriptions-billing-design.md` | **Parcial** | O commit `3cfd12f` foi documental. Login local e o núcleo de organizações já existem em `apps/api/autenticacao/` e `apps/organizacoes/`, mas faltam verificação/troca de e-mail, login Google, ciclo de exclusão da conta e todos os apps/contratos de assinatura e faturamento; não existem `apps/assinaturas/`, seu subapp `faturamento` nem dependência `django-checkouts`. O ROADMAP confirma a pendência em `docs/ROADMAP.md:128-136`. |

Assim, em termos de trabalho ainda aberto, há **cinco specs inteiramente sem
implementação e uma parcialmente implementada**.

## Evidências

### Histórico e planejamento

- `git show --stat 5567407` mostra que o commit adicionou somente as 256 linhas
  da spec, sem código, testes ou configuração.
- `git log --all --since='2026-08-15T14:25:50-03:00'` retorna apenas esse
  commit. Portanto não houve uma implementação posterior registrada em outra
  ref local.
- `.ai/brainstorming/plan/` contém apenas `.gitkeep`; não existe plano de
  implementação relacionado.

### Comando e testes propostos

- A spec define `manage.py reset_migrations` e um alvo Make
  `reset-migrations` em
  `.ai/brainstorming/spec/2026-08-15-migration-reset-and-test-database.md:52-57`.
- Não existe
  `apps/api/core/management/commands/reset_migrations.py`, nem teste dedicado
  ao comando. A listagem versionada do diretório contém apenas os comandos
  preexistentes de invalidação do cache, migração de storage, rotação de
  campos sensíveis, seed e criação de apps.
- A verificação dinâmica
  `uv run python manage.py reset_migrations --help`, com as variáveis locais
  mínimas, termina com `Unknown command: 'reset_migrations'`.
- O `Makefile` não declara `reset-migrations` e sua lista `.PHONY` também não
  o inclui (`Makefile:1`).

### Migrations e banco de testes

- A consolidação ainda não ocorreu: há 22 migrations numeradas nos apps
  próprios, inclusive sequências incrementais como
  `apps/api/autenticacao/migrations/0001_initial.py` até
  `0009_alter_authtoken_type_and_more.py`,
  `apps/organizacoes/migrations/0002_soft_delete.py` e
  `0003_reconcile_audit_timestamps.py`, e equivalentes em `apps/usuarios/`.
- A migration manual que a spec pretende preservar já existia antes dela:
  `apps/api/core/migrations/0001_schedule_access_log_cleanup.py` entrou em
  `201c5503` e teve sua dependência ajustada em `72c6f6c`. Isso é
  pré-requisito preexistente, não implementação parcial do reset.
- O banco de testes continua com o default legado
  `test_base_permission_cache` em `api/settings.py:223-225`, reforçado pelo
  teste `internal_frameworks/permission_cache/tests/test_config.py:10-12`.
  Esse contrato preexistente veio do commit `dfa6379`; a spec exige
  `base_test` (`:170-179`).
- Não foi executado um reset destrutivo nem tentada a validação integrada
  contra o banco local durante esta auditoria somente leitura.

### Suíte, CI e documentação

- Os quatro alvos de teste continuam passando `--nomigrations`
  (`Makefile:90-100`).
- O workflow principal também executa `pytest --nomigrations`
  (`.github/workflows/ci.yml:77-78`) e não define `TEST_DATABASE_NAME` em seu
  ambiente (`:59-70`).
- O gate `makemigrations --check --dry-run` continua ausente; o próprio
  workflow manda restaurá-lo somente junto do reset
  (`.github/workflows/ci.yml:35-36`).
- O template de PR ainda exige a política temporária de migrations
  (`.github/PULL_REQUEST_TEMPLATE.md:14`), o fluxo de desenvolvimento ainda
  descreve testes sem migrations
  (`docs/how-to/fluxo-de-desenvolvimento.md:46-54`) e o ROADMAP ainda registra
  o congelamento como pendência operacional (`docs/ROADMAP.md:137-139`).
- Esse estado foi introduzido explicitamente pelo commit `68ae094` e ainda não
  foi revertido pelos entregáveis da spec.

### MCP PostgreSQL

- A spec exige DBHub/PostgreSQL somente leitura na configuração local
  (`:198-225`). A inspeção dos nomes de seção de
  `~/.codex/config.toml`, sem exibir valores ou credenciais, encontrou apenas
  `node_repl` e `agentmemory`; não há referência a DBHub ou PostgreSQL.
- Sem servidor configurado, também não há evidência de que o papel PostgreSQL
  dedicado, os grants de leitura ou os testes de escrita recusada tenham sido
  aplicados.

## Interpretação

Algumas fundações citadas pela spec já existem — `BUSINESS_APPS`, a migration
manual do Celery Beat e suporte a `TEST_DATABASE_NAME` —, mas todas antecedem a
spec e ela pede sua transformação ou orquestração. Como nenhum entregável
central existe, classificá-la como parcial inflaria o progresso; a classificação
sustentada pelo código e pelo histórico é **não implementada**.

## Inventário histórico completo

Este inventário usa `docs/superpowers/specs/` apenas como acervo legado. Uma
spec classificada como pendente aqui não muda a regra de destino: qualquer
continuação deve gerar spec/plano novo em `.ai/brainstorming/`, ou declarar
explicitamente a adoção do documento antigo.

| Spec histórica | Classificação | Evidência primária resumida |
| --- | --- | --- |
| `2026-07-28-api-errors-design.md` | **Implementada** | `apps/api/core/errors.py` e sua suíte; commits `ec80864`, `16ec1a0`, `ec7c8f8` e `dc5a032`. |
| `2026-07-28-auth-tokens-api-keys-design.md` | **Implementada** | Models, serviços, views e testes em `apps/api/autenticacao/`; série de implementação de `9d6a21e` a `c49c369`, com correções posteriores. |
| `2026-07-28-django-anymail-resend-design.md` | **Implementada** | Backend/configuração em `api/settings.py`, documentação no README e commit `4a9e423`. |
| `2026-07-28-sensitive-fields-design.md` | **Implementada** | `internal_frameworks/sensitive_fields/` e testes; commits `d196bc9` e `30f60b0`. |
| `2026-07-29-devcontainer-design.md` | **Implementada** | `.devcontainer/`; commits `51c36fb`, `26434ad` e `94780da`. |
| `2026-07-29-mfa-password-security-design.md` | **Implementada** | Models, endpoints e testes MFA/senha em `apps/api/autenticacao/` e `apps/usuarios/`; commits `14ddef2`, `0fd4809`, `513916c`, `b047619`, `d3b2f5b`, `54403d3` e `0a2be38`. |
| `2026-07-29-permission-cache-design.md` | **Implementada** | `internal_frameworks/permission_cache/`, integrações e testes; commits `ac07d15`, `c7d7696`, `ac685be`, `1f4f192`, `a77d324`, `12c8b5d`, `632be1` e `5802f28`. |
| `2026-07-29-seed-demo-data-design.md` | **Implementada** | `apps/api/core/management/commands/seed_demo.py` e testes; commit `5604824`. |
| `2026-07-29-soft-delete-design.md` | **Implementada** | `SoftDeleteMixin`, managers e testes em `apps/api/base/` e `apps/organizacoes/`; commit `50e3ed5`. |
| `2026-07-29-start-api-app-design.md` | **Implementada** | `apps/api/core/management/commands/start_api_app.py`, template e testes; commits `b31d856`, `ccf54a8`, `32ed9e4` e `3b09c13`. |
| `2026-07-29-storage-migration-design.md` | **Implementada** | `apps/api/core/storage_migration.py`, comando e testes; commits `07e921f`, `6d721ef`, `13a5359` e `8398e46`. |
| `2026-07-30-django-axes-design.md` | **Implementada** | Configuração, handlers, task, migration manual e testes; commits `3347f7c`, `2e5924c`, `6df5a82`, `201c550`, `72c6f6c` e `90c3c1c`. |
| `2026-07-30-local-test-database-design.md` | **Substituída/obsoleta** | O contrato antigo foi implementado por `690e2db`, `038dcae` e `dfa6379`, mas a spec atual de reset declara `test_base_permission_cache` legado e o substitui por `base_test` (`.ai/brainstorming/spec/2026-08-15-migration-reset-and-test-database.md:16-19,170-179`). |
| `2026-07-31-mfa-otp-delivery-design.md` | **Implementada** | Entrega segura em `apps/api/autenticacao/mfa.py` e testes; commit `a8e0349`. |
| `2026-07-31-mfa-totp-replay-design.md` | **Implementada** | Estado/validação de replay e testes no app de autenticação; commit `813c0fc`. |
| `2026-07-31-trusted-device-revocation-design.md` | **Implementada** | Revogação em alterações de segurança e testes; commit `0b80fe4`. |
| `2026-08-03-as-curl-design.md` | **Implementada** | `utils/curl.py` e testes; commits `237d88b`, `9fbf6c8` e `ebdaac2`. |
| `2026-08-03-guardrails-framework-design.md` | **Implementada** | `internal_frameworks/guardrails/`, checks, testes e referência; commit `4cae82b`. |
| `2026-08-06-devcontainer-observability-alloy-design.md` | **Implementada** | `observability/config.alloy`, compose/configuração e testes; commit `98069c6`. |
| `2026-08-09-base-model-mixins-design.md` | **Implementada** | Composição atual em `apps/api/base/models.py` e testes; commit `30af303`. |
| `2026-08-09-base-model-serializer-field-policies-design.md` | **Implementada** | `FieldPolicyMixin`, `BaseModelSerializer` e testes; commits `c44e840`, `d7d74bc`, `3af2ec3` e `484efbd`. |
| `2026-08-09-importacoes-entre-modulos-design.md` | **Implementada** | Política normativa em `docs/adr/0006-importacoes-entre-modulos.md`, `AGENTS.md` e `CLAUDE.md`; imports canônicos do metadata no commit `ab6285c`. |
| `2026-08-09-metadata-api-design.md` | **Implementada** | `apps/api/metadata/`, action no `BaseModelViewSet` e testes; commits `3b59260`, `0dd2da8`, `17152a5`, `3b226c8`, `f3a5b86`, `dcd9917` e `9ce5383`. |
| `2026-08-09-rastreamento-de-campos-design.md` | **Implementada** | `ChangeTrackingMixin` e `apps/api/base/tests/test_change_tracking.py`; commit `30af303`. |
| `2026-08-09-test-suite-organization-design.md` | **Implementada** | Layout atual de testes e configuração de coleta; commits `0e654ad`, `3f0404d`, `ec940f9`, `f1f6e60` e `4a78462`. |
| `2026-08-10-model-permission-policy-design.md` | **Não implementada** | Commit documental `208ed75`; as classes e o contrato declarativo da spec não existem, e o código ainda usa os mixins anteriores. |
| `2026-08-12-public-routes-in-urls-design.md` | **Implementada** | Descoberta em `urls.py`, registry e testes; commit `206a9b7`, documentado por `78ee725`. |
| `2026-08-13-account-organization-subscriptions-billing-design.md` | **Parcial** | Fundação de autenticação/organizações presente, mas toda a camada nova de ciclo de conta, assinaturas e faturamento está ausente; commit da spec `3cfd12f`. |
| `2026-08-13-context-variables-framework-design.md` | **Não implementada** | Commit documental `033cfad`; `internal_frameworks/context/` ausente e `django-threadlocals` ainda ativo. |
| `2026-08-13-field-write-permissions-design.md` | **Não implementada** | Commit documental `023ede6`; models, serviços e integração propostos ausentes. |
| `2026-08-13-mini-mcp-django-health-design.md` | **Não implementada** | Commit documental `81e1d30`; pacote, dependência e testes ausentes. |

Nenhuma das 31 specs históricas ficou **inconclusiva**: em todos os casos o
histórico e a superfície atual permitiram confirmar presença, ausência,
implementação parcial ou substituição do contrato.
