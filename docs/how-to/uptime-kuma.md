# Monitorar a API e receber alertas com Uptime Kuma

O [Uptime Kuma](https://github.com/louislam/uptime-kuma) é a camada de
monitoramento sintético: ele consulta a API periodicamente e avisa quando ela
cai ou volta. Para métricas, logs e traces, continue usando a
[stack de observabilidade local](observabilidade-local.md).

Nesta base, monitore `GET /health/`. É o endpoint de *liveness*: responde 200
sem depender de banco, Redis ou storage. Assim, o alerta significa que a API ou
o caminho até ela está indisponível. Para alertar especificamente sobre
dependências, crie um segundo monitor para `/health/ready/`, que responde 503
quando uma delas falha.

## 1. Subir o Uptime Kuma

Crie o `.env`, caso ainda não exista, e suba a API e o monitoramento:

```bash
cp .env.example .env
make stack    # API, nginx, workers, banco, Redis e Uptime Kuma
```

Para subir apenas a interface depois de a stack já estar em execução:

```bash
make kuma-up
```

Abra <http://127.0.0.1:3002>. Na primeira visita, crie o usuário administrador
e guarde a senha em um gerenciador de senhas. Os dados do Kuma ficam no volume
Docker `uptime_kuma_data`, portanto sobrevivem à recriação do container.

Por padrão a interface só aceita conexões da própria máquina (`127.0.0.1`).
Não a exponha diretamente na internet: se o acesso remoto for necessário,
coloque-a atrás de HTTPS com autenticação adicional, ou acesse-a via VPN/túnel
SSH. Para mudar a porta local, defina `UPTIME_KUMA_PORT` no `.env` e recrie o
serviço:

```bash
docker compose up -d --force-recreate uptime_kuma
```

Para parar sem apagar a configuração, use `make kuma-down`.

## 2. Criar o monitor HTTP da API

No Uptime Kuma, clique em **Add New Monitor** e preencha:

| Campo | Valor para a stack Docker deste projeto |
|---|---|
| Monitor Type | `HTTP(s)` |
| Friendly Name | `API — liveness` |
| URL | `http://nginx/health/` |
| Heartbeat Interval | `60` segundos |
| Retries | `3` |
| Retry Interval | `20` segundos |
| Expected Status Codes | `200-299` |

Em **Advanced**, adicione o cabeçalho HTTP abaixo e salve:

```text
Host: localhost
```

O Kuma e o nginx compartilham a rede do Docker Compose, por isso o nome
`nginx` funciona dentro do monitor. O cabeçalho é necessário porque o Django
valida o host recebido; `localhost` já está na lista padrão de hosts permitidos.

Use **Test** antes de salvar. O resultado esperado é `200 OK` e o corpo
`{"ok": true}`. Depois de salvar, marque as notificações que serão criadas nas
próximas seções.

Em produção, crie o monitor com a URL pública HTTPS, por exemplo
`https://api.exemplo.com/health/`. Não use endereço `localhost` nesse caso: ele
se refere ao servidor onde o Kuma roda, não ao servidor da API. O hostname
monitorado precisa estar em `DJANGO_ALLOWED_HOSTS`.

### Monitor opcional de readiness

Duplique o monitor e troque o nome para `API — readiness` e a URL para
`http://nginx/health/ready/` (mantenha o mesmo cabeçalho `Host`). Esse monitor
detecta falhas de PostgreSQL, Redis, broker Celery e storage. Como ele pode
alertar durante uma falha parcial, é comum direcioná-lo a um canal de menor
urgência ou aumentar as tentativas.

## 3. Configurar alerta no Discord

1. No servidor Discord, crie ou escolha um canal de texto, por exemplo
   `#alertas-api`.
2. Abra **Server Settings → Integrations → Create Webhook**. Escolha o canal,
   dê um nome como `Uptime Kuma` e copie a **Webhook URL**. É preciso ter a
   permissão **Manage Webhooks** no canal.
3. No Uptime Kuma, abra **Settings → Notifications → Setup Notification**.
4. Em **Notification Type**, escolha **Discord**; dê o nome `Discord — alertas
   API`, cole a URL copiada em **Webhook URL** e clique em **Test**.
5. Confirme a mensagem de teste no canal e clique em **Save**.
6. Abra o monitor `API — liveness`, vá a **Notifications**, marque `Discord —
   alertas API` e salve. Repita no monitor de readiness se desejar.

A URL do webhook é uma credencial de envio para aquele canal. Não a coloque no
Git, em tickets públicos ou em screenshots. Se vazar, apague/regere o webhook
nas integrações do Discord e atualize a notificação no Kuma.

Veja o procedimento oficial do Discord em
[Intro to Webhooks](https://support.discord.com/hc/en-us/articles/228383668-Intro-to-Webhooks).

## 4. Configurar alerta no Telegram

1. No Telegram, abra [@BotFather](https://t.me/BotFather), envie `/newbot` e
   siga as instruções para nomear o bot. Copie o **HTTP API token** exibido no
   final. Ele é uma credencial: não o compartilhe.
2. Abra uma conversa com o bot recém-criado e envie `/start`. Para alertar um
   grupo, adicione o bot ao grupo e envie uma mensagem nele.
3. Descubra o **Chat ID**. No navegador ou terminal, substitua o token no
   comando abaixo e execute-o depois de enviar a mensagem do passo anterior:

   ```bash
   curl "https://api.telegram.org/bot<SEU_TOKEN>/getUpdates"
   ```

   No JSON retornado, copie `message.chat.id`. Em grupos ele normalmente é um
   número negativo. Não envie o token para ninguém nem deixe esse comando no
   histórico compartilhado do shell.
4. No Uptime Kuma, abra **Settings → Notifications → Setup Notification**.
   Escolha **Telegram**, dê o nome `Telegram — alertas API`, preencha **Bot
   Token** e **Chat ID**, então clique em **Test**.
5. Depois de confirmar a mensagem de teste, salve e associe a notificação ao
   monitor em **Notifications**, como no passo 6 da configuração do Discord.

Se `getUpdates` vier vazio, envie `/start` ao bot outra vez e execute o comando
novamente. Caso o bot tenha um webhook configurado por outro sistema,
`getUpdates` não funciona enquanto esse webhook estiver ativo; use uma conversa
sem esse conflito ou remova o webhook de forma consciente.

O Telegram explica a criação do bot e a proteção do token em seu
[guia oficial](https://core.telegram.org/bots/tutorial), e documenta o método
[`getUpdates`](https://core.telegram.org/bots/api#getupdates).

## 5. Validar o alerta sem derrubar a API

Na tela do monitor, use **Pause** para interromper verificações, se necessário,
e depois **Test** na notificação para validar cada destino. Para testar um
incidente completo, altere temporariamente a URL do monitor para um caminho
inexistente, aguarde as três tentativas configuradas e confirme:

1. O monitor muda para **Down** e chega um único alerta em cada canal.
2. Restaure a URL para `/health/` e confirme o alerta de recuperação.
3. Revise os intervalos e tentativas para que uma oscilação curta não acorde a
   equipe sem necessidade.

Agende janelas de manutenção no menu **Maintenance** antes de deploys que
interrompam a API. Isso evita alertas esperados e preserva o histórico do
incidente real.
