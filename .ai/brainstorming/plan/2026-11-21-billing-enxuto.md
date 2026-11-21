# Billing enxuto — plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** App `apps/billing` que espelha a assinatura do gateway (via `django-checkouts`) e cobre assinar, upgrade, downgrade e cancelar, com limite de membros por plano, webhook idempotente e reconciliação periódica.

**Architecture:** O gateway é a fonte da verdade. Toda escrita do espelho passa por `Subscriptions.sync(organization)`, que busca a assinatura remota sob lock e sobrescreve a linha local. Webhook e reconciliação só gravam eventos numa caixa de entrada e enfileiram a sincronização. A organização de um evento vem do `reference_id` gravado na assinatura do gateway.

**Tech Stack:** Django 5.2, DRF, django-rls, Celery, `django-checkouts[stripe]`, pytest.

**Spec:** `.ai/brainstorming/spec/2026-11-21-billing-enxuto-design.md` — leia antes de começar qualquer tarefa.

## Global Constraints

- Branch `refactor/billing-enxuto`. Nunca faça push.
- Leia `CLAUDE.md`, `docs/reference/convencoes.md` e carregue a skill `django-api` antes de criar models, serializers, views, filters ou migrations. Antes do primeiro grep/leitura de código, rode `graphify query "<pergunta>"` e leia arquivos brutos só nas linhas que o grafo ou uma leitura anterior apontarem.
- Idioma: identificadores em **inglês**; docstrings, comentários, `help_text`, `db_comment`, `verbose_name` e mensagens em **português**. Rotas em português, sob `/cobranca/`.
- Todo field declara `help_text` e `db_comment` com o mesmo valor, na ordem de argumentos de `convencoes.md`. Todo model tem `Meta` com `db_table`, `ordering = ("-id",)`, `verbose_name`, `verbose_name_plural`, `permissions`, e implementa `__str__`.
- Choices no **topo** do `models.py`, com alias dentro da classe (`Subscription.Status = SubscriptionStatus`).
- `Plan` e `BillingEvent` herdam de `BaseTenantless` (o "BaseGlobal" do CLAUDE.md). `Subscription` herda de `Base` (sob RLS). Import: `from apps.api.base.models import Base, BaseTenantless`.
- Leitura de ambiente só por `utils/env.py`. Toda variável nova entra em `EnviromentVar` (`utils/env.py`) e no `.env.example` (um teste compara as fontes).
- Erros de API: `raise APIError(BillingErrorCode.X, status_code=N)` (`apps.api.core.errors`). Os códigos vivem em `apps/billing/errors.py`, que é descoberto automaticamente.
- Comando de teste (`$PYTEST`): `DATABASE_NAME=base DATABASE_HOST=127.0.0.1 DATABASE_PORT=5432 DATABASE_TEST_NAME=base_test DATABASE_USER=postgres DATABASE_PASSWORD=postgres uv run --group test pytest <caminhos> -q --no-cov -p no:cacheprovider`. Postgres e Redis precisam estar no ar (`make up`).
- Migrations: `DJANGO_ENVIRONMENT=test uv run python manage.py makemigrations <app>`. Ao final de cada tarefa com model: `DJANGO_ENVIRONMENT=test uv run python manage.py makemigrations --check --dry-run` → `No changes detected`.
- Lint ao final de cada tarefa: `make lint` → `All checks passed!`.
- Commits em Conventional Commits, em português, terminando com `Co-Authored-By: Claude Haiku <noreply@anthropic.com>`. Um commit por tarefa.
- Falhas pré-existentes que **não** são desta tarefa: `apps/api/core/tests/management_commands/test_start_api_app.py` (2 testes) e `test_cached_guardian_backend_replaces_upstream_backend` (só falha na suíte completa).
- Tarefas fora de request (Celery, testes) acessam `Subscription` só dentro de `organization_scope(org_id)` (Task 2).

## Review Focus

1. Duas syncs simultâneas da mesma organização (webhook + reconciliação): a segunda espera o lock e grava o estado mais novo. Teste em Task 4: `test_sync_reads_remote_inside_lock` (a chamada `retrieve` acontece depois do `select_for_update`).
2. Evento de assinatura que chega antes do `checkout.paid`: a organização vem do `reference_id`, então o evento deve funcionar mesmo assim. Teste em Task 7: `test_process_subscription_event_before_checkout_links_and_syncs`.
3. Assinatura remota cujo `reference_id` aponta para outra organização: nada é gravado. Teste em Task 4: `test_sync_rejects_reference_mismatch`.
4. Convite aceito numa rota sem tenant (sem contexto RLS ativo): a checagem de capacidade não pode quebrar com `RLSContextRequiredError`. Teste em Task 3: `test_accept_invite_without_active_tenant_checks_capacity`.
5. Organização encerrada: `apply` não reativa ninguém e o cancelamento no gateway é imediato. Teste em Task 8: `test_closed_organization_cancels_immediately_and_reactivates_nobody`.

