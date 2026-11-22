# Billing enxuto sobre o django-checkouts — design

Data: 2026-11-21 · Branch: `refactor/billing-enxuto` (base `startup`)

## Intenção

O template precisa de billing de fábrica, mas a implementação anterior
(`apps/assinaturas` + `faturamento`, ~9 mil linhas de produção, 55 funções/triggers
SQL, processo e logins de banco dedicados) custava mais do que entregava. Ela foi
removida em `97d0166`. Este design a substitui por um app pequeno que cobre o
essencial — **assinar, cancelar, upgrade e downgrade** — com **um banco só**,
**tolerância a falhas** (worker de reconciliação) e **robustez**.

A ideia central: **o gateway é a fonte da verdade.** O app guarda um espelho do
estado da assinatura e decide só duas coisas: se a organização tem acesso ao plano
pago e se cabe mais um membro.

## Objetivos

- Organização nasce no **plano gratuito permanente**; pagar é um upgrade.
- Assinar plano pago por checkout hospedado.
- Upgrade imediato, com **fatura proporcional** na hora (crédito do tempo não usado
  do plano antigo − proporcional do novo: o cliente nunca paga duas vezes o mesmo
  período). Downgrade no próximo ciclo.
- Cancelar no fim do período e desfazer o cancelamento antes dele.
- **Preço fixo por plano** com **limite de membros** por plano (teto, não cobrança).
- Quando o acesso pago cai, a organização volta ao gratuito e os **membros mais
  recentes acima do limite são suspensos**; quando o acesso volta, são reativados.
- Webhook idempotente + reconciliação periódica: um evento perdido é recuperado
  em até um ciclo do worker (15 min), **desde que** esteja dentro da janela de
  reconciliação (`BILLING_RECONCILE_WINDOW_HOURS`, 72h) e abaixo de
  `BILLING_EVENT_MAX_ATTEMPTS` tentativas. Fora disso, a divergência pede
  sincronização manual (`Subscriptions.sync`).

## Não-objetivos

- Cobrança por usuário (seats), trial, cupons, propostas comerciais, versionamento
  de catálogo, listagem de faturas, troca de forma de pagamento.
- Mais de um gateway ativo ao mesmo tempo (o código é agnóstico via
  `django-checkouts`, mas o template configura uma variante).
- Pix/boleto recorrente: o gateway Stripe da lib só aceita cartão em assinatura.

## Anti-objetivos (seria fracasso mesmo funcionando)

- Máquina de estados própria de assinatura, snapshots, revisões otimistas.
- Guardar payload bruto do gateway.
- Logins/roles de banco, funções `SECURITY DEFINER`, processos ou filas dedicados.
- Ler dado de tenant fora do RLS.
- `organizacoes` dependendo de billing além de uma checagem de capacidade.

## Abordagens consideradas

1. **Evento como gatilho, estado buscado na fonte (escolhida).** Webhook só grava o
   evento e enfileira; a task chama `subscriptions.retrieve()` e sobrescreve o
   espelho. Uma única função `sync` serve webhook, reconciliação e operações.
   Ordem e duplicidade de eventos deixam de importar. Custo: chamadas à API por
   evento.
2. **Aplicar o payload do evento.** Economiza chamadas, mas exige comparar
   `occurred_at` para não regredir estado — volta a complexidade.
3. **Só polling.** Simples, mas o cliente paga e espera minutos pelo acesso.

O usuário escolheu a 1.

Para achar a organização de um evento, também foram consideradas: models de
billing globais (`BaseTenantless`) — rejeitado, dado de tenant fora do RLS; bypass
privilegiado — `organizacao_atual_privilegiada` só **troca** de tenant, não lê
atravessando tenants, então exigiria `SECURITY DEFINER`/`BYPASSRLS` (o que inflou a
versão antiga). **Escolhido:** a própria assinatura no gateway carrega o id da
organização (ver "Dependência na django-checkouts").

## Dependência na django-checkouts

