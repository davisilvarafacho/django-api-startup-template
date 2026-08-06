# Devcontainer Observability with Grafana Alloy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate the existing Grafana observability stack with the devcontainer on demand, replace Promtail with Alloy, and remove the redundant Jaeger setup.

**Architecture:** Keep `docker-compose.observability.yml` as the single stack definition and attach Tempo and Prometheus to a configurable external application network. Enable JSON file logging and OTLP exporters explicitly in development while leaving the default devcontainer lightweight and telemetry-disabled.

**Tech Stack:** Docker Compose, Grafana Alloy 1.18.0, Loki, Tempo, Prometheus, Grafana, Django logging, OpenTelemetry, pytest, MkDocs.

## Global Constraints

- Work directly on the current `main` checkout as explicitly requested by the user.
- Preserve pre-existing local modifications in `.env.example`, `api/settings.py`, `apps/api/core/errors.py`, `apps/api/core/metrics.py`, and `apps/api/core/tests/test_metrics.py`.
- Do not add Jaeger or Mimir.
- Do not auto-start observability when the devcontainer opens.
- Keep `OTEL_ENABLED=False` for normal commands.
- Keep the Loki job label `job="drf-base-api"` and only promote `level` to an indexed dynamic label.
- Pin Grafana Alloy to `grafana/alloy:v1.18.0`.

---

### Task 1: Optional JSON file logging in development

**Files:**
- Create: `apps/api/core/tests/test_logging_config.py`
- Modify: `api/logging_config.py`
- Modify: `api/settings.py`
- Modify: `.env.example`

**Interfaces:**
- Consumes: existing `build_logging(environment, level, log_root)` configuration factory.
- Produces: `build_logging(environment, level, log_root, *, write_json_file=False)` and environment variable `DJANGO_JSON_LOG_FILE_ENABLED`.

- [ ] **Step 1: Write failing logging tests**