---

### Task 1: Dependência, app, configuração e gateway

**Files:**
- Modify: `pyproject.toml`, `uv.lock` (`uv add "django-checkouts[stripe]==1.0.1"`; a Task 10 troca para 1.1.0)
- Create: app `apps/billing` com `uv run python manage.py start_api_app billing` (registra em `BUSINESS_APPS`)
- Create: `apps/billing/gateway.py`, `apps/billing/errors.py`
- Modify: `api/settings.py`, `utils/env.py`, `.env.example`
- Test: `apps/billing/tests/test_gateway.py`, `tests/support/billing.py`

**Interfaces:**
- Produces:
  - `apps.billing.gateway.get_client() -> CheckoutClient` — `get_checkout_gateway(settings.BILLING_GATEWAY_VARIANT)`.
  - `apps.billing.gateway.translate_gateway_errors()` — context manager que converte `django_checkouts.exceptions.CheckoutError` em `APIError`: `GatewayTemporaryError`, ou `retry_advice.disposition` em `{RETRY, RETRY_SAME_KEY, RECONCILE_FIRST}` → `503 GATEWAY_UNCERTAIN`; qualquer outro `CheckoutError` → `502 GATEWAY_ERROR`.
  - `apps.billing.errors.BillingErrorCode(models.TextChoices)`: `MEMBER_LIMIT_REACHED = "billing.member_limit_reached"` (409), `DOWNGRADE_BLOCKED = "billing.downgrade_blocked"` (409), `ALREADY_SUBSCRIBED = "billing.already_subscribed"` (409), `NOT_SUBSCRIBED = "billing.not_subscribed"` (409), `SAME_PLAN = "billing.same_plan"` (422), `PLAN_UNAVAILABLE = "billing.plan_unavailable"` (422), `IDEMPOTENCY_KEY_REQUIRED = "billing.idempotency_key_required"` (422), `GATEWAY_UNCERTAIN = "billing.gateway_uncertain"` (503), `GATEWAY_ERROR = "billing.gateway_error"` (502). Labels em português.
  - Settings: `CHECKOUT_VARIANTS = {"stripe": ("django_checkouts.gateways.stripe.StripeGateway", {"secret_key": STRIPE_SECRET_KEY, "webhook_secret": STRIPE_WEBHOOK_SECRET})}` (confira os nomes exatos dos kwargs no construtor do gateway Stripe da lib); `BILLING_GATEWAY_VARIANT` (default `"stripe"`), `FRONTEND_BILLING_CHECKOUT_SUCCESS_URL`, `FRONTEND_BILLING_CHECKOUT_CANCEL_URL` (default `http://localhost:3000/cobranca/checkout/sucesso` e `.../cancelado` fora de produção, `""` em produção), `BILLING_FREE_PLAN_MEMBER_LIMIT` (int, 3), `BILLING_RECONCILE_WINDOW_HOURS` (int, 72), `BILLING_EVENT_MAX_ATTEMPTS` (int, 10). Env vars de mesmo nome. Adicione `"django_checkouts"` a `LIBS_APPS` se a lib exigir (veja o `apps.py` dela).
  - `tests/support/billing.py`: `fake_client(results: dict) -> CheckoutClient` (com `FakeCheckoutGateway(results=..., capabilities=STRIPE_CAPABILITIES, variant="stripe")`) e `remote_subscription(**overrides) -> django_checkouts.types.subscriptions.Subscription` com defaults válidos (`external_id="sub_1"`, `status=ACTIVE`, um item com `price_id="price_pro"`, `reference_id=None`, `metadata={}`, `raw={}`).

