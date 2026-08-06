# Observabilidade opcional no devcontainer com Grafana Alloy

## Objetivo

Integrar a stack local de observabilidade ao devcontainer sem aumentar o custo
do ambiente básico, substituir o Promtail descontinuado por Grafana Alloy e
consolidar o Grafana Tempo como único backend local de traces.

## Escopo

A mudança cobre a stack local baseada em Docker Compose, o devcontainer, a
configuração de logging necessária para alimentar o Loki e a documentação de
uso. O Jaeger legado e sua configuração serão removidos.

Não fazem parte deste trabalho:

- adicionar Grafana Mimir;
- iniciar a observabilidade automaticamente ao abrir o devcontainer;
- alterar a arquitetura de observabilidade de produção;
- atualizar imagens não relacionadas à migração do Promtail;
- reescrever planos e especificações históricas em `docs/superpowers/`.

## Arquitetura

`docker-compose.observability.yml` continuará sendo a fonte única da stack de
observabilidade. Ela manterá uma rede interna para a comunicação entre Grafana,
Loki, Tempo, Prometheus e Alloy e conectará Tempo e Prometheus a uma rede externa
da aplicação. O nome dessa rede será configurável, com
`drf-base-api_default` como padrão.

O fluxo normal continuará usando `make obs-up`. Para o devcontainer, os novos
alvos `make dev-obs-up` e `make dev-obs-down`, executados no host, selecionarão
a rede `drf-base-api-devcontainer_default`. Assim, a stack é opcional e a mesma
definição Compose atende aos dois ambientes sem duplicação.

O devcontainer instalará o grupo Python `observability`, mas OpenTelemetry
continuará desligado por padrão. Os alvos `make run-observed` e
`make worker-observed` ativarão a instrumentação e apontarão o exportador OTLP
para `http://tempo:4318`. Os alvos normais `make run` e `make worker` manterão o
comportamento atual.

## Componentes e fluxo de dados

### Traces

O Django e o Celery instrumentados enviarão spans por OTLP HTTP ao serviço
`tempo`. O Grafana consultará o Tempo pela rede interna da stack. O Jaeger não
participará do fluxo e `docker-compose.otel.yml` será removido.

No devcontainer, `OTEL_EXPORTER_OTLP_ENDPOINT` terá o valor
`http://tempo:4318`, ainda com `OTEL_ENABLED=False`; `make run-observed` e
`make worker-observed` alterarão somente a chave de ativação. Fora de containers,
o endpoint padrão continuará sendo `http://localhost:4318`.

### Logs

O diretório canônico será `logs/`; o caminho incorreto `logss/` será corrigido.
Uma configuração booleana `DJANGO_JSON_LOG_FILE_ENABLED` permitirá habilitar o
handler rotativo `logs/api.jsonl` fora de produção. O valor padrão será falso,
enquanto o ambiente do devcontainer o habilitará para que a coleta local
funcione sem trocar `DJANGO_ENVIRONMENT` para `production`.

O serviço `alloy`, usando a imagem fixa `grafana/alloy:v1.18.0`, montará
`./logs` como somente leitura. O arquivo `observability/config.alloy` terá uma
pipeline composta por descoberta de `*.jsonl`, leitura de arquivo, parsing JSON,
promoção apenas de `level` a label, adoção do timestamp RFC3339 e envio para
`http://loki:3100/loki/api/v1/push`. `request_id`, `trace_id` e demais campos de
alta cardinalidade permanecerão no corpo do log.

`observability/promtail-config.yaml` será removido, assim como referências
ativas ao Promtail em comentários e documentação.

### Métricas

O Prometheus continuará raspando `/metrics`. O Compose selecionará o arquivo de
configuração por variável de ambiente: o arquivo principal usará `web:80`, e
uma variante específica do devcontainer usará `app:8000`. O endpoint e sua
política de autorização não serão alterados.

### Visualização e correlação

Grafana continuará provisionando Tempo, Loki e Prometheus como datasources. O
link de `trace_id` entre Loki e Tempo e os dashboards existentes serão
preservados.

## Interface operacional

No Compose principal:

```bash
make up
make obs-up
make run-observed
```

Com o devcontainer já aberto, a stack será iniciada no host:

```bash
make dev-obs-up
```

Dentro do terminal do devcontainer, a aplicação observada será iniciada com:

```bash
make run-observed
```

Quando necessário, o worker automático poderá ser parado e substituído por um
worker instrumentado com `make worker-observed`. A documentação explicará esse
passo para evitar dois workers consumindo as mesmas filas.

O Grafana continuará disponível em `http://localhost:3001`. Os comandos
`make obs-down` e `make dev-obs-down` removerão apenas a stack correspondente e
preservarão seus volumes.

## Falhas e comportamento seguro

- Sem a stack ativa, os comandos normais não tentarão exportar traces.
- `dev-obs-up` falhará claramente se a rede do devcontainer ainda não existir;
  a documentação exigirá abrir o devcontainer antes.
- Alloy manterá posições em volume próprio para reduzir reenvio de logs após
  reinícios.
- Arquivos de log continuarão rotativos, limitados pela configuração existente.
- Nenhum socket Docker será montado no devcontainer ou no Alloy.

## Compatibilidade e migração

A migração não preservará o arquivo de posições do Promtail, portanto a primeira
execução do Alloy poderá reenviar as linhas ainda existentes em `logs/*.jsonl`.
Isso é aceitável no ambiente local e será registrado na documentação.

Os nomes dos datasources, labels e serviços consultados pelos dashboards não
mudarão. O serviço coletor passará de `promtail` para `alloy`, sem alteração da
consulta Loki `{job="drf-base-api"}`.

## Verificação

A implementação será validada por:

- testes unitários do `build_logging` cobrindo o handler JSON opcional e seus
  valores padrão;
- renderização das configurações Compose para as redes principal e do
  devcontainer;
- validação de `observability/config.alloy` com a imagem fixa do Alloy;
- subida da stack e inspeção do estado dos serviços, quando o daemon Docker
  estiver disponível;
- execução de lint, testes relevantes e build estrito da documentação;
- busca final por referências ativas a Promtail e Jaeger fora dos documentos
  históricos.

## Critérios de aceite

1. O devcontainer básico continua subindo sem a stack de observabilidade.
2. `make dev-obs-up` conecta a stack à rede do devcontainer.
3. Django e Celery podem enviar traces ao Tempo por comandos explícitos.
4. Logs JSON de desenvolvimento chegam ao Loki por Alloy preservando a label
   `level` e a correlação por `trace_id`.
5. Prometheus raspa `app:8000` no devcontainer e `web:80` no Compose principal.
6. Jaeger e Promtail não permanecem em configurações nem documentação ativa.
7. Grafana continua correlacionando métricas, logs e traces sem mudanças nos
   datasources existentes.
