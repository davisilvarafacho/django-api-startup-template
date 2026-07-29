# Development Container Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide a reproducible Dev Containers environment with an interactive Django app, PostgreSQL 16, Redis 7, and a continuously running Celery worker.

**Architecture:** Add a development-only image and Compose project under `.devcontainer/`; do not reuse or modify the production image. Install the Python environment at `/opt/venv` so the repository bind mount cannot hide it, then mount the source at `/workspace` for both `app` and `worker`. Keep migrations, seed creation, and `runserver` explicit.

**Tech Stack:** Dev Containers specification, Docker Compose, Python 3.12, uv 0.11.28, Node.js/npm, PostgreSQL 16, Redis 7, Celery 5.

## Global Constraints

- Services must be named `app`, `db`, `redis`, and `worker`.
- Do not add Celery Beat or observability services.
- Do not read the host `.env`; all values are local, versioned, and non-secret.
- Do not publish PostgreSQL or Redis ports to the host.
- Forward only application port `8000` through Dev Containers.
- `postCreateCommand` must run `uv sync --frozen --group dev && npm ci`.
- Do not run migrations, seed data, or Django automatically.
- The worker command is `uv run celery -A api worker -l info`.
- The production `Dockerfile` and root `docker-compose.yml` remain unchanged.

---

### Task 1: Development image

**Files:**
- Create: `.devcontainer/Dockerfile`

**Interfaces:**
- Consumes: root `pyproject.toml`, `uv.lock`, `package.json`, and `package-lock.json`.
- Produces: image with user `vscode`, Python 3.12, uv, Node/npm, build headers, and `/opt/venv`.

- [ ] **Step 1: Add the development Dockerfile**

Create `.devcontainer/Dockerfile`:

```dockerfile
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    curl \
    g++ \
    gcc \
    git \
    libpq-dev \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /uvx /bin/

RUN groupadd --gid 1000 vscode \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash vscode \
    && mkdir -p /workspace /opt/venv \
    && chown -R vscode:vscode /workspace /opt/venv

WORKDIR /workspace

COPY --chown=vscode:vscode pyproject.toml uv.lock ./

USER vscode

RUN uv sync --frozen --group dev --no-install-project

CMD ["sleep", "infinity"]
```

- [ ] **Step 2: Build the image before Compose exists**

Run:

```bash
docker build -f .devcontainer/Dockerfile -t drf-base-api-devcontainer .
```

Expected: build exits `0`, including the frozen `uv sync`.

- [ ] **Step 3: Verify toolchain versions**

Run:

```bash
docker run --rm drf-base-api-devcontainer \
  bash -lc 'python --version && uv --version && node --version && npm --version'
```

Expected: Python reports `3.12.x`; uv, Node, and npm all exit `0`.

- [ ] **Step 4: Commit the development image**

```bash
git add .devcontainer/Dockerfile
git commit -m "build: add development container image"
```

---

### Task 2: Four-service development Compose

**Files:**
- Create: `.devcontainer/docker-compose.yml`

**Interfaces:**
- Consumes: image definition from Task 1 and the repository bind mount.
- Produces: internal services `app`, `db`, `redis`, and `worker`.

- [ ] **Step 1: Write the Compose configuration**

Create `.devcontainer/docker-compose.yml`:

```yaml
name: drf-base-api-devcontainer

x-app-environment: &app-environment
  DJANGO_ENVIRONMENT: development
  DJANGO_DEBUG: "True"
  DJANGO_SECRET_KEY: devcontainer-only-secret
  DJANGO_ALLOWED_HOSTS: 127.0.0.1,localhost
  DJANGO_CSRF_TRUSTED_ORIGINS: http://127.0.0.1:8000,http://localhost:8000
  DATABASE_NAME: base
  DATABASE_USER: postgres
  DATABASE_PASSWORD: postgres
  DATABASE_HOST: db
  DATABASE_PORT: "5432"
  REDIS_HOST: redis
  REDIS_PORT: "6379"
  POSTHOG_DISABLED: "True"

x-app-build: &app-build
  context: ..
  dockerfile: .devcontainer/Dockerfile

services:
  app:
    build: *app-build
    command: sleep infinity
    environment: *app-environment
    volumes:
      - ..:/workspace:cached
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy

  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: base
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
    volumes:
      - devcontainer_postgres:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres -d base"]
      interval: 5s
      timeout: 5s
      retries: 10

  redis:
    image: redis:7-alpine
    volumes:
      - devcontainer_redis:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 10

  worker:
    build: *app-build
    command: uv run celery -A api worker -l info
    environment: *app-environment
    volumes:
      - ..:/workspace:cached
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy

volumes:
  devcontainer_postgres:
  devcontainer_redis:
```

