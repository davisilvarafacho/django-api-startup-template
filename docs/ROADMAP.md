# Roadmap — Base DRF "de gente grande"

Roadmap de tudo o que foi apresentado para elevar esta base a padrão de produção,
com o que **já foi implementado** e o que está **planejado**, em ondas (batches).

**Legenda:** ✅ feito · 🚧 em andamento · ⏳ planejado · 🧠 brainstorm próprio antes de
codar · 🔎 estudar antes · ⏸️ adiado

_Atualizado em 2026-08-09._

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
| Batch 8 | Domínio & segurança | 🚧 |
| Batch 9 | API avançada | ⏳ |
| Batch 10 | Escala de banco | ⏳ |
| Batch 11 | DevEx & operação (restante) | 🚧 |

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
- **Throttling** do DRF (anon 100/h, user 1000/h) + limites próprios de
  autenticação (`auth_login` 10/min e `auth_reauthenticate` 5/min).
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

### Soft delete real
- `Base.delete()` marca `is_deleted=True` em vez de apagar; `perform_destroy` do
  `BaseModelViewSet` passa a ser soft delete por padrão.
- Managers: `objects` (não deletados), `all_objects` (inclui deletados) e `ativos`
  (`is_active=True`).

### Batch 3 — DevEx / CI
- **pre-commit** (ruff + hooks básicos).
- **GitHub Actions** por cadência: `ci.yml` (lint + pytest com Postgres/Redis) e `security.yml` (pip-audit + bandit + trivy) + `dependabot.yml`; `makemigrations --check` está suspenso até o reset integral pré-lançamento.
- **Codecov** + cobertura (`pytest-cov`), gate de patch 80% em código novo.
- **Makefile** e arquivos padrão do GitHub (CONTRIBUTING, SECURITY, CODE_OF_CONDUCT, templates).
- Helper determinístico `criar_usuario` restrito a `tests/support/` + testes DB-less (lookup, env).
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
- **Estratégia de logs de request**: nada em banco em produção; log JSON → Alloy → Loki com retenção de 30 dias ([ADR 0002](adr/0002-logs-estruturados-e-retencao.md)).
- **Health check completo**: `/health/` (liveness) e `/health/ready/` (banco, cache, broker, storage), agora fora do bloco de desenvolvimento — o `HEALTHCHECK` do Dockerfile marcava o container como unhealthy em produção.
- **Métricas** com `django-prometheus` em `/metrics`, restrito a rede interna/token.
- **Traces** com **OpenTelemetry** (grupo opcional `observability`), exportando OTLP para o Tempo.
- **Dashboards Grafana** provisionados + stack local (Tempo, Loki, Prometheus, Alloy) alinhada ao compose do projeto.
- **`django-waffle`** (feature flags operacionais) + divisão de papéis com o PostHog ([ADR 0003](adr/0003-feature-flags.md)).
- **PostHog** com as pendências do setup fechadas (vars no `.env.example`, SDK desligado em teste/sem token).

---

## 🚧 Em andamento

### Batch 5 — Autenticação & permissões 🚧
- ✅ **`django-guardian` + `django-rules`** (setup extensível, object-level).
- ✅ Papéis estilo **Saleor** (ordem crescente) somados às permissions do Django.
- ✅ Modelo de token **swappable** (`settings.KNOX_TOKEN_MODEL = "autenticacao.AuthToken"`), não mais proxy: `AuthToken.type` (1=token, 2=reset_password, 999=api_key); reset password não autentica API.
- ✅ **Scoped API tokens**: `AuthToken.scopes` + `TokenScopePermission` global; scopes falam `resource:action` (`apps.api.core.scope_registry`), traduzido para `app_label.codename` do Django.
- ✅ **Delegação de scope**: uma API key nunca recebe mais poder do que o ator que
  a cria ou altera possui (`validate_scope_delegation`); actions customizadas
  também são mapeadas para permissions Django.
- ✅ **Envelope de erros unificado** (`{"errors": [...], "request_id": ...}`) para DRF, middlewares e handlers de status HTTP do Django.
- ✅ **Gestão de sessões** (`/auth/sessions/`, `/auth/logout*`) e **autenticação recente/step-up** (`@require_recent_auth`, `/auth/reauthenticate/`).
- ✅ **API keys por organização** (`/auth/api_keys/`): CRUD, rotação atômica, suspensão manual/automática (fail-closed quando o responsável perde o vínculo) e retomada; UUID como identificador público; plain token exibido só na criação/rotação.
- ✅ **MFA/2FA** (TOTP, e-mail, SMS, recovery codes, token `PRE_AUTH` e trusted
  devices).
- ✅ **Política de senha centralizada** (`apps/usuarios/passwords.py`) com
  checagem de **senha vazada** (HaveIBeenPwned, k-anonymous e fail-open),
  redefinição deslogada sem enumeração de contas e alteração autenticada com
  step-up. Ambos revogam sessões, tokens efêmeros e dispositivos confiáveis,
  preservando API keys.