Na 1.0.1, a assinatura criada pelo checkout Stripe não carrega o `reference_id`
(fica só no `client_reference_id` da sessão). A lib passa a copiar o
`reference_id`, **quando informado** (continua opcional), para
`subscription_data.metadata.reference_id` em checkouts de modo assinatura. Assim
`Subscription.reference_id` da lib devolve o id da organização em qualquer
consulta. Mudança na branch `feat/reference-id-na-assinatura` do repositório da
lib; o template exige a versão publicada que a contiver (o lockfile aponta para a
PyPI, nunca para path ou VCS).

## Idioma

Identificadores em inglês; docstrings, comentários e documentação em português.
**Rotas em português** (padrão do projeto).

## Arquitetura

App novo: `apps/billing` (em `BUSINESS_APPS`). Depende de
`django-checkouts[stripe]` na versão com a mudança acima (`uv add`).

```
apps/billing/
  models.py         choices no topo; Plan, Subscription, BillingEvent
  subscriptions.py  class Subscriptions: subscribe, change_plan, cancel, resume,
                    sync, ensure_member_capacity, apply_member_limit
  gateway.py        get_client() -> CheckoutClient da variante configurada
  tasks.py          process_billing_event, reconcile_billing,
                    cancel_for_closed_organization
  handlers.py       post_migrate: garante o plano gratuito
  serializers.py / views.py / urls.py / filters.py / schema.py / admin.py
  tests/
```

### Topologia

```
                 Plan  (BaseTenantless · catálogo)
                  ▲  ▲
          plan N:1│  │N:1 pending_plan (downgrade agendado)
                  │  │
Organizacao ─1:1─ Subscription  (Base · tenant, sob RLS)
     │ 1:N
  Vinculo ─N:1─ Usuario
  (+ plan_limit_suspended_at)

BillingEvent  (BaseTenantless · caixa de entrada, sem FK)
```

- `Plan` é catálogo: não pertence a organização → `BaseTenantless`.
- `Subscription` é dado do tenant → `Base`, sob RLS. Toda leitura/escrita fora de
  request usa `organizacao_atual(org_id)`, com o id vindo do gateway.
- `BillingEvent` existe antes de se saber o tenant e só guarda ids e metadados do
  evento (nenhum dado da organização) → `BaseTenantless`.

## Modelo de dados

### Choices (topo do `models.py`)

```python
class SubscriptionStatus(models.TextChoices):
    """Mesmos valores de django_checkouts.enums.SubscriptionStatus."""
    INCOMPLETE = "incomplete", _("Incompleta")
    TRIALING = "trialing", _("Em teste")
    ACTIVE = "active", _("Ativa")
    PAST_DUE = "past_due", _("Em atraso")
    PAUSED = "paused", _("Pausada")
    UNPAID = "unpaid", _("Não paga")
    CANCELED = "canceled", _("Cancelada")
    EXPIRED = "expired", _("Expirada")

class BillingCycle(models.TextChoices): ...  # mesmos valores da lib
```

`TextChoices` com os **mesmos valores da lib**: a conversão é
`SubscriptionStatus(remote.status.value)`, sem tabela de de-para (com
`IntegerChoices` seria preciso um mapa manual). Exceção à preferência por
`IntegerChoices`, comentada no código. Alias na classe:
`Subscription.Status = SubscriptionStatus`, `Plan.Cycle = BillingCycle`.

### `Plan` (`billing_plan`, `BaseTenantless`)
- `name`, `slug` (único), `description`
- `rank` `PositiveSmallIntegerField` — ordem para decidir upgrade × downgrade
- `price_amount` `PositiveIntegerField` nulo (centavos, só exibição), `currency`
- `billing_cycle` (`BillingCycle`) nulo para o gratuito
- `gateway_price_id` único, nulo para o gratuito; **imutável depois de salvo**
  (admin read-only): preço novo = plano novo, para o espelho sempre mapear
  `price_id → Plan`
- `member_limit` `PositiveSmallIntegerField`
- `is_free` com `UniqueConstraint(condition=Q(is_free=True))` — exatamente um
- `is_active` (herdado): plano inativo some do catálogo, mas continua mapeável

