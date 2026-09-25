# Subir a observabilidade localmente

A stack local é Grafana + Tempo (traces) + Loki/Alloy (logs) + Prometheus
(métricas). Ela entra na rede da aplicação para receber traces e raspar métricas.

## 1. Subir os serviços

```bash
make up        # Postgres + Redis (e a rede que a stack de observabilidade usa)
make obs-up    # Grafana, Tempo, Loki, Prometheus e Alloy
```

Grafana em <http://localhost:3001> — login desabilitado, entra direto. As
datasources e o dashboard "DRF Base API — visão geral" já vêm provisionados.
Se a porta estiver ocupada, escolha outra antes de subir a stack, por exemplo
`GRAFANA_PORT=13001 make obs-up`.

### Com Dev Containers

Abra o devcontainer primeiro, para criar sua rede. Depois execute no host:

```bash
make dev-obs-up
```

Esse comando usa a mesma stack, mas conecta Tempo e Prometheus à rede
`django-api-startup-template-devcontainer_default` e raspa métricas em `app:8000`.

## 2. Ligar traces e logs

Os pacotes do OpenTelemetry estão num grupo opcional:

```bash
uv sync --group observability
```

O devcontainer já instala esse grupo durante o build.

E no `.env`:

```bash
OTEL_ENABLED=True
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318   # tempo, se o Django roda no host
DJANGO_JSON_LOG_FILE_ENABLED=True                  # arquivo consumido pelo Alloy
```

Em vez de exportar essas variáveis manualmente, inicie a aplicação com
`make run-observed`. No devcontainer, o endpoint já é `http://tempo:4318` e o
arquivo JSON já está habilitado.

Para observar tasks no devcontainer, evite dois workers na mesma fila. No host,
pare o worker automático e, dentro do devcontainer, inicie o instrumentado:

```bash
docker compose -f .devcontainer/docker-compose.yml stop worker
make worker-observed
```

## 3. Conferir cada sinal

| Sinal | Onde | O que esperar |
|---|---|---|
| Métricas | `curl localhost:8000/metrics` | Séries `django_http_*`. Fora das redes internas o endpoint responde 403 — é o comportamento correto. |
| Logs | Grafana → Explore → Loki → `{job="django-api-startup-template"}` | Uma linha JSON por request com `request_id`. |
| Traces | Grafana → Explore → Tempo | Spans de request, query e task. |
| Correlação | No painel de log, clicar em **TraceID** | Abre o trace correspondente no Tempo. |

Em desenvolvimento, o console permanece legível. Quando
`DJANGO_JSON_LOG_FILE_ENABLED=True`, o mesmo evento também vai para o arquivo
rotativo `logs/api.jsonl`, que o Alloy envia ao Loki. Na primeira execução após
a migração, linhas antigas ainda presentes nesse arquivo podem ser reenviadas,
pois as posições anteriores do coletor não são reaproveitadas.

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
make obs-down       # Compose principal
make dev-obs-down   # devcontainer
```

Os volumes são preservados; para descartar os dados, acrescente `-v` ao
`docker compose down`.

## 7. Alertas

Métricas sem alerta só servem para autópsia. Os contact points de Discord e
Telegram e três regras de exemplo já vêm provisionados em
`observability/grafana/provisioning/alerting/` — falta só preencher as
credenciais. Ver [alertas do Grafana](alertas-grafana.md).
