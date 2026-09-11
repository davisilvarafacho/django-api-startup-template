# Changelog

Todas as mudanças relevantes deste projeto são registradas neste arquivo.

O formato segue [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e o
projeto adota [Versionamento Semântico](https://semver.org/lang/pt-BR/).

## [Unreleased]

### Added

- Ciclo completo de contas e organizações, com identidade Google, verificação e
  troca de e-mail, MFA, desativação, reativação e exclusão agendada.
- Catálogo versionado de planos, contratos gratuitos/trial/enterprise,
  propostas, seats, carências e política tenant de acesso comercial.
- Faturamento recorrente Stripe pela interface publicada
  `django-checkouts[stripe]==1.0.1`, com checkouts, setup de forma de pagamento,
  faturas, webhooks autenticados, retries, recuperação e reconciliação.
- Rollout idempotente de organizações existentes por `sync_plans` e
  `initialize_subscriptions`, protegido por system check de deploy.
- Decorator e política única para depreciação gradual de handlers da API.
- Portal de documentação MkDocs estruturado por Diátaxis.
- Validação de Conventional Commits em hooks e CI.

### Security

- Dados financeiros usam `FORCE ROW LEVEL SECURITY`; o ingresso anterior ao
  tenant opera por roles PostgreSQL sem bypass e funções `SECURITY DEFINER` de
  privilégio mínimo.
- Webhooks persistem somente fatos normalizados em allowlist; corpos, headers,
  credenciais, PII e o payload diagnóstico `raw` do gateway não são gravados.

## [0.1.0] - 2026-07-21

### Added

- Base Django REST Framework com PostgreSQL, Redis, Celery, cache, throttling,
  documentação OpenAPI e automações de qualidade.