- [ ] **Step 1: Escrever os testes que falham** — `test_translate_temporary_error_returns_503` (levanta `GatewayTemporaryError` dentro do context manager → `APIError` com `status_code == 503` e `code == "billing.gateway_uncertain"`), `test_translate_permanent_error_returns_502`, `test_get_client_uses_configured_variant` (com `settings.BILLING_GATEWAY_VARIANT` apontando para uma variante fake em `CHECKOUT_VARIANTS` via `settings` fixture → `client.variant == "fake"`).
- [ ] **Step 2: Rodar** `$PYTEST apps/billing/tests/test_gateway.py` → FAIL (módulo inexistente).
- [ ] **Step 3: Implementar** dependência, app, settings, env, `gateway.py`, `errors.py` e `tests/support/billing.py`.
- [ ] **Step 4: Rodar** `$PYTEST apps/billing/tests/test_gateway.py utils/tests` → PASS. `uv run python manage.py check` sem erros com `DJANGO_ENVIRONMENT=test`.
- [ ] **Step 5: Commit** `feat(billing): criar app de cobrança e cliente do gateway`.

### Task 2: Models, plano gratuito e escopo de organização

**Files:**
- Create: `apps/billing/models.py`, `apps/billing/handlers.py`, `apps/billing/context.py`, migrations de `billing` e `organizacoes`
- Modify: `apps/billing/apps.py` (conectar `post_migrate`), `apps/organizacoes/models.py` (coluna no `Vinculo`)
- Test: `apps/billing/tests/test_models.py`, `apps/billing/tests/test_handlers.py`, `apps/billing/tests/test_context.py`

**Interfaces:**
- Produces (campos exatamente como na seção "Modelo de dados" da spec):
  - Choices no topo: `SubscriptionStatus(TextChoices)` com os 8 valores da lib (`incomplete`, `trialing`, `active`, `past_due`, `paused`, `unpaid`, `canceled`, `expired`); `BillingCycle(TextChoices)` com os valores de `django_checkouts.enums.BillingCycle`. Comentário explicando por que `TextChoices`: valores iguais aos da lib, conversão por `SubscriptionStatus(remote.status.value)`.
  - `Plan(BaseTenantless)`, `db_table="billing_plan"`, `Cycle = BillingCycle`. `__str__` → `name`. Classmethod `Plan.get_free() -> Plan`.
  - `Subscription(Base)`, `db_table="billing_subscription"`, `Status = SubscriptionStatus`; constraint `UniqueConstraint(fields=["organizacao"], name="billing_subscription_unique_organization")`; constantes `ACCESS_STATUSES`, `TERMINAL_STATUSES`, `ABANDONED_STATUSES` e properties `is_free`, `has_access`, `is_cancel_scheduled`, `effective_plan`, `member_limit` como na spec. FKs `plan`/`pending_plan` com `on_delete=PROTECT`, `related_name="+"`.
  - `BillingEvent(BaseTenantless)`, `db_table="billing_event"`: campos da spec mais `subscription_external_id` (`CharField` nulo: id da assinatura no gateway, extraído do recurso no momento do registro). `UniqueConstraint(fields=["gateway_variant", "event_id"])`; índice parcial em `occurred_at` com `condition=Q(processed_at__isnull=True)`.
  - `Vinculo.plan_limit_suspended_at` (`DateTimeField(null=True, blank=True)`, help_text/db_comment "Momento em que a cobrança desativou o vínculo por limite do plano; vazio quando não suspenso pela cobrança.").
  - `apps.billing.context.organization_scope(organization_id: int)` — context manager: se `django_rls.context.get_active_rls_context().get("tenant_id")` já é `str(organization_id)`, não faz nada; senão entra em `apps.organizacoes.context.organizacao_atual(organization_id)`.
  - `apps.billing.handlers.ensure_free_plan(**kwargs) -> None` — `post_migrate` (só quando `app_config.label == "billing"`): cria o plano gratuito se não houver `is_free=True` (`name="Gratuito"`, `slug="gratuito"`, `rank=0`, `member_limit=settings.BILLING_FREE_PLAN_MEMBER_LIMIT`). Idempotente.
- Se o check `base.W004` exigir, declare a constraint de `Subscription` em `indexes_sem_workspace_obrigatorio`.

- [ ] **Step 1: Escrever os testes que falham:**
  - DB-less, com instâncias não salvas: `test_has_access_by_status` (parametrize: `active`, `trialing`, `past_due` → True; `incomplete`, `paused`, `unpaid`, `canceled`, `expired` → False); `test_effective_plan_falls_back_to_free_without_access`; `test_member_limit_uses_effective_plan`; `test_is_cancel_scheduled_requires_cancel_at_and_access`.
  - Com banco: `test_only_one_free_plan_allowed` (segundo `is_free=True` → `IntegrityError`); `test_ensure_free_plan_is_idempotent` (chamar duas vezes → 1 plano gratuito, com `member_limit == 3`); `test_one_subscription_per_organization`; `test_billing_event_unique_per_variant_and_event_id`; `test_organization_scope_noop_when_same_tenant_active` e `test_organization_scope_opens_context_when_none_active` (dentro do escopo, `Subscription.objects.count()` não levanta `RLSContextRequiredError`).