### `Subscription` (`billing_subscription`, `Base`)
- `organizacao` (herdado de `Base`) com `UniqueConstraint(fields=["organizacao"])`
  — uma por organização; é o único índice além dos herdados
- `plan` FK `Plan`; `pending_plan` FK `Plan` nulo (downgrade agendado, limpo quando
  o espelho mostrar o preço novo)
- `status` (`SubscriptionStatus`)
- `gateway_variant`, `external_id`, `customer_id` (nulos no gratuito)
- `current_period_end`, `cancel_at` (nulos)
- `synced_at`

Sem índice composto em `status`/`current_period_end`: nenhum processo consulta
esses campos em massa (toda leitura é pela organização).

Propriedades (a definição dos status vive aqui):

```python
ACCESS_STATUSES = frozenset({Status.ACTIVE, Status.TRIALING, Status.PAST_DUE})
TERMINAL_STATUSES = frozenset({Status.CANCELED, Status.EXPIRED})
ABANDONED_STATUSES = frozenset({Status.UNPAID})  # encerrar no gateway

@property
def is_free(self) -> bool: ...           # plan.is_free
@property
def has_access(self) -> bool: ...        # status in ACCESS_STATUSES
@property
def is_cancel_scheduled(self) -> bool:   # cancel_at is not None and has_access
@property
def effective_plan(self) -> Plan: ...    # plan se has_access, senão o gratuito
@property
def member_limit(self) -> int: ...       # effective_plan.member_limit
```

Nenhum campo "tem acesso" é gravado: o acesso é sempre derivado do status.

### `Vinculo` (`organizacoes`) — coluna nova
- `plan_limit_suspended_at` `DateTimeField` nulo: preenchido quando **o billing**
  desativa o vínculo por limite do plano. Distingue essas desativações das feitas
  à mão por um admin, para reativar só as do billing.

### `BillingEvent` (`billing_event`, `BaseTenantless`) — caixa de entrada
- `gateway_variant`, `event_id` — `UniqueConstraint` em ambos (idempotência)
- `event_type`, `resource_kind`, `resource_id`, `occurred_at`
- `processed_at` nulo, `attempts`, `last_error`
- Índice parcial em `occurred_at` onde `processed_at IS NULL` (consultado a cada
  ciclo da reconciliação numa tabela que só cresce)
- Sem payload bruto, headers ou dados pessoais.

## Fluxos

### Onboarding

`OrganizationOnboarding.criar` cria a `Subscription` gratuita (`plan` = gratuito,
`status=active`, sem `external_id`). `seed_demo` idem. O plano gratuito é garantido
por um handler `post_migrate` idempotente (sobrevive a `reset_migrations`, que
descarta `RunPython`), com `member_limit` de `BILLING_FREE_PLAN_MEMBER_LIMIT`
(default 3).

### `sync(organization)` — a única fonte de escrita do espelho

Roda sob o contexto RLS da organização (request ou `organizacao_atual`).

1. Transação, `select_for_update` na `Subscription` da organização.
2. Se há `external_id`: `client.subscriptions.retrieve(external_id)` **dentro do
   lock** (serializa syncs da mesma organização e impede que uma leitura antiga
   sobrescreva uma nova).
3. Confere `remote.reference_id == str(organization.id)`; divergência → erro, nada
   é gravado.
4. Mapeia `items[0].price_id → Plan` (desconhecido → mantém o plano atual e loga
   erro). Atualiza `status`, `plan`, `current_period_end`, `cancel_at`,
   `customer_id`, `synced_at`. Limpa `pending_plan` se `plan == pending_plan`.
5. `unpaid` (o gateway desistiu de cobrar): `cancel(timing=IMMEDIATELY)` no gateway
   e segue como terminal — nunca sobra assinatura remota "pendurada" impedindo um
   checkout novo. `incomplete` e `paused` ficam sem acesso, mas não são terminais.
6. Terminal (`canceled`/`expired`): volta a linha ao gratuito (`plan`=gratuito,
   `status=active`, `external_id`/`cancel_at`/`pending_plan` nulos).
