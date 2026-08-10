# Receber alertas do Grafana no Discord e no Telegram

O [Uptime Kuma](uptime-kuma.md) responde a uma pergunta binária: a API atende?
O Grafana Alerting responde a outra: ela está atendendo *bem*? Taxa de erro,
latência e qualquer outra série do Prometheus viram alertas aqui — com os mesmos
dois canais de destino.

Os três recursos (destinos, roteamento e regras) já vêm provisionados por
arquivo em `observability/grafana/provisioning/alerting/`. Só faltam as
credenciais, que ficam no `.env`.

!!! warning "Recursos provisionados são somente leitura na interface"
    O Grafana não deixa editar pela UI nada que veio de arquivo. Para mudar
    limiares, rótulos ou roteamento, altere o YAML e reinicie o serviço.

## 1. Criar a webhook do Discord

1. No servidor Discord, escolha o canal de destino, por exemplo `#alertas-api`.
2. Abra **Server Settings → Integrations → Create Webhook**, selecione o canal,
   nomeie-a `Grafana` e copie a **Webhook URL**. É preciso ter a permissão
   **Manage Webhooks** no canal.
3. No `.env`:

   ```bash
   GRAFANA_DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/.../...
   ```

A URL é uma credencial de envio para aquele canal: nada de Git, tickets
públicos ou screenshots. Se vazar, apague a webhook nas integrações do Discord,
crie outra e atualize o `.env`. O procedimento oficial está em
[Intro to Webhooks](https://support.discord.com/hc/en-us/articles/228383668-Intro-to-Webhooks).

## 2. Criar o bot do Telegram

1. Abra [@BotFather](https://t.me/BotFather), envie `/newbot` e siga as
   instruções. Copie o **HTTP API token** exibido no final.
2. Converse com o bot recém-criado e envie `/start`. Para alertar um grupo,
   adicione o bot ao grupo e mande uma mensagem lá.
3. Descubra o **Chat ID** trocando o token no comando abaixo:

   ```bash
   curl "https://api.telegram.org/bot<SEU_TOKEN>/getUpdates"
   ```

   No JSON, copie `message.chat.id` — em grupos costuma ser negativo. Se vier
   vazio, mande `/start` outra vez e repita. Um webhook ativo no bot bloqueia o
   `getUpdates`; nesse caso use outro bot ou remova o webhook conscientemente.
4. No `.env`:

   ```bash
   GRAFANA_TELEGRAM_BOT_TOKEN=123456789:AA...
   GRAFANA_TELEGRAM_CHAT_ID=-1001234567890
   ```

Detalhes no [guia oficial de bots](https://core.telegram.org/bots/tutorial) e na
documentação do método [`getUpdates`](https://core.telegram.org/bots/api#getupdates).

## 3. Aplicar

```bash
make obs-up   # ou, se a stack já estiver de pé:
docker compose -f docker-compose.observability.yml up -d --force-recreate grafana
```

Confira em <http://localhost:3001> → **Alerting → Contact points**: devem
aparecer `discord-alertas` e `telegram-alertas`, ambos marcados como
*Provisioned*. Use **Test** em cada um e confirme a mensagem chegando no canal.

Sem as variáveis preenchidas o Grafana sobe do mesmo jeito — o compose injeta
placeholders sintaticamente válidos — e as regras continuam avaliando. O que
falha é só a entrega, com erro nos logs do container quando um alerta dispara.

## 4. O que já vem configurado

### Regras (`rules.yaml`)

| Regra | Condição | `for` | `severity` |
|---|---|---|---|
| API fora do ar | `up{job="drf-base-api"} < 1` | 2 min | `critical` |
| Taxa de erros 5xx acima de 5% | proporção de respostas `5xx` > 0,05 | 5 min | `critical` |
| Latência p95 acima de 1s | `histogram_quantile(0.95, ...)` > 1 | 10 min | `warning` |

Os limiares são exemplos conservadores; ajuste-os ao tráfego real antes de levar
para produção. Cada regra segue o encadeamento que o próprio Grafana gera —
A (consulta PromQL) → B (`reduce`, último valor) → C (`threshold`) — e a
condição avaliada é sempre `C`.

As duas últimas usam `noDataState: NoData`: sem tráfego não há série, e silêncio
é a resposta certa. A primeira usa `Alerting`, porque ausência da série `up`
significa scrape quebrado. Localmente, isso faz a regra disparar sempre que você
deixa a API parada de propósito — **pause** a regra na interface enquanto
trabalha assim, ou troque o `noDataState` no arquivo.

### Roteamento (`notification-policies.yaml`)

Todo alerta vai para o Discord; os rotulados `severity: critical` vão também
para o Telegram. O agrupamento é por `grafana_folder` + `alertname`, para render
um incidente por regra em vez de uma mensagem por série.

Se quiser mudar o destino de uma regra, mexa no rótulo `severity` dela — não no
roteamento.

!!! danger "Provisionar políticas substitui a árvore inteira"
    O bloco `policies` sobrescreve **todas** as políticas de notificação da
    organização, inclusive a raiz padrão do Grafana. Para voltar ao estado
    original, troque o conteúdo do arquivo por `resetPolicies: [1]` e reinicie
    o Grafana.

## 5. Validar sem quebrar nada

O caminho mais rápido para um disparo real é derrubar o alvo do Prometheus:

```bash
docker compose stop web    # a regra "API fora do ar" dispara em ~2 min
docker compose start web   # e resolve na avaliação seguinte
```

Você deve receber a notificação de disparo no Discord **e** no Telegram (a regra
é `critical`) e, depois, a de resolução. Se não chegar, olhe nesta ordem:

```bash
docker compose -f docker-compose.observability.yml logs grafana | grep -i alert
```

- Contact point não aparece na UI → erro de provisionamento; o log traz a linha
  do YAML.
- Contact point aparece mas o **Test** falha → credencial errada no `.env`, ou o
  container não foi recriado depois de editá-lo (variáveis de ambiente só são
  lidas na criação).
- Regra em **Normal** quando deveria disparar → confira a consulta em
  **Explore → Prometheus**; provavelmente o job não está sendo raspado.

## Levando para produção

Nada aqui é específico de desenvolvimento além das portas. Em produção:

- Mantenha os segredos fora do arquivo — eles já são lidos do ambiente, então
  use o gerenciador de segredos da plataforma em vez do `.env`.
- Reveja `for` e limiares: os valores locais foram escolhidos para você ver o
  alerta rápido, não para evitar acordar alguém à toa.
- Configure janelas de silêncio (**Alerting → Silences**) antes de deploys que
  interrompam a API, como se faz com o **Maintenance** do Uptime Kuma.
