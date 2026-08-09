# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Base reutilizável para APIs Django REST Framework, multi-tenant por padrão. Código,
comentários e documentação são em **português**; codinames de permissão e nomes de
migração seguem o padrão do Django (inglês).

## Comandos

Dependências e execução são sempre via `uv` (nunca `pip`/`python` direto). O
`Makefile` é o atalho canônico — `make help` lista tudo.

```bash
make install      # uv sync (inclui grupo dev)
make hooks        # pre-commit install + npm install (hooks pre-commit e commit-msg)
make up           # sobe Postgres + Redis (docker compose up -d db redis)
make stack        # sobe a stack completa, API atrás do nginx
make nginx-test   # valida a config do nginx dos dois ambientes
make migrate      # uv run python manage.py migrate
make run          # runserver
make worker       # celery -A api worker
make beat         # celery -A api beat (DatabaseScheduler)
make test         # pytest com cobertura
make lint         # ruff check .
make format       # ruff format .
make docs         # mkdocs build --strict
make check        # manage.py check --deploy
make version-check# versão do pacote == versão do schema OpenAPI
```

A suíte **exige Postgres e Redis rodando** (`make up`); o alvo `test` do Makefile
exporta os defaults do `docker-compose.yml` (`base`/`postgres`/`postgres`/`127.0.0.1`/`5432`),
mas variáveis `DATABASE_*` já presentes no ambiente prevalecem.

Um teste isolado (o grupo `test` precisa ser explícito fora do Makefile):

```bash
uv run --group test pytest apps/api/core/tests/test_lookup.py::test_nome -q
uv run --group test pytest apps/organizacoes -k rls
```

Dependências: `uv add <pacote>` / `uv remove <pacote>`; `pyproject.toml` e `uv.lock`
entram no mesmo commit.

Antes de abrir PR (é o que a CI roda): `make lint`, `make test`, `make docs` e
`uv run python manage.py makemigrations --check --dry-run`. Commits seguem
Conventional Commits (validado por commitizen + commitlint no `commit-msg`).

## Arquitetura

### Configuração por ambiente

`api/configure_enviroment.py` é a **fonte única** de apps, middlewares e storages por
ambiente (`development` / `production` / `test`). `api/settings.py` resolve
`CONFIG_ENVIRONMENT` (teste é detectado por `pytest in sys.modules`) e só concatena o
resultado. Nunca adicione um app/middleware condicional direto no settings — declare-o
na função do ambiente correspondente. `api/urls.py` usa o mesmo `CONFIG_ENVIRONMENT`
para registrar as URLs de dev (debug_toolbar, hijack).

Leitura de ambiente **sempre** por `utils/env.py` (`get_env_var`, `get_bool_from_env`,
`get_list_from_env`) — nunca `os.environ` cru.

### Pipeline da request

A ordem em `MIDDLEWARE` é significativa e está comentada no settings:

1. `RequestIDMiddleware` — correlation id, logo após o SecurityMiddleware, para que
   todo log a partir dali o carregue.
2. `ThreadLocalMiddleware` — precede a autenticação (`get_current_user()` depende dele).
3. `apps.api.autenticacao.middleware.AuthenticationMiddleware` — **a autenticação real
   acontece aqui**, não no DRF. O DRF só reaproveita o resultado via
   `PassthroughAuthentication`. Motivo: auditlog, PostHog e tenancy rodam como
   middleware e veriam `AnonymousUser` se a autenticação ficasse no `dispatch()`.
4. `OrganizacaoMiddleware` — o mais interno possível: lê o header `X-Organization` e
   **abre a transação** que envolve a request (necessária para o `SET LOCAL` do RLS).
   O contexto de tenant em si é aplicado depois, na permission.

### Proxy reverso

Em qualquer ambiente conteinerizado a API fica **atrás de um nginx** e o
gunicorn/runserver não publica porta — quem atende o host é o proxy, sempre em
`http://localhost:8000`. A configuração vive em `docker/nginx/`: `nginx.conf`
(bloco http comum) + `snippets/` (headers) + `sites/<ambiente>/`, montado sobre
`/etc/nginx/conf.d`. Os `X-Forwarded-*` são sobrescritos pelo proxy e o Django
confia neles via `DJANGO_BEHIND_PROXY` (`SECURE_PROXY_SSL_HEADER`,
`USE_X_FORWARDED_HOST`/`_PORT`).

`/ws/` já sai configurado para WebSocket (upgrade via `map $http_upgrade`,
timeout longo, sem buffering), mas o upstream é WSGI: concluir o handshake
exige um worker ASGI (`GUNICORN_WORKER_CLASS`). Ver `docs/how-to/proxy-nginx.md`.

