---
title: "No billing, só encerramento solicitado cancela a assinatura; organização apenas desativada continua cobrada"
author: Davisilvarafacho
date: 2026-11-22
branch: startup
cost: { minutes: 122, tokens: 540160 }
id: l-7c0b6617
scope: repo
status: active
src: agent
touches:
  - README.md
  - docs/CHANGELOG.md
  - docs/explanation/autenticacao.md
  - docs/how-to/desenvolvimento-local.md
  - docs/how-to/operar-cobranca.md
  - docs/reference/estrutura-de-diretorios.md
  - docs/tutorial/primeiro-ambiente.md
  - ".ai/brainstorming/spec/2026-11-21-billing-enxuto-design.md"
  - apps/billing/tasks.py
  - apps/billing/tests/test_reconcile.py
---

## Decided
- "Organização encerrada" para o billing significa exclusivamente `encerramento_solicitado_em` preenchido. `is_active=False` ou `is_deleted=True` isolados NÃO contam: o admin deixa desativar uma organização paga, e tratar isso como encerramento cancelava a assinatura no gateway de forma imediata e irreversível (sem período final, sem proration) em até 15 minutos.
- A varredura de encerradas na reconciliação faz um loop por organização, não uma query única: a policy RLS (`organizacao_id = current_setting('rls.tenant_id')`) impede enxergar vários tenants numa consulta, e o contexto privilegiado só relaxa a guarda de troca de identidade. Uma query única exigiria BYPASSRLS/SECURITY DEFINER, que a spec exclui. Custo aceito: uma transação por organização encerrada a cada 15 minutos.
- O cancelamento ao encerrar roda só pela task Celery disparada no `on_commit`, nunca dentro da transação de encerramento: o `sync` trava Subscription → Organizacao → Vínculos, e `solicitar_encerramento` já segura o lock da Organizacao; chamar inline inverteria a ordem e arriscaria deadlock.
- Assinatura duplicada (segundo checkout concluído para a mesma organização) é cancelada na hora com a chave `billing:duplicate:<external_id>`; reembolso fica manual. O evento duplicado era marcado como processado, então a reconciliação nunca o detectaria.

## Tried and ruled out
- Aceitar a corrida do `subscribe` e deixar a reconciliação como ponto de detecção: não detecta, pelo motivo acima.
- Não fazer varredura de organizações encerradas: um checkout concluído depois do encerramento (ou um evento em voo) cobrava uma organização encerrada todo mês, e um `.delay` perdido no `on_commit` nunca era refeito.

## Watch out
- `django-checkouts` 1.0.1 só mapeia `reference_id` para `client_reference_id` do checkout; sem a 1.1.0 a assinatura remota volta com `reference_id=None`, o `sync` levanta `ReferenceMismatch` e quem pagou nunca ganha acesso. Nada falha no boot, e as fixtures de teste preenchem `reference_id` na mão, então só o teste-guarda pega isso.
- `organization_scope` e `ensure_free` limpam o contexto RLS ao sair: quem chama não pode contar com o tenant continuando ativo depois (por isso `ensure_free` é o último passo do onboarding e do seed).
- Rodar dois pytest ao mesmo tempo usa o mesmo banco de teste (`base_test`) e colide; e subagentes Haiku travaram esperando suíte em background: rode testes sempre em primeiro plano.

In the code: `apps/billing/tasks.py` (`_is_closed`, `reconcile_billing`, `process_billing_event`), `apps/billing/subscriptions.py` (`link_external`, `cancel_immediately`, `sync`), `apps/organizacoes/organizations.py` (`solicitar_encerramento`), `apps/billing/tests/test_checkouts_version.py`, `docs/how-to/operar-cobranca.md`.