7. `apply_member_limit(organization)` ao final (idempotente).

### Limite de membros

`apply_member_limit`: com o lock da organização (o mesmo
`_bloquear_organizacao_aberta` que `Vinculos` usa), conta vínculos ativos.
- Acima do limite: suspende os **mais recentes por `created_at`**, nunca o
  proprietário (`Papel.PROPRIETARIO`): `is_active=False` e
  `plan_limit_suspended_at=agora`.
- Abaixo do limite com suspensos pelo billing: reativa os **suspensos há mais
  tempo primeiro** até o limite, limpando `plan_limit_suspended_at`.

Vínculo inativo já é barrado no pipeline (`vinculo.is_active=False` é rejeitado),
então nada mais muda na autenticação.

`ensure_member_capacity(organization, *, count_pending_invites=False)` levanta
`409 billing.member_limit_reached` se não couber mais um. Chamado em
`organizacoes.memberships.Vinculos`:
- `criar_convite` — conta ativos + convites pendentes;
- `aceitar_convite` — conta ativos;
- `atualizar_vinculo` quando `is_active` passa de `False` para `True`; limpa
  `plan_limit_suspended_at` (o admin reativou à mão).

Remover um membro **não** reativa suspensos automaticamente: a vaga fica livre e o
proprietário reativa quem quiser. A reativação automática acontece só no `sync`.

É a única dependência de `organizacoes` para `billing`.

### Idempotência e resultado incerto nas operações

Toda rota que chama o gateway exige o header `Idempotency-Key`, repassado como
`idempotency_key` à lib. Sem ele → `422`. Erro com `retry_advice`
`RETRY_SAME_KEY`/`RECONCILE_FIRST` → `503 billing.gateway_uncertain` (o cliente
repete com a **mesma** chave; a reconciliação corrige o espelho). Erro permanente →
`502 billing.gateway_error`. As rotas são marcadas `@io_externo_sem_transacao`
(chamada externa fora do `atomic`).

### Assinar (gratuito → pago)

Proprietário. Exige que a organização **não tenha assinatura vinculada ao gateway**
(`external_id` vazio) e esteja no plano gratuito; vínculo existente → `409
billing.already_subscribed`, mesmo que o espelho mostre o gratuito (preço
desconhecido ou plano excluído). Cria
`CheckoutCreate(mode=SUBSCRIPTION, items=[preço de catálogo],
reference_id=str(org.id), customer=e-mail de faturamento, success_url/cancel_url)`.
Devolve a URL. O `reference_id` chega à assinatura pela mudança na lib.

### Trocar de plano (pago → pago)

Proprietário, corpo `{plan}`.
- `rank` maior ou igual (ex.: mesmo nível, mensal → anual) → **upgrade**:
  `change(ReplacePrice, timing=IMMEDIATELY, proration=INVOICE_IMMEDIATELY)` — fatura
  proporcional na hora; descarta um `pending_plan` existente.
- `rank` menor → **downgrade**: ativos > `member_limit` do alvo → `409
  billing.downgrade_blocked` informando quantos precisam sair. Senão
  `change(ReplacePrice, timing=NEXT_CYCLE, proration=NONE)` e grava `pending_plan`.
- Alvo gratuito → é cancelamento (abaixo), com a mesma barreira de limite.

Depois da chamada: `sync(organization)`.

### Cancelar / desfazer

`cancel(timing=PERIOD_END)` / `resume()`, depois `sync`. Cancelar é barrado com
membros acima do limite do gratuito (cancelar = descer para o gratuito).

### Encerramento de organização

`Organizacoes.solicitar_encerramento` agenda via `transaction.on_commit`
`cancel_for_closed_organization(org_id)` → `cancel(timing=IMMEDIATELY)` (chave
`billing:close:<id>`) + `sync`. Falha temporária do gateway é repetida com backoff.

Cobrança nunca continua para organização encerrada, por três caminhos:

