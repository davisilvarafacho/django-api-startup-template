# 0002 — Logs estruturados, correlation ID e retenção de log de request

- Status: Aceito
- Data: 2026-07-23

## Contexto

O `LOGGING` do projeto estava inteiramente comentado: em produção valia o default
do Django, sem trilha de request e sem nada que ligasse uma linha de log a um
evento no Sentry.

Havia também uma decisão pendente sobre **onde guardar o log de request**. O
`drf-api-logger` grava uma linha por requisição em tabela — abordagem que já
levou bancos de produção a dezenas de GB em poucos dias, porque o volume cresce
com o tráfego e nada expira sozinho.

Por fim, faltava definir se o correlation ID viria de biblioteca
(`django-log-request-id`) ou de código próprio.

## Decisão

**Formato.** Logging estruturado com `python-json-logger`: JSON de uma linha por
evento em produção, texto legível em desenvolvimento e teste, com arquivo JSON
opcional nesses ambientes por `DJANGO_JSON_LOG_FILE_ENABLED`. A montagem fica
em `api/logging_config.py`, no mesmo espírito de `api/configure_enviroment.py` —
o `settings.py` continua sendo o único ponto de entrada, mas sem cem linhas de
dicionário no meio.

**Correlation ID próprio.** `apps/api/core/request_id.py` implementa o
middleware em cerca de 40 linhas: lê `X-Request-ID` (validando o valor, que vem
de fora), gera um UUID quando ausente, devolve o header na resposta e marca a tag
no Sentry. O id vive num `contextvar`, não em `threading.local`, para não vazar
entre requests que reciclam a mesma thread. O `before_task_publish` do Celery
carimba o id na mensagem, então a task herda a trilha de quem a disparou.

A alternativa (`django-log-request-id`) resolveria o mesmo, mas o código próprio
custa pouco e permite integrar no mesmo lugar o `trace_id` do OpenTelemetry —
que é o que fecha o link log ↔ trace no Grafana.

**Retenção.** Log de request **não vai para banco em produção**. O
`drf-api-logger` continua carregado apenas em desenvolvimento (e num banco
`logging` separado, nunca no da aplicação). Em produção, cada request emite uma
linha estruturada em stdout/arquivo, que o Grafana Alloy envia ao Loki com
`retention_period` de 30 dias.

## Consequências

- Todo log carrega `request_id`, `trace_id`, `span_id`, `usuario_id` e
  `organizacao` — filtráveis no Loki e correlacionáveis com Sentry e Tempo.
- O crescimento do log deixa de ser problema do banco: expira no Loki por
  configuração, sem rotina de expurgo.
- Handlers de e-mail (`AdminEmailHandler`) e syslog externo não voltaram: alerta
  de erro é papel do Sentry.
- Consultar request antiga passa a ser feito no Grafana, não com um `SELECT` no
  admin. É a troca consciente por não pagar o custo em banco.