- [ ] **Step 2: Rodar** `$PYTEST apps/billing/tests/test_models.py apps/billing/tests/test_handlers.py apps/billing/tests/test_context.py` → FAIL.
- [ ] **Step 3: Implementar** models, migrations (`makemigrations billing organizacoes`), handler e `context.py`.
- [ ] **Step 4: Rodar** os mesmos testes → PASS; `makemigrations --check --dry-run` → `No changes detected`; `$PYTEST apps/organizacoes apps/workspaces` → sem regressões.
- [ ] **Step 5: Commit** `feat(billing): modelar plano, assinatura e caixa de eventos`.

### Task 3: Limite de membros

**Files:**
- Create: `apps/billing/members.py`
- Modify: `apps/organizacoes/memberships.py` (`criar_convite`, `aceitar_convite`, `atualizar_vinculo`)
- Test: `apps/billing/tests/test_members.py`

**Interfaces:**
- Consumes: `Subscription`, `Plan.get_free()`, `organization_scope` (Task 2).
- Produces `class MemberLimits` em `apps/billing/members.py`:
  - `active_count(cls, organization_id: int, *, using: str = "default") -> int` — vínculos com `is_active=True`, `is_deleted=False`.
  - `limit_for(cls, organization_id: int) -> int` — `Subscription.member_limit` sob `organization_scope`; sem `Subscription`, o limite do plano gratuito.
  - `ensure_capacity(cls, organization_id: int, *, count_pending_invites: bool = False, using: str = "default") -> None` — se `active_count` (+ convites pendentes: `aceito_em` nulo, não excluídos, não expirados) `>= limit_for` → `APIError(BillingErrorCode.MEMBER_LIMIT_REACHED, status_code=409, context={"limit": limite})`.
  - `apply(cls, organization_id: int, *, now: datetime | None = None, using: str = "default") -> None` — trava `Organizacao` com `select_for_update`. Se a organização estiver inativa ou excluída, retorna sem fazer nada. Acima do limite: desativa os ativos mais recentes por `created_at` (desempate por `pk`), pulando `Papel.PROPRIETARIO`, com `is_active=False` e `plan_limit_suspended_at=now`. Abaixo do limite: reativa os que têm `plan_limit_suspended_at` não nulo, do mais antigo para o mais novo (ordem de `plan_limit_suspended_at`, depois `created_at`), limpando a marca, até o limite. Sempre `save(update_fields=[...])`.
- Hooks em `Vinculos` (dentro das transações e locks que já existem, depois de `_bloquear_organizacao_aberta`):
  - `criar_convite`: `MemberLimits.ensure_capacity(organizacao.pk, count_pending_invites=True, using=using)`;
  - `aceitar_convite`: `MemberLimits.ensure_capacity(convite.organizacao_id, using=using)`, só quando o vínculo **não** existe ou está inativo;
  - `atualizar_vinculo`: quando `is_active` passa de `False` para `True`, `ensure_capacity` antes de gravar e `plan_limit_suspended_at = None`.

- [ ] **Step 1: Escrever os testes que falham:** `test_apply_suspends_newest_members_above_limit` (limite 3, 5 ativos → os 2 de `created_at` mais recente ficam `is_active=False` com marca); `test_apply_never_suspends_owner` (proprietário é o mais recente → suspende o próximo); `test_apply_reactivates_oldest_suspended_first`; `test_apply_does_not_reactivate_manual_deactivation` (inativo sem marca continua inativo); `test_apply_is_idempotent`; `test_create_invite_counts_pending_invites` (2 ativos + 1 convite pendente, limite 3 → 409 `billing.member_limit_reached`); `test_accept_invite_at_limit_is_blocked`; `test_accept_invite_without_active_tenant_checks_capacity` (sem contexto RLS ativo, não levanta `RLSContextRequiredError` e bloqueia no limite); `test_manual_reactivation_clears_suspension_mark`; `test_manual_reactivation_at_limit_is_blocked`.
- [ ] **Step 2: Rodar** `$PYTEST apps/billing/tests/test_members.py` → FAIL.
- [ ] **Step 3: Implementar** `members.py` e os hooks.
- [ ] **Step 4: Rodar** `$PYTEST apps/billing/tests/test_members.py apps/organizacoes` → PASS. Testes existentes de convite que criam mais membros que o limite padrão precisam de uma `Subscription` com plano de limite maior. Ajuste-os via um helper `tests/support/billing.py::subscribe_organization(organization, *, member_limit: int) -> Subscription`, nunca afrouxando a regra.
- [ ] **Step 5: Commit** `feat(billing): limitar membros pelo plano`.

