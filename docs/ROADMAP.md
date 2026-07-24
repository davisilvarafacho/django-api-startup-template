# Roadmap — Base DRF "de gente grande"

Roadmap de tudo o que foi apresentado para elevar esta base a padrão de produção,
com o que **já foi implementado** e o que está **planejado**, em ondas (batches).

**Legenda:** ✅ feito · 🚧 em andamento · ⏳ planejado · 🧠 brainstorm próprio antes de
codar · 🔎 estudar antes · ⏸️ adiado

_Atualizado em 2026-07-24._

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
| Batch 5 | Autenticação & permissões | 🚧 |
| Batch 6 | Multi-tenancy | ✅ |
| Batch 7 | Observabilidade | ✅ |
| Batch 8 | Domínio & segurança | ⏳ |
| Batch 9 | API avançada | ⏳ |
| Batch 10 | Escala de banco | ⏳ |
| Batch 11 | DevEx & operação (restante) | ⏳ |

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

### Batch 6 — Multi-tenancy
- **Organização → Time → Vínculo → Convite**.
- Isolamento por tenant com **RLS** via `django-rls`.
- Camada HTTP do app (`views.py`/`serializers.py`/`urls.py`):
  - `GET/POST /organizacoes/` para listar/criar organizações do usuário sem exigir `X-Organization`.
  - `GET/POST /times/` filtrado pela organização do header.
  - `GET/PATCH/DELETE /vinculos/` filtrado pela organização do header.
  - `GET/POST/DELETE /convites/` filtrado pela organização do header.
  - `POST /convites/aceitar/` para aceitar convite sem exigir `X-Organization`.

### Batch 7 — Observabilidade
- **Logging estruturado (JSON)** com `python-json-logger` (`api/logging_config.py`).
- **Correlation/request ID próprio** (`apps/api/core/request_id.py`), propagado para Sentry e Celery.
- **Estratégia de logs de request**: nada em banco em produção; log JSON → Promtail → Loki com retenção de 30 dias ([ADR 0002](adr/0002-logs-estruturados-e-retencao.md)).
- **Health check completo**: `/health/` (liveness) e `/health/ready/` (banco, cache, broker, storage), agora fora do bloco de desenvolvimento — o `HEALTHCHECK` do Dockerfile marcava o container como unhealthy em produção.
- **Métricas** com `django-prometheus` em `/metrics`, restrito a rede interna/token.
- **Traces** com **OpenTelemetry** (grupo opcional `observability`), exportando OTLP para o Tempo.
- **Dashboards Grafana** provisionados + stack local (Tempo, Loki, Prometheus, Promtail) alinhada ao compose do projeto.
- **`django-waffle`** (feature flags operacionais) + divisão de papéis com o PostHog ([ADR 0003](adr/0003-feature-flags.md)).
- **PostHog** com as pendências do setup fechadas (vars no `.env.example`, SDK desligado em teste/sem token).

---

## ⏳ Planejado

### Batch 5 — Autenticação & permissões 🚧
- ✅ **`django-guardian` + `django-rules`** (setup extensível, object-level).
- ✅ Papéis estilo **Saleor** (ordem crescente) somados às permissions do Django.
- ✅ Tipagem operacional de `knox.AuthToken` via `TokenMetaData.type` (1=token, 2=reset_password, 999=api_key); reset password não autentica API.
- ✅ **Scoped API tokens**: `TokenMetaData.scopes` + `TokenScopePermission` global via `required_token_scopes` na view.
- ⏳ **Cache de permissão**.
- ⏳ **MFA/2FA** + checagem de senha vazada (HaveIBeenPwned).
- ⏳ **Field-level permissions** (serializers dinâmicos por papel).
- ⏳ Ciclo de vida de conta: verificação de e-mail, social auth, desativação/exclusão, gestão de sessões e dispositivos.

### Batch 8 — Domínio & segurança
- Base de código de **notificações** (providers plugáveis, templates, preferências).
- Lib para **dados sensíveis** (field-level encryption).
- **Validação de upload** genérica e plugável.
- **Money handling** + **metadata framework** (JSON key-value por modelo).
- **Idempotency keys** em POST (evita duplicidade em retry de rede/pagamento).
- **`django-anymail`**: abstração de e-mail multi-provider (hoje a base está acoplada ao Resend).

### Batch 9 — API avançada
- `select_related` / `prefetch_related` sistematizados no `BaseViewSet`.
- Sparse fieldsets / field expansion (`?fields=`, `?expand=`).
- **ETags** / conditional requests.
- **Cursor pagination**.
- Serializer registry (Sentry) / dataloaders (Saleor).

### Batch 10 — Escala de banco
- **Read replica + DB router** para escala de leitura.
- **Constraints no banco** (`UniqueConstraint`, `CheckConstraint`) e triggers com **`django-pgtrigger`**.
- Data migrations separadas de schema migrations.

### Batch 11 — DevEx & operação (restante)
- **Fixtures / seeds / demo data** via management command.
- **Devcontainer** para onboarding.
- **Runbooks** operacionais.
- Política de **deprecação de API** (changelog de API + header `Sunset`).

---

## 🧠 Brainstorms próprios (antes de implementar)
- **Webhooks de saída** — framework open source próprio.
- **URLs assinadas** para arquivos privados (auth base que gera e valida token).

## 🔎 A estudar
- **Soft delete real**: existe o campo `ativo`, mas o `destroy` apaga fisicamente.
- **`django-constance`** (configuração em runtime, sem redeploy).
- Headers de **SSL/HSTS/secure** + `manage.py check --deploy`.
- **CSP** (`django-csp`).
- **oso** / **casbin** (camada de policy).
- Regras de **migration zero-downtime** (Saleor/Sentry) — entender se é limitação de banco.
- `responses` / `vcrpy`, **snapshot tests**, **load/contract testing**.

## ⏸️ Adiado
- **LGPD** (bloco próprio): PII, retenção/expurgo, exportação, direito ao esquecimento, consentimento, scrub de PII.
- Arquitetura de **plugins / integrações**.

## Brainstorm do dev

- Permissões/rules via plano
- Checkout por seat e plano, default stripe - classe de abstração backend plugavel na frente - lib para conectar 5 ou mais providers de cara - stripe, assas, etc
