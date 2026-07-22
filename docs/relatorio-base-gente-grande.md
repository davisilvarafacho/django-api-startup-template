# Relatório — Tornando a base DRF uma aplicação "de gente grande"

Levantamento do que foi sugerido para elevar esta base a padrão de produção
(inspirado em codebases maduros como Sentry, Saleor e Pretix), com o status de
cada item.

**Legenda:** ✅ implementado · ⏳ aprovado (a implementar) · 🔎 estudar antes ·
⏸️ adiado · 🧠 merece brainstorm próprio

_Última atualização: 2026-07-21._

---

## 1. Infraestrutura & escala

| Item | Status |
|------|--------|
| `configure_enviroment` como fonte única de apps/middlewares/storages por ambiente | ✅ |
| PostgreSQL como banco padrão + `CONN_MAX_AGE` + `CONN_HEALTH_CHECKS` | ✅ |
| Fila de tarefas assíncronas — **Celery + Redis** + `django-celery-beat` (agendador via banco) | ✅ |
| `docker-compose` local (Postgres, Redis, web, worker, beat) | ✅ |
| Read replica + DB router | ⏳ |

## 2. Cache & performance

| Item | Status |
|------|--------|
| Cache backend **Redis** (`django-redis`), compartilhado entre workers | ✅ |
| Correção do cachalot: cache dedicado no Redis (invalidação entre workers) | ✅ |
| Postura de cache: cachalot opt-in + primitivos explícitos + HTTP/TTL como espinha dorsal | ✅ |
| Endpoint `invalidate_cache` no `BaseModelViewSet` (invalidação O(1) por versão de namespace) | ✅ |
| `select_related`/`prefetch_related` sistematizados no `BaseViewSet` | ⏳ |
| Sparse fieldsets / field expansion (`?fields=`, `?expand=`) | ⏳ |
| ETags / conditional requests · cursor pagination | ⏳ |
| Serializer registry (estilo Sentry) · dataloaders (estilo Saleor) | 🔎 |

## 3. Autenticação & autorização

| Item | Status |
|------|--------|
| Throttling do DRF (anon 100/h, user 1000/h, escopo `auth`) | ✅ |
| Endpoint global de lookup (autocomplete de FK sem exigir permissão do modelo-alvo) | ✅ |
| App de integrações: proxy model de `knox.AuthToken` com `type` (1=token, 2=reset_password, 999=api_key) | ⏳ |
| Scoped API tokens | ⏳ |
| `django-guardian` + `django-rules` (setup extensível, object-level) | ⏳ |
| Papéis estilo Saleor (ordem crescente) somados às permissions do Django | ⏳ |
| Cache de permissão | ⏳ |
| MFA/2FA + checagem de senha vazada (HaveIBeenPwned) | ⏳ |
| `oso` / `casbin` como camada de policy | 🔎 |

## 4. Multi-tenancy

| Item | Status |
|------|--------|
| Organization → Team → Membership → Role, com convites | ⏳ |
| Isolamento por tenant com **RLS** via `django-rls` | ⏳ |

## 5. Observabilidade

| Item | Status |
|------|--------|
| Sentry: sample rates ajustados (0.1) e scrub do token de autenticação | ✅ |
| Logging estruturado (JSON) com `python-json-logger` | ⏳ |
| Correlation/request ID (decidir `django-request-id` vs próprio) | ⏳ |
| PostHog (product analytics / eventos) | ⏳ |
| `django-waffle` (feature flags) | ⏳ |
| Métricas + dashboards (Grafana / Prometheus / OpenTelemetry) | ⏳ |
| Estratégia de armazenamento de logs de request (evitar banco de 50GB+) | ⏳ |

## 6. Segurança