### Task 4: Serviço `Subscriptions` e `sync`

**Files:**
- Create: `apps/billing/subscriptions.py`
- Test: `apps/billing/tests/test_sync.py`

**Interfaces:**
- Consumes: `get_client` (Task 1), models e `organization_scope` (Task 2), `MemberLimits.apply` (Task 3).
- Produces `class Subscriptions` em `apps/billing/subscriptions.py`:
  - `ensure_free(cls, organization_id: int) -> Subscription` — `get_or_create` da assinatura gratuita (`plan=Plan.get_free()`, `status=ACTIVE`) sob `organization_scope`.
  - `get_current(cls, organization_id: int, *, lock: bool = False) -> Subscription`.
  - `link_external(cls, organization_id: int, *, external_id: str, gateway_variant: str) -> None` — grava `external_id`/`gateway_variant` só se o local estiver sem `external_id`. Se já houver **outro** `external_id`, loga `error` e não sobrescreve.
  - `sync(cls, organization_id: int) -> Subscription` — algoritmo da spec ("`sync(organization)`", passos 1–7), tudo dentro de `organization_scope` + `transaction.atomic()`. O `retrieve` acontece **depois** do `select_for_update`. Divergência de `reference_id` → `class ReferenceMismatch(Exception)` (definida em `subscriptions.py`), sem gravar nada. `unpaid` → `client.subscriptions.cancel(external_id, timing=CancellationTiming.IMMEDIATELY, idempotency_key=f"billing:abandon:{external_id}")` e segue como terminal. Preço sem `Plan` correspondente → mantém `plan` e `logger.error`. No fim, `MemberLimits.apply(organization_id)`.
  - Logger: `logging.getLogger("apps.billing")`.

- [ ] **Step 1: Escrever os testes que falham** (cliente via `monkeypatch.setattr("apps.billing.subscriptions.get_client", lambda: fake_client({...}))`): `test_ensure_free_is_idempotent`; `test_sync_free_without_external_id_only_applies_limit`; `test_sync_maps_price_to_plan_and_status` (remoto `price_pro`, `past_due` → `plan` Pro, `status == "past_due"`, `has_access` True); `test_sync_clears_pending_plan_when_applied`; `test_sync_terminal_resets_to_free` (`canceled` → plano gratuito, `external_id` None, `cancel_at` None); `test_sync_unpaid_cancels_remote_and_resets` (um `CancelRemoteSubscription` com `timing=IMMEDIATELY` em `gateway.commands`); `test_sync_unknown_price_keeps_plan_and_logs` (`caplog` com nível ERROR); `test_sync_rejects_reference_mismatch` (`reference_id="999"` → `ReferenceMismatch`, linha intacta); `test_sync_reads_remote_inside_lock` (o callable de `RetrieveSubscription` confere `connection.in_atomic_block` e que a linha está travada, por exemplo registrando ordem de chamadas com um `select_for_update` espionado); `test_sync_loss_of_access_suspends_members`.
- [ ] **Step 2: Rodar** `$PYTEST apps/billing/tests/test_sync.py` → FAIL.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Rodar** → PASS.
- [ ] **Step 5: Commit** `feat(billing): sincronizar o espelho da assinatura com o gateway`.

### Task 5: Onboarding e seed

**Files:**
- Modify: `apps/organizacoes/onboarding.py`, `apps/api/core/management/commands/seed_demo.py`
- Test: `apps/organizacoes/tests/test_onboarding.py`, teste existente do `seed_demo`

**Interfaces:**
- Consumes: `Subscriptions.ensure_free` (Task 4).
- `OrganizationOnboarding.criar` chama `Subscriptions.ensure_free(organizacao.pk)` depois de `Workspaces.criar_inicial`, dentro da mesma transação. `seed_demo` chama o mesmo para a organização `demo`.

- [ ] **Step 1: Testes que falham:** `test_onboarding_creates_free_subscription` (após `criar`, sob `organization_scope`, existe `Subscription` com `is_free` True e `status == "active"`); no teste do `seed_demo`, `test_seed_demo_creates_free_subscription` (rodar 2× → 1 assinatura).
- [ ] **Step 2: Rodar** → FAIL. **Step 3: Implementar.** **Step 4: Rodar** `$PYTEST apps/organizacoes apps/api/core/tests` → PASS (exceto as falhas pré-existentes listadas).
- [ ] **Step 5: Commit** `feat(billing): criar assinatura gratuita no onboarding`.