```python
from api.logging_config import build_logging


def test_development_does_not_write_json_file_by_default(tmp_path):
    config = build_logging("development", "INFO", tmp_path)

    assert "arquivo" not in config["handlers"]
    assert config["root"]["handlers"] == ["console"]


def test_development_can_write_json_file_when_enabled(tmp_path):
    config = build_logging("development", "INFO", tmp_path, write_json_file=True)

    assert config["handlers"]["arquivo"]["filename"] == str(tmp_path / "api.jsonl")
    assert config["handlers"]["arquivo"]["formatter"] == "json"
    assert config["root"]["handlers"] == ["console", "arquivo"]


def test_production_keeps_json_file_without_explicit_opt_in(tmp_path):
    config = build_logging("production", "INFO", tmp_path)

    assert "arquivo" in config["handlers"]
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `uv run --group test pytest --no-cov apps/api/core/tests/test_logging_config.py -q`

Expected: the opt-in test fails with `TypeError` because `write_json_file` does not exist.

- [ ] **Step 3: Implement the logging flag**

Change the signature to:

```python
def build_logging(environment, level, log_root, *, write_json_file=False):
```

Create the file handler when `em_producao or write_json_file`. In
`api/settings.py`, set the canonical directory and pass the flag:

```python
LOGGING_ROOT = os.path.join(BASE_DIR, "logs/")
JSON_LOG_FILE_ENABLED = get_bool_from_env("DJANGO_JSON_LOG_FILE_ENABLED", False)
LOGGING = build_logging(
    CONFIG_ENVIRONMENT,
    LOG_LEVEL,
    LOGGING_ROOT,
    write_json_file=JSON_LOG_FILE_ENABLED,
)
```

Document `DJANGO_JSON_LOG_FILE_ENABLED=False` in `.env.example`.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run: `uv run --group test pytest --no-cov apps/api/core/tests/test_logging_config.py -q`

Expected: `3 passed`.

- [ ] **Step 5: Run Ruff on changed Python files**

Run: `uv run ruff check api/logging_config.py api/settings.py apps/api/core/tests/test_logging_config.py`

Expected: exit 0.

---

### Task 2: Replace Promtail with Alloy and make the application network selectable

**Files:**
- Create: `observability/config.alloy`
- Create: `observability/prometheus-devcontainer.yml`
- Modify: `docker-compose.observability.yml`
- Delete: `observability/promtail-config.yaml`

**Interfaces:**
- Consumes: JSON files at `/var/log/drf-base-api/*.jsonl`, Loki push API, application Docker network.
- Produces: Alloy pipeline `local.file_match -> loki.source.file -> loki.process -> loki.write`, `APPLICATION_NETWORK`, and `PROMETHEUS_CONFIG` Compose variables.

- [ ] **Step 1: Record failing structural checks**

Run:

```bash
test -f observability/config.alloy
! rg -n "promtail" docker-compose.observability.yml observability
```

Expected: failure because `config.alloy` is absent and Promtail is still configured.

- [ ] **Step 2: Add the Alloy pipeline**

Create `observability/config.alloy`:

```alloy
local.file_match "django_json_logs" {
  path_targets = [{
    __address__ = "localhost",
    __path__    = "/var/log/drf-base-api/*.jsonl",
    job         = "drf-base-api",
  }]
}

loki.source.file "django_json_logs" {
  targets    = local.file_match.django_json_logs.targets
  forward_to = [loki.process.django_json_logs.receiver]
}

loki.process "django_json_logs" {
  forward_to = [loki.write.local.receiver]

  stage.json {
    expressions = {
      level     = "level",
      logger    = "logger",
      timestamp = "timestamp",
    }
  }

  stage.labels {
    values = {
      level = "level",
    }
  }

  stage.timestamp {
    source = "timestamp"
    format = "RFC3339"
  }
}

loki.write "local" {
  endpoint {
    url = "http://loki:3100/loki/api/v1/push"
  }
}
```

- [ ] **Step 3: Replace the Compose service and network topology**

Replace service `promtail` with:

```yaml
alloy:
  image: grafana/alloy:v1.18.0
  command: ["run", "--storage.path=/var/lib/alloy/data", "/etc/alloy/config.alloy"]
  volumes:
    - ./observability/config.alloy:/etc/alloy/config.alloy:ro
    - ./logs:/var/log/drf-base-api:ro
    - alloy_data:/var/lib/alloy/data
  depends_on:
    - loki
  restart: unless-stopped
```

Give Tempo and Prometheus both the internal default network and an external
`application` network whose name is `${APPLICATION_NETWORK:-drf-base-api_default}`.
Select the Prometheus bind-mounted config with
`${PROMETHEUS_CONFIG:-./observability/prometheus.yml}` and add
`observability/prometheus-devcontainer.yml`:

```yaml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: drf-base-api
    metrics_path: /metrics
    static_configs:
      - targets: ["app:8000"]
        labels:
          service: drf-base-api
```

- [ ] **Step 4: Remove the obsolete Promtail configuration**

Delete `observability/promtail-config.yaml`.

- [ ] **Step 5: Validate both rendered Compose variants**

Run:

```bash
docker compose -f docker-compose.observability.yml config --quiet
APPLICATION_NETWORK=drf-base-api-devcontainer_default \
PROMETHEUS_CONFIG=./observability/prometheus-devcontainer.yml \
docker compose -f docker-compose.observability.yml config --quiet
```

Expected: both commands exit 0.

- [ ] **Step 6: Validate the Alloy configuration**

Run:

```bash
docker run --rm \
  -v "$PWD/observability/config.alloy:/etc/alloy/config.alloy:ro" \
  grafana/alloy:v1.18.0 \
  validate /etc/alloy/config.alloy
```

Expected: exit 0 with a valid configuration.

---

### Task 3: Add the optional devcontainer workflow and remove Jaeger

**Files:**
- Modify: `.devcontainer/Dockerfile`
- Modify: `.devcontainer/docker-compose.yml`
- Modify: `.devcontainer/devcontainer.json`
- Modify: `Makefile`
- Delete: `docker-compose.otel.yml`

**Interfaces:**
- Consumes: application network names and the normal `run`/`worker` commands.
- Produces: `dev-obs-up`, `dev-obs-down`, `run-observed`, and `worker-observed` Make targets.

- [ ] **Step 1: Record failing target/config checks**

Run:

```bash
make help | rg "dev-obs-up|run-observed|worker-observed"
docker compose -f .devcontainer/docker-compose.yml config | rg "DJANGO_JSON_LOG_FILE_ENABLED|OTEL_EXPORTER_OTLP_ENDPOINT"
```

Expected: failure because the targets and environment variables are absent.

- [ ] **Step 2: Install inert observability dependencies in the dev image**

Change both `uv sync` invocations in `.devcontainer/Dockerfile` and
`.devcontainer/devcontainer.json` to include `--group observability` while
keeping `OTEL_ENABLED=False`.

- [ ] **Step 3: Configure the devcontainer application environment**

Add these shared environment values:

```yaml
DJANGO_JSON_LOG_FILE_ENABLED: "True"
OTEL_ENABLED: "False"
OTEL_EXPORTER_OTLP_ENDPOINT: http://tempo:4318
```

- [ ] **Step 4: Add Make targets**

Add host-side targets using the devcontainer network and Prometheus config:

```make
dev-obs-up:
	APPLICATION_NETWORK=drf-base-api-devcontainer_default \
	PROMETHEUS_CONFIG=./observability/prometheus-devcontainer.yml \
	docker compose -f docker-compose.observability.yml up -d

dev-obs-down:
	APPLICATION_NETWORK=drf-base-api-devcontainer_default \
	PROMETHEUS_CONFIG=./observability/prometheus-devcontainer.yml \
	docker compose -f docker-compose.observability.yml down
```

Add application commands that set only `OTEL_ENABLED=True` before delegating to
the current command bodies:

```make
run-observed:
	OTEL_ENABLED=True uv run python manage.py runserver $(RUN_HOST):$(RUN_PORT)

worker-observed:
	OTEL_ENABLED=True uv run celery -A api worker -l info
```

- [ ] **Step 5: Remove the obsolete Jaeger Compose file**

Delete `docker-compose.otel.yml`.

- [ ] **Step 6: Verify the devcontainer and targets**

Run:

```bash
uv run python -m json.tool .devcontainer/devcontainer.json >/dev/null
docker compose -f .devcontainer/docker-compose.yml config --quiet
make help | rg "dev-obs-up|dev-obs-down|run-observed|worker-observed"
```

Expected: all commands exit 0 and all four targets are listed.

---

### Task 4: Update active documentation and verify the complete change

**Files:**
- Modify: `.gitignore`
- Modify: `api/logging_config.py`
- Modify: `api/settings.py`
- Modify: `docs/ROADMAP.md`
- Modify: `docs/adr/0002-logs-estruturados-e-retencao.md`
- Modify: `docs/how-to/desenvolvimento-local.md`
- Modify: `docs/how-to/observabilidade-local.md`
- Modify: `docs/relatorio-base-gente-grande.md`

**Interfaces:**
- Consumes: commands and environment variables added in Tasks 1–3.
- Produces: active documentation that describes Alloy, Tempo, and both optional startup workflows.

- [ ] **Step 1: Replace active Promtail/Jaeger references**

Update comments and documentation to say Alloy reads `logs/api.jsonl` and sends
it to Loki. Explain that Jaeger was removed because Tempo is the sole traces
backend. Leave historical `docs/superpowers/plans/` and
`docs/superpowers/specs/` unchanged.

- [ ] **Step 2: Document the devcontainer workflow**

Document that `make dev-obs-up` runs on the host after the devcontainer network
exists, `make run-observed` runs inside the devcontainer, and the automatic
worker must be stopped before `make worker-observed` is started.

- [ ] **Step 3: Verify active references**

Run:

```bash
rg -n -i "promtail|jaeger" \
  .devcontainer .env.example .gitignore api observability Makefile \
  docker-compose*.yml docs/ROADMAP.md docs/adr docs/how-to \
  docs/relatorio-base-gente-grande.md
```

Expected: no active references.

- [ ] **Step 4: Run focused and static verification**

Run:

```bash
uv run --group test pytest --no-cov apps/api/core/tests/test_logging_config.py -q
uv run ruff check api/logging_config.py api/settings.py apps/api/core/tests/test_logging_config.py
docker compose -f docker-compose.observability.yml config --quiet
APPLICATION_NETWORK=drf-base-api-devcontainer_default \
PROMETHEUS_CONFIG=./observability/prometheus-devcontainer.yml \
docker compose -f docker-compose.observability.yml config --quiet
docker compose -f .devcontainer/docker-compose.yml config --quiet
uv run mkdocs build --strict
git diff --check
```

Expected: every command exits 0.

- [ ] **Step 5: Run the repository test suite**

Run: `make test`

Expected: exit 0. If a pre-existing dirty test fails, record the exact failure
without modifying or committing the unrelated local work.

- [ ] **Step 6: Commit only implementation-owned hunks**

Stage clean files normally. For `.env.example` and `api/settings.py`, stage only
the observability/logging hunks and verify `git diff --cached` does not include
the pre-existing internal-IP changes before committing with Conventional
Commits.
