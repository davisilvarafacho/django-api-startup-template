# Changelog

Todas as mudanças relevantes deste projeto são registradas neste arquivo.

O formato segue [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e o
projeto adota [Versionamento Semântico](https://semver.org/lang/pt-BR/).

## [Unreleased]

### Added

- Ciclo completo de contas e organizações, com identidade Google, verificação e
  troca de e-mail, MFA, desativação, reativação e exclusão agendada.
- Cobrança enxuta no app `apps.billing`, sobre a interface publicada
  `django-checkouts[stripe]==1.1.0`, com plano pago de preço fixo e limite de
  membros por plano. O plano gratuito é criado no `migrate`, e a assinatura
  gratuita, no onboarding e no `seed_demo`.
- Assinatura da organização com checkout, troca de plano (upgrade imediato com
  cobrança proporcional; downgrade no próximo ciclo, bloqueado acima do limite do
  plano de destino), cancelamento no fim do período e retomada, todos com
  `Idempotency-Key`. Vínculos excedentes ao limite do plano são suspensos.
- Webhook público, autenticado pela assinatura do gateway, com caixa de eventos e
  processamento assíncrono; reconciliação periódica a cada 15 minutos e
  cancelamento imediato quando a organização é encerrada. Rotas em `/cobranca/` e
  guia em `docs/how-to/operar-cobranca.md`.
- Decorator e política única para depreciação gradual de handlers da API.
- Portal de documentação MkDocs estruturado por Diátaxis.
- Validação de Conventional Commits em hooks e CI.

### Security

- A assinatura de cobrança (`Subscription`) herda de `Base` e fica isolada por
  organização pelo RLS; planos e eventos do gateway não são isolados por
  organização (`BaseTenantless`).
- Webhooks persistem somente fatos normalizados em allowlist; corpos, headers,
  credenciais, PII e o payload diagnóstico `raw` do gateway não são gravados.

## [0.1.0] - 2026-07-21

### Added

- Base Django REST Framework com PostgreSQL, Redis, Celery, cache, throttling,
  documentação OpenAPI e automações de qualidade.
