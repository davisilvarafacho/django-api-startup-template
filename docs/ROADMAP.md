# Roadmap — Base DRF "de gente grande"

Roadmap de tudo o que foi apresentado para elevar esta base a padrão de produção,
com o que **já foi implementado** e o que está **planejado**, em ondas (batches).

**Legenda:** ✅ feito · ⏳ planejado · 🧠 brainstorm próprio antes de codar ·
🔎 estudar antes · ⏸️ adiado

_Atualizado em 2026-07-22._

---

## Progresso

| Onda | Tema | Status |
|------|------|--------|
| Batch 1 | Hardening de settings e ambiente | ✅ |
| Batch 2 | Fila de tarefas, cache e throttling | ✅ |
| — | Endpoint de invalidação de cache | ✅ |
| — | Endpoint global de lookup | ✅ |
| Batch 3 | DevEx / CI | ✅ |
| Batch 4 | Documentação & convenções | ✅ |
| Batch 5 | Autenticação & permissões | ⏳ |
| Batch 6 | Multi-tenancy | ⏳ |
| Batch 7 | Observabilidade | ⏳ |
| Batch 8 | Domínio & segurança | ⏳ |
| Batch 9 | API avançada | ⏳ |

---

## ✅ Concluído

### Batch 1 — Hardening de settings e ambiente
- `configure_enviroment` como **fonte única** de apps/middlewares/storages por ambiente.
- **PostgreSQL** como banco padrão + `CONN_MAX_AGE` + `CONN_HEALTH_CHECKS`.
- **Sentry**: sample rates em `0.1` e scrub do token de autenticação.
- `ALLOWED_HOSTS` / `CSRF_TRUSTED_ORIGINS` via env + **`.env.example`**.
- Convenção de docstring `ruff pydocstyle: google`.
- Helpers de env: `get_list_from_env` e `get_env_var` com default opcional.

### Batch 2 — Fila de tarefas, cache e throttling
- **Celery + Redis** + `django-celery-beat` (agendador via banco); tasks eager em teste.
- **Cache Redis** (`django-redis`); correção do **cachalot** (cache compartilhado → invalidação entre workers).
- **Throttling** do DRF (anon 100/h, user 1000/h, escopo `auth`).
- **`docker-compose`** (Postgres, Redis, web, worker, beat).
- Postura de cache definida (**opção A**): cachalot opt-in + primitivos explícitos + HTTP/TTL como espinha dorsal.

### Endpoint de invalidação de cache
- `build_cache_key` / `get_cache_version` / `bump_cache_version` no `BaseModelViewSet`.
- Action `POST .../invalidate_cache/` (invalidação O(1) por versão de namespace).

### Endpoint global de lookup (autocomplete de FK)
- Decorator `@lookup` + registry central (chave = nome do modelo).
- `GET /lookup/<chave>/` com `IsAuthenticated` (não exige permissão do modelo-alvo).
- Saída via serializer DRF (default `{id, label}` ou serializer próprio).
- N+1 tratado (`select_related`/`prefetch_related` + `setup_eager_loading`).

### Batch 3 — DevEx / CI
- **pre-commit** (ruff + hooks básicos).
- **GitHub Actions** por cadência: `ci.yml` (lint + `makemigrations --check` + pytest com Postgres/Redis) e `security.yml` (pip-audit + bandit + trivy) + `dependabot.yml`.
- **Codecov** + cobertura (`pytest-cov`), gate de patch 80% em código novo.
- **Makefile** e arquivos padrão do GitHub (CONTRIBUTING, SECURITY, CODE_OF_CONDUCT, templates).
- **factory_boy** (`UsuarioFactory`) + testes DB-less (lookup, env).
- Extras: app `configuracoes` removido; dívida de lint zerada; fix do import de `debug_toolbar` (quebrava test/prod).

### Batch 4 — Documentação & convenções
- Versionamento **SemVer** alinhado com `SPECTACULAR_SETTINGS["VERSION"]`.
- **Conventional Commits** validados por commitlint no hook e na CI.
- `CHANGELOG.md` no formato **Keep a Changelog**.
- Portal **MkDocs + Material** organizado por **Diátaxis**.
- **ADRs (MADR)** em `docs/adr/`.
- Convenções de código explícitas em `.ai/CONVENTIONS.md`.

---

## ⏳ Planejado

### Batch 5 — Autenticação & permissões
- App de integrações: proxy model de `knox.AuthToken` com campo `type` (1=token, 2=reset_password, 999=api_key).
- **`django-guardian` + `django-rules`** (setup extensível, object-level).
- **Cache de permissão**.
- Papéis estilo **Saleor** (ordem crescente) somados às permissions do Django.
- **MFA/2FA** + checagem de senha vazada (HaveIBeenPwned).

### Batch 6 — Multi-tenancy
- **Organization → Team → Membership → Role**, com convites.
- Isolamento por tenant com **RLS** via `django-rls`.

### Batch 7 — Observabilidade
- **Logging estruturado (JSON)** + correlation/request ID.
- **PostHog** (analytics/eventos) + **`django-waffle`** (feature flags).
- Métricas & dashboards (**Grafana / Prometheus / OpenTelemetry**).
- Estratégia de armazenamento de logs de request (evitar banco gigante).

### Batch 8 — Domínio & segurança
- Base de código de **notificações** (providers plugáveis, templates, preferências).
- Lib para **dados sensíveis** (field-level encryption).
- **Validação de upload** genérica e plugável.
- **Money handling** + **metadata framework** (JSON key-value por modelo).

### Batch 9 — API avançada
- `select_related` / `prefetch_related` sistematizados no `BaseViewSet`.
- Sparse fieldsets / field expansion (`?fields=`, `?expand=`).
- **ETags** / conditional requests.
- **Cursor pagination**.
- Serializer registry (Sentry) / dataloaders (Saleor).

---

## 🧠 Brainstorms próprios (antes de implementar)
- **Webhooks de saída** — framework open source próprio.
- **URLs assinadas** para arquivos privados (auth base que gera e valida token).

## 🔎 A estudar
- Headers de **SSL/HSTS/secure** + `manage.py check --deploy`.
- **CSP** (`django-csp`).
- **oso** / **casbin** (camada de policy).
- Regras de **migration zero-downtime** (Saleor/Sentry) — entender se é limitação de banco.
- `responses` / `vcrpy`, **snapshot tests**, **load/contract testing**.

## ⏸️ Adiado
- **LGPD** (bloco próprio): PII, retenção/expurgo, exportação, direito ao esquecimento, consentimento, scrub de PII.
- Arquitetura de **plugins / integrações**.