### Task 6: Operações e API

**Files:**
- Modify: `apps/billing/subscriptions.py`, `apps/billing/views.py`, `apps/billing/serializers.py`, `apps/billing/urls.py`, `apps/billing/docs.py` (schema), `apps/billing/filters.py`
- Create: `apps/billing/tenant_free_routes.py` se `@no_tenancy` não bastar para `/cobranca/planos/`
- Test: `apps/billing/tests/test_operations.py`, `apps/billing/tests/test_api.py`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces em `Subscriptions` (os métodos deixam `CheckoutError` propagar, e a view traduz com `translate_gateway_errors`):
  - `subscribe(cls, organization_id: int, *, plan: Plan, idempotency_key: str, customer_email: str | None) -> str` — exige `is_free` (senão `409 ALREADY_SUBSCRIBED`) e `plan` pago e ativo (senão `422 PLAN_UNAVAILABLE`). `client.checkouts.create(CheckoutCreate(mode=SUBSCRIPTION, items=(CheckoutItem(price=CatalogPrice(plan.gateway_price_id)),), success_url=settings.FRONTEND_BILLING_CHECKOUT_SUCCESS_URL, cancel_url=settings.FRONTEND_BILLING_CHECKOUT_CANCEL_URL, reference_id=str(organization_id), customer=Customer(email=customer_email) if customer_email else None), idempotency_key=idempotency_key)` (confira os nomes exatos dos tipos em `django_checkouts.types`). Retorna `checkout.url`.
  - `change_plan(cls, organization_id: int, *, plan: Plan, idempotency_key: str) -> Subscription` — sem assinatura paga → `409 NOT_SUBSCRIBED`; mesmo plano → `422 SAME_PLAN`; plano gratuito como alvo → delega a `cancel`. `plan.rank >= atual.rank` → `ReplacePrice(item_id=<item remoto>, price=CatalogPrice(...))`, `timing=IMMEDIATELY`, `proration=INVOICE_IMMEDIATELY` e `pending_plan=None`. `rank` menor → se `MemberLimits.active_count > plan.member_limit` → `409 DOWNGRADE_BLOCKED` com `context={"excess": n}`; senão `timing=NEXT_CYCLE`, `proration=NONE` e `pending_plan=plan`. O `item_id` vem de `client.subscriptions.retrieve(external_id).items[0].external_id`. Depois, `sync`.
  - `cancel(cls, organization_id: int, *, idempotency_key: str) -> Subscription` — barreira de limite contra `Plan.get_free().member_limit` (`409 DOWNGRADE_BLOCKED`); `cancel(timing=PERIOD_END)`; `sync`.
  - `resume(cls, organization_id: int, *, idempotency_key: str) -> Subscription` — `resume`; `sync`.
  - Leituras e validações dentro de `organization_scope`; a chamada ao gateway fora dele.
- Rotas (`apps/billing/urls.py`, sem router):
  - `GET cobranca/planos/` → `PlanListView` (`@no_tenancy`, `ListAPIView`, `Plan.objects.filter(is_active=True).order_by("rank")`, com `filterset_class` explícito);
  - `GET cobranca/assinatura/` → `SubscriptionView` (papel mínimo `ADMINISTRADOR`): `plan`, `pending_plan`, `status`, `has_access`, `effective_plan`, `current_period_end`, `cancel_at`, `is_cancel_scheduled`, `members: {"active": n, "limit": n}`;
  - `POST cobranca/assinatura/checkout/` `{plan: slug}` → `201 {"url": ...}`;
  - `POST cobranca/assinatura/alteracao/` `{plan: slug}` → `200` com a representação da assinatura;
  - `POST cobranca/assinatura/cancelamento/` e `DELETE cobranca/assinatura/cancelamento/` → `200` com a representação.
  - As 4 mutações: papel mínimo `PROPRIETARIO` via `PapelMinimoPermission` (`papel_minimo`); `session_only = True`; `@io_externo_sem_transacao`; header `Idempotency-Key` obrigatório (`422 IDEMPOTENCY_KEY_REQUIRED`); corpo do gateway envolto em `translate_gateway_errors()`. `organization_id` vem de `request.tenant.organization_id`; `customer_email` de `Organizacao.email_faturamento`.