- ✅ **Cache de permissions** com invalidação ao alterar papéis, vínculos, grupos
  ou permissões (ver `docs/adr/0005-cache-semantico-de-autorizacao.md`).
- ⏳ Para fechar o batch:
  - **Field-level permissions** com serializers dinâmicos por papel.
    - Evolução futura: estender o `makemigrations` para detectar
      `RenameField` em campos autorizáveis e acrescentar automaticamente uma
      operação reversível que preserve as políticas de escrita e suas
      concessões. A primeira versão exigirá uma operação explícita na
      migration e permanecerá fail-closed quando houver políticas órfãs.
  - **Ciclo de vida de conta**: verificação de e-mail, social auth,
    desativação e exclusão.
- ⚠️ Pendência operacional: migrations permanecem congeladas nesta fase; CI e
  testes usam `--nomigrations`. O histórico será recriado integralmente antes
  do lançamento e o gate `makemigrations --check --dry-run` será restaurado.

### Batch 8 — Domínio & segurança 🚧
- ⏳ Base de código de **notificações** (providers plugáveis, templates, preferências).
- ✅ Lib para **dados sensíveis** (field-level encryption): wrapper `encrypt(...)`, keyring/rotação Fernet, write-only no DRF e exclusão automática do auditlog.
- ⏸️ **Validação de upload** genérica e plugável (adiado).
- ⏸️ **Money handling** (adiado; não será adotado por enquanto).
- ✅ **Metadata framework** (JSON key-value por objeto): action `GET`/`PATCH`
  `.../metadata/` ligada por padrão no `BaseModelViewSet` (opt-out por
  `metadata_habilitado`), merge de chaves com `null` removendo, valores texto,
  limites por settings e documento isolado por organização.
- ⏳ **Idempotency keys** em POST (evita duplicidade em retry de rede/pagamento).
- ✅ **`django-anymail`**: abstração de e-mail multi-provider; envio padrão pelo Resend, sem acoplamento ao SDK do provider.

## ⏳ Planejado

### Batch 9 — API avançada
- `select_related` / `prefetch_related` sistematizados no `BaseViewSet`.
- ⏸️ Sparse fieldsets / field expansion (`?fields=`, `?expand=`) (adiado).
- **ETags** / conditional requests.
- **Cursor pagination**.
- ⏸️ Serializer registry (Sentry) / dataloaders (Saleor) (adiado).

### Batch 10 — Escala de banco
- **Read replica + DB router** para escala de leitura.
- **Constraints no banco** (`UniqueConstraint`, `CheckConstraint`).
- ⏸️ Triggers com **`django-pgtrigger`** (adiado; não será adotado por enquanto).
- Data migrations separadas de schema migrations.

### Batch 11 — DevEx & operação (restante) 🚧
- ✅ **Fixtures / seeds / demo data** via management command (`manage.py seed_demo`,
  idempotente e bloqueado em produção).
- ✅ **Devcontainer** para onboarding (`.devcontainer/`).
- ✅ **Ambiente local completo atrás do Nginx** (`docker/nginx/`, `make stack` e
  `make nginx-test`; ver `docs/how-to/proxy-nginx.md`).
- ⏳ Política de **deprecação de API** (changelog de API + header `Sunset`).
- ⏸️ **Runbooks** operacionais (adiado).

---

## 🧠 Brainstorms próprios (antes de implementar)
- **Webhooks de saída** — framework open source próprio.
- **URLs assinadas** para arquivos privados (auth base que gera e valida token).

## 🔎 A estudar
- **`django-constance`** (configuração em runtime, sem redeploy).
- Headers de **SSL/HSTS/secure**: o alvo `make check` (`manage.py check --deploy`) já
  existe, mas o settings só define `SECURE_PROXY_SSL_HEADER` — faltam
  `SECURE_SSL_REDIRECT`, `SECURE_HSTS_*`, `SESSION_COOKIE_SECURE` e `CSRF_COOKIE_SECURE`.
- **CSP** (`django-csp`).
- **oso** / **casbin** (camada de policy).
- Regras de **migration zero-downtime** (Saleor/Sentry) — entender se é limitação de banco.
- `responses` / `vcrpy`, **snapshot tests**, **load/contract testing**.

## ⏸️ Adiado
- **LGPD** (bloco próprio): PII, retenção/expurgo, exportação, direito ao esquecimento, consentimento, scrub de PII.
- Arquitetura de **plugins / integrações**.
- **Validação de upload** genérica e plugável.
- **Money handling**.
- Sparse fieldsets / field expansion (`?fields=`, `?expand=`).
- Serializer registry (Sentry) / dataloaders (Saleor).
- Triggers com **`django-pgtrigger`**.
- **Runbooks operacionais** (item do Batch 11).

## Brainstorm do dev

- Permissões/rules via plano
- Checkout por seat e plano, default stripe - classe de abstração backend plugavel na frente - lib para conectar 5 ou mais providers de cara - stripe, assas, etc
- Após o modelo de checkout/planos estar pronto, cache de entitlements para decidir se o plano do usuário libera cada endpoint/feature.
- .memory/