- [ ] **Step 2: Validate the resolved Compose model**

Run:

```bash
docker compose -f .devcontainer/docker-compose.yml config --quiet
docker compose -f .devcontainer/docker-compose.yml config --services
```

Expected: first command exits `0`; second prints exactly `app`, `db`, `redis`,
and `worker`, one per line.

- [ ] **Step 3: Confirm host isolation in the resolved model**

Run:

```bash
docker compose -f .devcontainer/docker-compose.yml config
```

Expected: `db` and `redis` contain no `ports` keys, no `env_file` key exists,
and only the local values declared above appear.

- [ ] **Step 4: Commit the Compose topology**

```bash
git add .devcontainer/docker-compose.yml
git commit -m "build: add devcontainer services"
```

---

### Task 3: Dev Containers editor contract

**Files:**
- Create: `.devcontainer/devcontainer.json`

**Interfaces:**
- Consumes: `.devcontainer/docker-compose.yml` service `app`.
- Produces: editor attachment, port forwarding, extensions, and post-create dependency sync.

- [ ] **Step 1: Add `devcontainer.json`**

Create `.devcontainer/devcontainer.json`:

```json
{
  "name": "DRF Base API",
  "dockerComposeFile": "docker-compose.yml",
  "service": "app",
  "workspaceFolder": "/workspace",
  "shutdownAction": "stopCompose",
  "remoteUser": "vscode",
  "forwardPorts": [8000],
  "postCreateCommand": "uv sync --frozen --group dev && npm ci",
  "customizations": {
    "vscode": {
      "extensions": [
        "charliermarsh.ruff",
        "ms-python.python"
      ],
      "settings": {
        "python.defaultInterpreterPath": "/opt/venv/bin/python"
      }
    }
  }
}
```

- [ ] **Step 2: Validate JSON and cross-file references**

Run:

```bash
uv run python -m json.tool .devcontainer/devcontainer.json
docker compose -f .devcontainer/docker-compose.yml config --quiet
```

Expected: both commands exit `0`; `service` is `app`, `workspaceFolder` is
`/workspace`, and the only forwarded port is `8000`.

- [ ] **Step 3: Commit the editor configuration**

```bash
git add .devcontainer/devcontainer.json
git commit -m "build: configure Dev Containers workspace"
```

---

### Task 4: Runtime smoke test

**Files:**
- Verify only; no expected file changes.

**Interfaces:**
- Consumes: all `.devcontainer/` files and the `seed_demo` command.
- Produces: evidence that the complete environment works without the host `.env`.

- [ ] **Step 1: Prove the host `.env` is not required**

Run:

```bash
docker compose -f .devcontainer/docker-compose.yml config --quiet
```

Expected: exits `0` even when no root `.env` file exists.

- [ ] **Step 2: Build and start all services**

Run:

```bash
docker compose -f .devcontainer/docker-compose.yml up -d --build
docker compose -f .devcontainer/docker-compose.yml ps
```

Expected: `app`, `db`, `redis`, and `worker` are running; `db` and `redis` are
healthy.

- [ ] **Step 3: Apply migrations and seed from `app`**

Run:

```bash
docker compose -f .devcontainer/docker-compose.yml exec app make migrate
docker compose -f .devcontainer/docker-compose.yml exec app \
  uv run python manage.py seed_demo
```

Expected: migrations complete and the seed reports `demo@example.com`,
`demo123456`, and `X-Organization: demo`.

- [ ] **Step 4: Confirm the worker is connected to Redis**