- [ ] **Step 1: Testes que falham.** `test_operations.py`: `test_subscribe_requires_free`, `test_subscribe_sends_reference_id_and_catalog_price`, `test_upgrade_is_immediate_with_invoice_proration` (o `ChangeRemoteSubscription` capturado tem `timing == IMMEDIATELY` e `proration == INVOICE_IMMEDIATELY`), `test_downgrade_is_next_cycle_and_sets_pending_plan`, `test_downgrade_blocked_above_target_limit`, `test_same_rank_counts_as_upgrade`, `test_change_to_free_cancels`, `test_cancel_blocked_above_free_limit`, `test_cancel_at_period_end_then_resume`. `test_api.py`: `test_plans_list_without_organization_header`, `test_subscription_requires_admin`, `test_mutations_require_owner`, `test_mutations_reject_api_keys`, `test_mutation_requires_idempotency_key`, `test_gateway_temporary_error_returns_503`, `test_checkout_returns_url`.
- [ ] **Step 2: Rodar** → FAIL. **Step 3: Implementar.** **Step 4: Rodar** `$PYTEST apps/billing` → PASS.
- [ ] **Step 5: Commit** `feat(billing): expor assinatura, troca de plano e cancelamento`.

### Task 7: Webhook e processamento de eventos

**Files:**
- Create: `apps/billing/events.py`, `apps/billing/tasks.py`
- Modify: `apps/billing/views.py`, `apps/billing/urls.py`
- Test: `apps/billing/tests/test_webhook.py`, `apps/billing/tests/test_tasks.py`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces:
  - `apps.billing.events.record_event(event: WebhookEvent, *, variant: str) -> tuple[BillingEvent, bool]` — extrai `subscription_external_id` do recurso (`Checkout.subscription_id`, `Subscription.external_id`, `Invoice.subscription_id`; qualquer outro, `None`). Insere com `bulk_create(..., ignore_conflicts=True)` e relê pela chave única. O segundo valor indica se a linha foi criada agora.
  - `apps.billing.tasks.process_billing_event` — `@shared_task(name="billing.process_event", bind=True, ignore_result=True, max_retries=5)`, assinatura `(self, event_pk: int)`. Algoritmo da spec ("`process_billing_event`"), mas usando `BillingEvent.subscription_external_id` em vez de reconsultar o recurso. Sem `subscription_external_id` → `processed_at=now`. `retrieve` → sem `reference_id` → `last_error="missing reference_id"`, `attempts += 1`, sem retry. Com `reference_id`: `Subscriptions.link_external(...)` e `Subscriptions.sync(...)`, depois `processed_at=now`. Em `GatewayTemporaryError`: `attempts += 1`, `last_error`, `raise self.retry(countdown=min(2 ** self.request.retries * 30, 900))`. Outros `CheckoutError`/`ReferenceMismatch`: `attempts += 1`, `last_error`, sem retry.
  - `WebhookView` em `POST cobranca/webhooks/<str:variante>/`: `@public`, `authentication_classes = []`, `permission_classes = []`. Variante diferente de `settings.BILLING_GATEWAY_VARIANT` → 404. `get_client().webhooks.verify(request.body, request.headers)`; `WebhookVerificationError` → 400. `record_event`; se criado, `transaction.on_commit(lambda: process_billing_event.delay(event.pk))`; `200`.

- [ ] **Step 1: Testes que falham:** `test_webhook_invalid_signature_returns_400`, `test_webhook_unknown_variant_returns_404`, `test_webhook_records_and_enqueues_once` (mesmo evento 2× → 1 linha e 1 enfileiramento), `test_process_event_without_subscription_marks_processed`, `test_process_checkout_paid_links_and_syncs`, `test_process_subscription_event_before_checkout_links_and_syncs`, `test_process_missing_reference_records_error`, `test_process_temporary_error_retries` (Celery eager: `attempts` sobe e a exceção de retry propaga), `test_process_reference_mismatch_records_error`.
- [ ] **Step 2: Rodar** → FAIL. **Step 3: Implementar.** **Step 4: Rodar** `$PYTEST apps/billing` → PASS.
- [ ] **Step 5: Commit** `feat(billing): receber webhooks e processar eventos de cobrança`.

### Task 8: Reconciliação e encerramento de organização

**Files:**
- Modify: `apps/billing/tasks.py`, `api/settings.py` (`CELERY_BEAT_SCHEDULE`), `apps/organizacoes/organizations.py`
- Test: `apps/billing/tests/test_reconcile.py`, `apps/organizacoes/tests/test_closure.py`