- **Evento tardio** (checkout concluído depois do encerramento): `process_billing_event`
  detecta a organização encerrada, cancela já a assinatura remota que dá acesso com a
  mesma chave `billing:close:<id>` e **não** espelha plano pago nela.
- **Varredura** na reconciliação: organizações encerradas com assinatura vinculada
  (`external_id` preenchido) têm `cancel_for_closed_organization` reenfileirado. Cobre
  um `.delay` perdido no `on_commit` e uma recusa permanente do gateway. A consulta
  de assinatura roda sob o contexto de cada organização encerrada, porque o RLS não
  permite uma consulta única entre organizações.
- **Duplicada:** ver abaixo.

### Assinatura duplicada

`link_external` só preenche um `external_id` vazio. Se a organização já está ligada a
outra assinatura e chega uma segunda (ex.: dois checkouts concluídos), a recém-chegada
é cancelada **na hora** no gateway (`timing=IMMEDIATELY`, chave
`billing:duplicate:<external_id>`), com log de erro, e a original fica intacta. O
cancelamento acontece fora do lock e da transação; o evento só é marcado como
processado se ele der certo. O **reembolso da duplicada é manual**.

### Preço desconhecido

Quando o preço da assinatura remota não existe no catálogo (ou o plano foi excluído),
`_apply_state` mantém o plano atual e o espelho pode ler `active` no plano gratuito com
`external_id` preenchido. Nesse estado: assinar é recusado (vínculo existente), cancelar
e desfazer chegam ao gateway, e trocar para plano pago é recusado com `409
billing.current_plan_unknown`, porque o rank do gratuito não diz se é upgrade ou
downgrade.

### Webhook

`POST /cobranca/webhooks/<variante>/`, `@public`, sem `X-Organization`.
`client.webhooks.verify(raw_body, headers)`; falha → `400`. Grava `BillingEvent`
com `ON CONFLICT DO NOTHING`; `on_commit` enfileira `process_billing_event(id)`;
responde `200`. Nenhuma outra lógica na request.

### `process_billing_event(id)` (Celery)

1. Extrai o id da assinatura no gateway: `Checkout.subscription_id`,
   `Subscription.external_id` ou `Invoice.subscription_id`. Evento sem assinatura
   (ex.: checkout expirado) → `processed_at=agora`, nada mais.
2. `client.subscriptions.retrieve(...)` → `reference_id` = id da organização.
   Sem `reference_id` → `last_error`, fica pendente (não é deste template).
3. `with organizacao_atual(org_id)`: se a `Subscription` local ainda não tem
   `external_id` (primeiro checkout), grava `external_id`/`gateway_variant`;
   depois `sync(organization)`.
4. `processed_at=agora`.

Erros: `GatewayTemporaryError` → `self.retry` com backoff exponencial respeitando
`retry_advice` (máx. 5); demais → `last_error`, fica pendente. Após
`BILLING_EVENT_MAX_ATTEMPTS` (default 10) a reconciliação para de reenfileirar e
loga em nível `error` (Sentry).

### `reconcile_billing` (beat, a cada 15 min)

1. **Eventos perdidos:** `events.list` na janela
   `[agora − BILLING_RECONCILE_WINDOW_HOURS (72h), agora]`, paginando; insere os que
   faltam (`ON CONFLICT DO NOTHING`) e enfileira. Sem tabela de checkpoint: a janela
   fixa sobreposta basta na escala de um template.
2. **Pendentes:** reenfileira `BillingEvent` não processados há mais de 5 min com
   `attempts` abaixo do máximo.
3. **Organizações encerradas:** reenfileira o cancelamento das encerradas que ainda têm
   assinatura vinculada (ver "Encerramento de organização"). Uma falha neste passo é
   logada e não aborta os demais.
4. **Limpeza:** apaga `BillingEvent` processados há mais de 90 dias.

### Erros do gateway e `ReferenceMismatch`

Uma assinatura remota cujo `reference_id` aponta para outra organização nunca é gravada.
Numa rota (alteração, cancelamento, desfazer) a operação já chegou ao gateway, então o
desencontro vira `502 billing.gateway_error` com log de erro, nunca um `500` não tratado.
Na tarefa, vira `last_error` e o evento fica pendente.

