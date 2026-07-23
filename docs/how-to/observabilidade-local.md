# Subir a observabilidade localmente

A stack local é Grafana + Tempo (traces) + Loki (logs) + Prometheus (métricas).
Ela roda na mesma rede do `docker-compose.yml`, então **suba o compose principal
antes**.

## 1. Subir os serviços

```bash
make up        # Postgres + Redis (e a rede que a stack de observabilidade usa)
make obs-up    # Grafana, Tempo, Loki, Prometheus, Promtail
```

Grafana em <http://localhost:3001> — login desabilitado, entra direto. As
datasources e o dashboard "DRF Base API — visão geral" já vêm provisionados.

## 2. Ligar os traces

Os pacotes do OpenTelemetry estão num grupo opcional:

```bash
uv sync --group observability
```

E no `.env`:

```bash
OTEL_ENABLED=True
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318   # tempo, se o Django roda no host
```

Rodando o Django em container, use `http://tempo:4318`.

## 3. Conferir cada sinal

| Sinal | Onde | O que esperar |
|---|---|---|
| Métricas | `curl localhost:8000/metrics` | Séries `django_http_*`. Fora das redes internas o endpoint responde 403 — é o comportamento correto. |
| Logs | Grafana → Explore → Loki → `{job="drf-base-api"}` | Uma linha JSON por request com `request_id`. |
| Traces | Grafana → Explore → Tempo | Spans de request, query e task. |
| Correlação | No painel de log, clicar em **TraceID** | Abre o trace correspondente no Tempo. |

Em desenvolvimento o log sai como texto legível no console, não como JSON — o
Promtail lê `logs/api.jsonl`, que só é escrito em produção. Para ver o pipeline
completo de log localmente, rode com `DJANGO_ENVIRONMENT=production`.

## 4. Health checks

```bash
curl localhost:8000/health/         # liveness: 200, sem tocar em dependência
curl localhost:8000/health/ready/   # readiness: banco, cache, broker e storage
```

O readiness devolve 503 e diz **qual** dependência caiu:

```json
{
  "ok": false,
  "checks": {
    "banco": { "ok": true, "duracao_ms": 1.2 },
    "cache": { "ok": false, "erro": "ConnectionError: ...", "duracao_ms": 3.4 }
  }
}
```

O `HEALTHCHECK` do `Dockerfile` aponta para `/health/` (liveness). Aponte o
readiness do orquestrador (`readinessProbe` no Kubernetes) para `/health/ready/`
— nunca o liveness, ou uma indisponibilidade momentânea do banco reinicia toda a
frota em vez de apenas tirá-la do balanceador.

## 5. Correlation ID

Toda resposta traz `X-Request-ID`. Para rastrear uma chamada específica ponta a
ponta, mande o seu:

```bash
curl -H "X-Request-ID: minhachamada123" localhost:8000/health/ready/ -i
```

O mesmo id aparece no log, na tag do Sentry e na task do Celery que a request
disparar.

## 6. Derrubar

```bash
make obs-down
```

Os volumes são preservados; para descartar os dados, acrescente `-v` ao
`docker compose down`.