**Interfaces:**
- Consumes: Tasks 4 e 7.
- Produces:
  - `reconcile_billing` — `@shared_task(name="billing.reconcile", ignore_result=True)`: (1) `client.events.list(occurred_since=now - timedelta(hours=settings.BILLING_RECONCILE_WINDOW_HOURS), occurred_before=now, cursor=...)`, paginando por `next_cursor`, `record_event` + enfileirar os criados; (2) reenfileirar `BillingEvent` com `processed_at` nulo, `created_at` com mais de 5 min e `attempts < settings.BILLING_EVENT_MAX_ATTEMPTS`; os que atingiram o máximo geram `logger.error` uma vez por execução, com a contagem; (3) apagar com `delete()` real (não soft delete; use `all_objects` e queryset `.delete()` do Django) os processados com mais de 90 dias.
  - Beat: `"reconcile-billing": {"task": "billing.reconcile", "schedule": crontab(minute="*/15")}`.
  - `cancel_for_closed_organization` — `@shared_task(name="billing.cancel_closed_organization", ignore_result=True)`, assinatura `(organization_id: int)`: assinatura paga → `cancel(timing=IMMEDIATELY, idempotency_key=f"billing:close:{organization_id}")` + `sync`; gratuita → nada.
  - `Organizacoes.solicitar_encerramento`: depois de `_efetivar_bloqueada`, `transaction.on_commit(lambda: cancel_for_closed_organization.delay(atual.pk))`.

- [ ] **Step 1: Testes que falham:** `test_reconcile_records_missing_events_across_pages`, `test_reconcile_requeues_stale_pending_events`, `test_reconcile_skips_events_at_max_attempts_and_logs`, `test_reconcile_purges_processed_older_than_90_days`, `test_closed_organization_cancels_immediately_and_reactivates_nobody` (encerrar organização paga → `CancelRemoteSubscription` com `IMMEDIATELY`; depois do `sync`, nenhum vínculo reativado).
- [ ] **Step 2: Rodar** → FAIL. **Step 3: Implementar.** **Step 4: Rodar** `$PYTEST apps/billing apps/organizacoes` → PASS.
- [ ] **Step 5: Commit** `feat(billing): reconciliar eventos e cancelar ao encerrar organização`.

### Task 9: Admin, documentação e Makefile

**Files:**
- Modify: `apps/billing/admin.py` (`Plan`: `gateway_price_id` read-only depois de salvo; `Subscription` e `BillingEvent` só leitura), `Makefile` (alvo `reset-migrations` com `DATABASE_USER="$(DATABASE_ADMIN_USER)" DATABASE_PASSWORD="$(DATABASE_ADMIN_PASSWORD)"`, seguido de `$(MAKE) db-roles` quando houver `--apply`), `CLAUDE.md` (o `BaseGlobal` do texto chama-se `BaseTenantless` no código, e os modelos de billing estão listados), `docs/explanation/arquitetura.md` (seção "Cobrança"), `docs/reference/api.md` (seção com as rotas de `/cobranca/`), `docs/reference/glossario.md` (verbetes Plano, Assinatura, Plano efetivo, Suspensão por limite), `docs/how-to/operar-cobranca.md` (novo: criar planos no gateway e no admin, configurar webhook, variáveis de ambiente, o que a reconciliação faz), `mkdocs.yml` (nav)
- Test: `apps/billing/tests/test_admin.py`

- [ ] **Step 1: Teste que falha:** `test_plan_gateway_price_id_read_only_after_save`.
- [ ] **Step 2: Rodar** → FAIL. **Step 3: Implementar** admin, Makefile e docs.
- [ ] **Step 4: Rodar** `$PYTEST apps/billing/tests/test_admin.py` → PASS; `make docs` → build sem erro; `make lint` → limpo.
- [ ] **Step 5: Commit** `docs(billing): documentar operação da cobrança`.

### Task 10: Versão da lib e verificação final

Só execute depois que `django-checkouts` 1.1.0 estiver na PyPI. Confira com `curl -s https://pypi.org/pypi/django-checkouts/json | python3 -c "import sys,json;print(json.load(sys.stdin)['info']['version'])"`.

- [ ] **Step 1:** `uv add "django-checkouts[stripe]==1.1.0"`; confirme no `uv.lock` que a origem é `registry = "https://pypi.org/simple"`.
- [ ] **Step 2:** `make test` → nenhuma falha além das pré-existentes listadas em Global Constraints; `make lint`; `make docs`; `DJANGO_ENVIRONMENT=test uv run python manage.py makemigrations --check --dry-run`.
- [ ] **Step 3:** `graphify update .`
- [ ] **Step 4: Commit** `build(deps): exigir django-checkouts 1.1.0 com reference_id na assinatura`.