### Multi-tenancy e RLS

- `Base` (em `apps/api/base/models.py`) = campos comuns **+ isolamento por organização**.
  Todo modelo de negócio herda dela; o FK `organizacao` e a `TenantPolicy` do
  `django-rls` são propagados às subclasses. Multi-tenancy é o default: esquecer de
  configurar algo resulta em ficar protegido, não em vazar dados.
- `BaseGlobal` = mesmos campos **sem** isolamento. Reservado aos modelos de
  identidade/bootstrap lidos antes de existir tenant: `Organizacao`, `Vinculo`,
  `Convite`, `Time`, `Usuario`.
- `apps.organizacoes.permissions.TenantPermission` está em
  `DEFAULT_PERMISSION_CLASSES`: **toda** rota exige `X-Organization` válido por padrão.
  Ela resolve o `Vinculo`, popula `request.organizacao`/`request.vinculo` e chama
  `definir_organizacao_atual()`.
- O contexto usa sempre `SET LOCAL` (`apps/organizacoes/context.py`), seguro sob pool
  em transaction mode. **Fora do ciclo de request** (tasks Celery, management commands,
  testes) envolva o código em `organizacao_atual(id)` ou, quando precisar atravessar
  organizações, `organizacao_atual_privilegiada(id)` — sem isso as queries levantam
  `RLSContextRequiredError` (`DJANGO_RLS["REQUIRE_CONTEXT"] = True`).
- **Não troque o `ENGINE` do banco.** `django_rls.backends.postgresql` é obrigatório;
  com o engine padrão as policies são silenciosamente ignoradas. Pelo mesmo motivo os
  backends `django_prometheus.db.*` não podem ser usados (métricas de banco vêm do
  OpenTelemetry).

### Exceções de rota: registries + marcadores

Padrão recorrente do projeto — comportamento global por padrão, exceções declaradas
pelo app e coletadas no boot (`RouteRegistry`, em `apps/api/core/routes_registry.py`,
descoberto no `ready()` de `apps.api.core`):

- `urls.py` → `PUBLIC_ROUTES`: rotas **sem token**.
- `tenant_free_routes.py` → `TENANT_FREE_ROUTES`: com token, **sem organização**.
- Decorators `@public` / `@no_tenancy` (`apps/api/core/route_markers.py`) para views
  deste projeto. A checagem é sempre `decorator OU prefixo`; prefixos existem para o
  que não dá para decorar (`/admin/`, estáticos, views de terceiros).

Cuidados: a comparação de prefixo é `startswith` (declare granular — `/auth/` liberaria
`logout/`), e o marcador é atributo de classe, então **subclasses herdam** (não decore
uma view base).

### Camada base da API (`apps/api/base/`)

- `BaseModelViewSet` entrega de graça as actions `grid`, `form`, `values`,
  `bulk_create`, `bulk_update`, `clonar`, `invalidate_cache` e `ativar`/`inativar`.
  Use `serializer_class` **ou** `serializer_classes = {"<action>": ...}`, nunca os dois.
  `get_queryset()` chama `modify_base_queryset()` e, se existir, `modify_<action>_queryset()`.
- Escrita → `BaseModelSerializer` (DRF); leitura → `BaseModelSerpySerializer` (serpy).
  O `BaseModelSerializer` aplica automaticamente `internal_fields` /`read_only_fields`
  do model (`extra_internal_fields`, `extra_read_only_fields`, `extra_write_only_fields`).
- Soft delete é o comportamento padrão: `delete()` marca `is_deleted=True`. Managers:
  `objects` (não deletados, com `defer` dos `queryset_deferred_fields`), `all_objects`
  (inclui deletados) e `ativos` (`is_active=True`).
- Permissões de action vêm do `perms_map` de `CustomDjangoModelPermissions`
  (`apps/api/autenticacao/permissions.py`); actions fora dos verbos do Django usam o
  prefixo `can_` (ex.: `can_toggle_<model>`) e devem ser declaradas em `Meta.permissions`.

### Cache

Namespace versionado por modelo (`viewcache:<app>.<model>`). Monte chaves com
`build_cache_key(...)`; a invalidação em massa é **O(1)** via `bump_cache_version()` /
action `invalidate_cache`, sem depender de `delete_pattern`. Redis db 1 = `default`,
db 3 = `cachalot` (precisa ser cache **compartilhado**, senão a invalidação não alcança
os outros workers do gunicorn). `cachalot` é opt-in e só carrega em produção.

### Lookup