## Rotas

| Rota | Quem | O quê |
|---|---|---|
| `GET /cobranca/planos/` | sessão humana apenas (API key → `403`), sem tenant (`@no_tenancy`) | catálogo ativo |
| `GET /cobranca/assinatura/` | proprietário/admin | espelho + `has_access`, `effective_plan`, `pending_plan`, uso de membros |
| `POST /cobranca/assinatura/checkout/` | proprietário | assinar |
| `POST /cobranca/assinatura/alteracao/` | proprietário | upgrade/downgrade |
| `POST, DELETE /cobranca/assinatura/cancelamento/` | proprietário | cancelar / desfazer |
| `POST /cobranca/webhooks/<variante>/` | público | entrada do gateway |

Sessão humana apenas (API keys sem escopo de billing). Erros no envelope padrão,
códigos `billing.*`.

## Configuração

`CHECKOUT_VARIANTS = {"stripe": ("django_checkouts.gateways.stripe.StripeGateway",
{...})}` com `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` (env via `utils/env.py`,
nomes seguindo `convencoes.md`). `BILLING_GATEWAY_VARIANT` (default `stripe`),
`FRONTEND_BILLING_CHECKOUT_SUCCESS_URL`, `FRONTEND_BILLING_CHECKOUT_CANCEL_URL`,
`BILLING_FREE_PLAN_MEMBER_LIMIT`, `BILLING_RECONCILE_WINDOW_HOURS`,
`BILLING_EVENT_MAX_ATTEMPTS`. Os checks do próprio `django-checkouts` validam
credenciais no `check --deploy`.

## Testes

`django_checkouts.testing.FakeCheckoutGateway` como variante em teste. Cobrir:
- propriedades de `Subscription` (DB-less);
- `sync`: mapeamento de preço, `reference_id` divergente, `unpaid` encerrado,
  terminal → gratuito, `pending_plan`, preço desconhecido;
- `apply_member_limit`: suspende mais recentes, nunca proprietário, reativa na
  ordem, não reativa desativação manual;
- capacidade em convite, aceite e reativação;
- webhook: assinatura inválida, duplicado, enfileiramento;
- task: retry em erro temporário, evento sem assinatura, sem `reference_id`,
  primeiro checkout gravando `external_id`;
- reconciliação: evento faltante, pendente, limpeza;
- operações: upgrade imediato com proporcional, downgrade agendado e barrado,
  cancelar/desfazer, encerramento da organização.

## Itens incidentais

- Alvo `make reset-migrations` passa a usar a credencial admin (hoje usa o login
  web, sem DDL) e roda `db-roles` depois.
- Documentação: how-to "Operar billing", seção em `arquitetura.md`, rotas em
  `reference/api.md`, verbetes em `glossario.md`.

## Decisões tomadas sem consulta (revisar)

1. Status e ciclo como `TextChoices` com os valores da lib, não `IntegerChoices`.
2. Um preço por plano: anual é outro plano, com o mesmo `rank`; mesmo `rank` conta
   como upgrade (imediato, proporcional).
3. `gateway_price_id` imutável; preço novo = plano novo.
4. Cancelar também é barrado com membros acima do limite do gratuito.
5. `unpaid` é encerrado no gateway e tratado como terminal.
6. Proprietário nunca é suspenso; remover membro não reativa suspensos.
7. Convite pendente conta na capacidade ao convidar, não ao aceitar.
8. Reconciliação com janela fixa de 72 h, sem checkpoint; eventos processados
   apagados após 90 dias.
9. Plano gratuito garantido por `post_migrate`, limite default 3.
10. Mutação exige papel proprietário e sessão humana, sem reautenticação recente.
11. `sync` confere o `reference_id` remoto contra a organização antes de gravar.

## O que fica com o construtor

Nomes internos, formato exato dos serializers e do schema OpenAPI, estrutura de
testes, mensagens de erro — seguindo `convencoes.md` e a skill `django-api`.
