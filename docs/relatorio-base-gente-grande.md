# Relatório — Tornando a base DRF uma aplicação "de gente grande"

Levantamento do que foi sugerido para elevar esta base a padrão de produção
(inspirado em codebases maduros como Sentry, Saleor e Pretix), com o status de
cada item.

**Legenda:** ✅ implementado · ⏳ aprovado (a implementar) · 🔎 estudar antes ·
⏸️ adiado · 🧠 merece brainstorm próprio · ⚠️ pendência operacional

_Última atualização: 2026-07-29._

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
| Throttling do DRF (anon 100/h, user 1000/h, login 10/min, reautenticação 5/min) | ✅ |
| Endpoint global de lookup (autocomplete de FK sem exigir permissão do modelo-alvo) | ✅ |
| Modelo próprio e swappable `autenticacao.AuthToken` com tipos de sessão, reset de senha e API key | ✅ |
| Scoped API tokens no padrão `resource:action`, traduzidos para permissions Django | ✅ |
| Delegação de scopes limitada às permissions do ator | ✅ |
| `django-guardian` + `django-rules` (setup extensível, object-level) | ✅ |
| Papéis estilo Saleor (ordem crescente) somados às permissions do Django | ✅ |
| API keys independentes por organização, com responsável, rotação, suspensão e revogação | ✅ |
| Gestão de sessões, logout seletivo/global e autenticação recente via decorator | ✅ |
| Envelope e registry centralizados de erros, com códigos `TextChoices` por app | ✅ |
| Cache de permissão | ⏳ |
| MFA/2FA + checagem de senha vazada (HaveIBeenPwned) | ⏳ |
| Field-level permissions nos serializers | ⏳ |
| Ciclo de vida de conta (verificação de e-mail, social auth, desativação/exclusão) | ⏳ |
| `oso` / `casbin` como camada de policy | 🔎 |

## 4. Multi-tenancy

| Item | Status |
|------|--------|
| Organização → Time → Vínculo → Papel, com convites | ✅ |
| Isolamento por tenant com **RLS** via `django-rls` | ✅ |

## 5. Observabilidade

| Item | Status |
|------|--------|
| Sentry: sample rates ajustados (0.1) e scrub do token de autenticação | ✅ |
| Logging estruturado (JSON) com `python-json-logger` | ✅ |
| Correlation/request ID próprio, propagado para Sentry e Celery | ✅ |
| PostHog (product analytics / eventos) | ✅ |
| `django-waffle` (feature flags operacionais) | ✅ |
| Métricas + dashboards (Grafana / Prometheus / OpenTelemetry) | ✅ |
| Health checks de liveness/readiness | ✅ |
| Estratégia de logs de request: JSON → Alloy → Loki, sem banco em produção | ✅ |

## 6. Segurança

| Item | Status |
|------|--------|
| `ALLOWED_HOSTS` / `CSRF_TRUSTED_ORIGINS` via variável de ambiente | ✅ |
| `.env.example` versionado + `.env` fora do versionamento | ✅ |
| Headers de SSL/HSTS/secure + `check --deploy` | 🔎 |
| CSP (`django-csp`) | 🔎 |
| Lib para dados sensíveis (field-level encryption, keyring e rotação) | ✅ |
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
| Pré-commit hooks | ✅ |
| GitHub Actions (commits, lint, versões, docs e testes) | ✅ |
| Gate de migrations | ⚠️ suspenso até o reset integral pré-lançamento |
| Serviços Postgres/Redis no CI (fecha o caveat dos testes) | ✅ |
| Segurança de dependências/imagem: `pip-audit`, `bandit`, `trivy`, Dependabot | ✅ |
| `pytest-cov` + helpers locais de teste + Codecov + coverage gate | ✅ |
| Fixtures / seeds / demo data | ⏳ |
| Makefile | ✅ |
| `responses` / `vcrpy` (mock de HTTP externo) · snapshot tests · load/contract testing | 🔎 |

## 9. Documentação & release

| Item | Status |
|------|--------|
| Versionamento **SemVer** casado com `SPECTACULAR_SETTINGS["VERSION"]` | ✅ |
| **Conventional Commits** padronizados e validados | ✅ |
| Changelog no formato **Keep a Changelog** | ✅ |
| Arquivos padrão do GitHub (CONTRIBUTING, SECURITY, CODE_OF_CONDUCT, templates) | ✅ |
| Docs com **MkDocs + Material** organizadas por Diátaxis | ✅ |
| **ADRs (MADR)** em `docs/adr/` | ✅ |
| Convenções de código explícitas em `.ai/CONVENTIONS.md` | ✅ |

## 10. Compliance & extensibilidade

| Item | Status |
|------|--------|
| LGPD (bloco próprio): PII, retenção/expurgo, exportação, direito ao esquecimento, consentimento | ⏸️ |
| Arquitetura de plugins / integrações | ⏸️ |
| Regras de migration zero-downtime (Saleor/Sentry) — entender se é limitação de banco | 🔎 |

---

## Resumo do que já implementamos (✅)

1. **Settings e infraestrutura**: configuração por ambiente, PostgreSQL,
   Celery, Redis, cache compartilhado, throttling e stack Docker local.
2. **Base de API**: invalidação versionada de cache, lookup global, paginação,
   auditoria comum e convenções de modelos.
3. **DevEx e CI**: pre-commit, Ruff, GitHub Actions, segurança automatizada,
   cobertura, factories, Makefile e templates do GitHub.
4. **Documentação e release**: SemVer, Conventional Commits, changelog, MkDocs,
   Diátaxis, ADRs e convenções explícitas.
5. **Multi-tenancy**: organização, time, vínculo, convite, APIs tenant-aware e
   isolamento por RLS.
6. **Observabilidade**: logs JSON, request ID, Sentry, PostHog, feature flags,
   health checks, métricas, traces e dashboards.
7. **Autenticação e autorização — núcleo do Batch 5**: token swappable,
   sessões, step-up auth, API keys por organização, scopes, delegação,
   permissions por papel e erros estruturados.
8. **Dados sensíveis**: field-level encryption com keyring, integração DRF,
   exclusão automática de auditoria e rotação em lote.

## Próximas entregas prioritárias

1. Fechar o **Batch 5**: cache de permissions, MFA/2FA + HaveIBeenPwned,
   field-level permissions e ciclo de vida de conta.
2. Fazer o **reset integral das migrations** antes do lançamento e restaurar o
   gate `makemigrations --check --dry-run`.
3. Continuar o **Batch 8** com notificações, uploads, money/metadata,
   idempotency keys e abstração de e-mail.