Run:

```bash
docker compose -f .devcontainer/docker-compose.yml logs --no-color worker
```

Expected: logs contain `celery@`, `ready`, and a Redis transport using host
`redis`; they contain no connection-refused loop.

- [ ] **Step 5: Confirm Django can start explicitly**

Run:

```bash
docker compose -f .devcontainer/docker-compose.yml exec app \
  timeout 15s uv run python manage.py runserver 0.0.0.0:8000
```

Expected: output contains `Starting development server`; timeout exits after the
smoke window without a Django startup traceback.

- [ ] **Step 6: Stop the smoke environment without deleting volumes**

Run:

```bash
docker compose -f .devcontainer/docker-compose.yml down
```

Expected: containers and network are removed; named volumes remain.

---

### Task 5: Devcontainer onboarding documentation

**Files:**
- Modify: `docs/tutorial/primeiro-ambiente.md`
- Modify: `docs/how-to/desenvolvimento-local.md`

**Interfaces:**
- Consumes: Dev Containers configuration and the `seed_demo` workflow.
- Produces: explicit local-container onboarding with no hidden writes.

- [ ] **Step 1: Add a Dev Containers path to the tutorial**

Append this section to `docs/tutorial/primeiro-ambiente.md`:

````markdown
## Alternativa: Dev Containers

Com Docker e uma ferramenta compatível com a especificação Dev Containers:

1. Abra o repositório no container.
2. Aguarde `uv sync --frozen --group dev && npm ci`.
3. Execute `make migrate`.
4. Execute `uv run python manage.py seed_demo`.
5. Inicie a API com `make run`.

PostgreSQL, Redis e o worker Celery já acompanham o ambiente. Migrações, seed e
servidor permanecem explícitos; abrir o container não grava dados da aplicação.
````

- [ ] **Step 2: Document service inspection and lifecycle**

Append this section to `docs/how-to/desenvolvimento-local.md`:

````markdown
## Ambiente com Dev Containers

O diretório `.devcontainer/` abre o editor no serviço `app` e sobe também
PostgreSQL 16, Redis 7 e um worker Celery. O ambiente usa somente credenciais
locais versionadas e não lê o `.env` do host.

Depois da criação:

```bash
make migrate
uv run python manage.py seed_demo
make run
```

Para inspecionar os serviços a partir do host:

```bash
docker compose -f .devcontainer/docker-compose.yml ps
docker compose -f .devcontainer/docker-compose.yml logs -f worker
```

O Celery Beat e a stack de observabilidade não fazem parte do devcontainer.
````

- [ ] **Step 3: Build the documentation**

Run:

```bash
uv run mkdocs build --strict
```

Expected: exits `0` and the tutorial/how-to pages render without broken
navigation.

- [ ] **Step 4: Commit the Dev Containers documentation**

```bash
git add docs/tutorial/primeiro-ambiente.md docs/how-to/desenvolvimento-local.md
git commit -m "docs: add devcontainer onboarding"
```

---

### Task 6: Devcontainer final verification

**Files:**
- Verify only; no expected file changes.

**Interfaces:**
- Consumes: Tasks 1–5.
- Produces: final configuration, runtime, and documentation evidence.

- [ ] **Step 1: Run static checks**

Run:

```bash
uv run python -m json.tool .devcontainer/devcontainer.json
docker compose -f .devcontainer/docker-compose.yml config --quiet
uv run mkdocs build --strict
```

Expected: all three commands exit `0`.

- [ ] **Step 2: Repeat the four-service smoke test**

Run:

```bash
docker compose -f .devcontainer/docker-compose.yml up -d --build
docker compose -f .devcontainer/docker-compose.yml exec app make migrate
docker compose -f .devcontainer/docker-compose.yml exec app \
  uv run python manage.py seed_demo
docker compose -f .devcontainer/docker-compose.yml ps
docker compose -f .devcontainer/docker-compose.yml logs --no-color worker
docker compose -f .devcontainer/docker-compose.yml down
```

Expected: migrations and seed succeed, all four services run, the worker reports
ready, and shutdown exits `0`.