| Item | Status |
|------|--------|
| `ALLOWED_HOSTS` / `CSRF_TRUSTED_ORIGINS` via variável de ambiente | ✅ |
| `.env.example` versionado + `.env` fora do versionamento | ✅ |
| Headers de SSL/HSTS/secure + `check --deploy` | 🔎 |
| CSP (`django-csp`) | 🔎 |
| Lib para dados sensíveis (field-level encryption) | ⏳ |
| Validação de upload genérica e plugável | ⏳ |
| Expiração/assinatura de URLs para arquivos privados | 🧠 |

## 7. Notificações & domínio

| Item | Status |
|------|--------|
| Base de código de notificações (providers plugáveis, templates, preferências) | ⏳ |
| Framework de webhooks de saída (open source, próprio) | 🧠 |
| Money handling (`py-moneyed` / `django-prices`) | ⏳ |
| Metadata framework (JSON key-value por modelo, estilo Saleor) | ⏳ |
| Idempotency keys em POST | ⏳ |

## 8. Qualidade, testes & DevEx/CI

| Item | Status |
|------|--------|
| Convenção de docstring (`ruff pydocstyle: google`) | ✅ |
| `get_env_var` com default opcional + helper `get_list_from_env` | ✅ |
| Pré-commit hooks | ⏳ |
| GitHub Actions (lint, testes, `check --deploy`, `makemigrations --check`) | ⏳ |
| Serviços Postgres/Redis no CI (fecha o caveat dos testes) | ⏳ |
| Segurança de dependências/imagem: `pip-audit`, `bandit`, `trivy`, Dependabot | ⏳ |
| `pytest-cov` + `factory_boy` + Codecov + coverage gate | ⏳ |
| Fixtures / seeds / demo data | ⏳ |
| Makefile / justfile | ⏳ |
| `responses` / `vcrpy` (mock de HTTP externo) · snapshot tests · load/contract testing | 🔎 |

## 9. Documentação & release

| Item | Status |
|------|--------|
| Versionamento **SemVer** casado com `SPECTACULAR_SETTINGS["VERSION"]` | ⏳ |
| **Conventional Commits** padronizados | ⏳ |
| Changelog no formato **Keep a Changelog** | ⏳ |
| Arquivos padrão do GitHub (CONTRIBUTING, SECURITY, CODE_OF_CONDUCT, templates) | ⏳ |
| Docs com **MkDocs + Material** organizadas por Diátaxis | ⏳ |
| **ADRs (MADR)** em `docs/adr/` | ⏳ |
| Convenções de código explícitas (ex.: `r = f(); g(r)` em vez de `g(f())`) documentadas | ⏳ |

## 10. Compliance & extensibilidade

| Item | Status |
|------|--------|
| LGPD (bloco próprio): PII, retenção/expurgo, exportação, direito ao esquecimento, consentimento | ⏸️ |
| Arquitetura de plugins / integrações | ⏸️ |
| Regras de migration zero-downtime (Saleor/Sentry) — entender se é limitação de banco | 🔎 |

---

## Resumo do que já implementamos (✅)

1. **Configuração por ambiente** centralizada no `configure_enviroment`.
2. **PostgreSQL** como banco padrão, com health checks e conexões persistentes.
3. **Sentry** endurecido (sample rates + scrub do token).
4. **Hosts/CSRF via env** + `.env.example`.
5. **Celery + Redis + django-celery-beat** (fila de tarefas + agendador).
6. **Cache Redis** (`django-redis`) e correção da invalidação do cachalot.
7. **Throttling** do DRF.
8. **`docker-compose`** com a stack local completa.
9. **Cache versionado por viewset** + endpoint `invalidate_cache`.
10. **Endpoint global de lookup** (autocomplete de FK).
11. Correção de bug: import do `debug_toolbar` só em desenvolvimento.
12. Convenção de docstring (`pydocstyle: google`) e melhorias no helper de env.

> Próximas ondas sugeridas: **Batch 3 (DevEx/CI)** → **Batch 4 (Docs & convenções)**
> → **Batch 5 (Auth & permissões)** → **Batch 6 (Multi-tenancy)** → demais.