`@lookup(...)` (`apps/api/core/lookup.py`) registra um modelo no endpoint global
`GET /lookup/<chave>/`, que exige apenas autenticação — resolve o caso de FK cujo
modelo-alvo o usuário não tem permissão de ver. O registry é a fronteira de segurança:
modelo não registrado responde 404. Trate N+1 no próprio decorator (`select_related`)
ou via `setup_eager_loading(queryset)` estático no serializer (a view aplica sozinha).

### Autorização em camadas

`rules` (predicados por objeto, ex.: papel na organização) → `ModelBackend`
(permissions/groups) → `guardian` (permissões por objeto no banco). No DRF a ordem em
`DEFAULT_PERMISSION_CLASSES` é `IsAuthenticated` → `TenantPermission` (aplica o RLS) →
`TokenScopePermission` (escopos, só para tokens `API_KEY`) → `CustomDjangoModelPermissions`.

Para proteção contra força bruta, `AxesStandaloneBackend` é o primeiro item de
`AUTHENTICATION_BACKENDS`: ele apenas verifica se a combinação usuário + IP está
bloqueada e delega a autenticação real aos backends seguintes. A resposta de
bloqueio é `resposta_de_bloqueio`, em
`apps/api/autenticacao/handlers.py`. Consulte o
[how-to de proteção contra força bruta](docs/how-to/protecao-forca-bruta.md).

### Feature flags

`waffle` (`apps/api/core/feature_flags.py`) para kill-switch e rollout interno — estado
no banco, avaliado localmente, falha fechado (`WAFFLE_*_DEFAULT = False`,
`CREATE_MISSING_* = False`). PostHog para rollout de produto por segmento. Ver
`docs/adr/0003-feature-flags.md`.

### Observabilidade

Logs estruturados em JSON só em produção (`api/logging_config.py`); Sentry só em
produção, com scrub do header `Authorization`. `/health/`, `/health/ready/` e `/metrics`
ficam fora do bloco de desenvolvimento (o HEALTHCHECK do Dockerfile depende disso).
OpenTelemetry é opt-in (`OTEL_ENABLED`, grupo de dependências `observability`,
inicializado no `ready()` do core).

## Convenções obrigatórias

### Imports entre módulos

Importe cada objeto diretamente do módulo que o declara; não crie `shared.py`
somente para reexportar símbolos. Imports entre apps e tipos de módulo são
livres. Quando houver um ciclo concreto, adie apenas uma aresta com import local
no menor escopo de runtime. Imports exclusivos de tipagem ficam sob
`TYPE_CHECKING`. Campos relacionais Django referenciam models obrigatoriamente
por string, como `"organizacoes.Organizacao"`, para desacoplar o carregamento da
ordem de imports. Consulte o ADR 0006.

`.ai/CONVENTIONS.md` é o documento normativo — leia antes de criar models, serializers
ou views. Os pontos mais fáceis de violar:

- Apps sempre em `apps/`. Cada módulo (`models`, `serializers`, `views`, `filters`,
  `handlers`, `docs`, `dashboards`) é **um arquivo**; `tests/` é a única exceção
  (pacote com `__init__.py`).
- Todo field declara `help_text` **e** `db_comment` com o mesmo valor, e os argumentos
  seguem a ordem fixa por tipo de field.
- `Meta` com `db_table`, `ordering = ("-id",)`, `verbose_name`, `verbose_name_plural`
  e `permissions`. Choices (preferencialmente `IntegerChoices`) no topo do `models.py`.
- Todo model herda de `Base` (ou `BaseGlobal`, se for identidade) e implementa `__str__`.
- Toda view define `filterset_class` explicitamente.
- QuerySets: `select_related`/`prefetch_related` obrigatórios; `only()`/`values()` para
  limitar colunas.
- Docstrings no padrão Google; prefira `r = f(); g(r)` a `g(f())`.
- Testes com pytest + `factory_boy` (um factory por app); Celery roda eager em teste;
  prefira testes DB-less quando o comportamento não depende do banco.

## Versionamento

SemVer via commitizen, com a versão espelhada em `api/settings.py`
(`SPECTACULAR_SETTINGS["VERSION"]`) — `make version-check` (rodado na CI) falha se
divergir de `pyproject.toml`. Release automatizado por release-please. Ver
`VERSION_RELEASE.md` e `docs/how-to/publicar-versao.md`.

## Notas

- `examples/` e `libs/` estão fora do ruff e da coleta do pytest (`norecursedirs`).
- `manage.py seed_demo` cria dados locais idempotentes (organização `demo`, usuário
  `demo@example.com` / `demo123456`); é bloqueado em produção.
- A documentação segue Diátaxis em `docs/` (tutorial / how-to / reference / explanation
  / adr) e é validada com `mkdocs build --strict` na CI.
