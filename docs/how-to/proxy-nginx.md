# Proxy reverso com nginx

A API nunca é publicada direto: em todos os ambientes conteinerizados quem
atende a porta é um nginx, e o gunicorn/runserver fica acessível apenas pela
rede interna do compose. Isso vale tanto para `docker-compose.yml` quanto para
o dev container — a URL é sempre `http://localhost:8000`.

Publicar o gunicorn direto contornaria o proxy e, com ele, os headers
`X-Forwarded-*`, o limite de upload, a ACL do `/metrics` e o upgrade para
WebSocket.

## Estrutura

```text
docker/nginx/
├── nginx.conf                     # bloco http comum (gzip, logs, maps)
├── snippets/
│   ├── proxy.conf                 # headers enviados ao Django
│   ├── security-headers.conf      # headers devolvidos ao cliente
│   └── websocket.conf             # timeouts/buffering de conexões WebSocket
└── sites/
    ├── development/default.conf   # upstream app:8000 (runserver)
    └── production/default.conf    # upstream web:80 (gunicorn)
```

O diretório de `sites/` é montado inteiro sobre `/etc/nginx/conf.d`. O mount é
de diretório, e não de arquivo, porque editor que salva trocando o inode quebra
bind mount de arquivo — o container continuaria vendo o conteúdo antigo.

| | desenvolvimento | produção |
|---|---|---|
| upstream | `app:8000` (runserver) | `web:80` (gunicorn, keepalive) |
| estáticos e mídia | servidos pelo runserver | do disco, volumes `static_files` / `media_files` |
| buffering | desligado (autoreload, streaming) | ligado |
| read timeout | 300s (aguenta breakpoint) | 60s |
| `/metrics` | liberado | só faixas internas |

O Compose de produção também possui um virtual host exato `mcp.localhost`,
separado da API. Ele resolve o serviço opcional `mcp:8001` somente quando recebe
uma request; por isso o Nginx continua iniciando com o profile `mcp` desligado.
Veja [Usar o servidor MCP](usar-servidor-mcp.md).

## Headers enviados ao Django

Definidos em `snippets/proxy.conf` e aplicados em todas as locations:

| Header | Valor | Para quê |
|---|---|---|
| `Host` | `$host` | `ALLOWED_HOSTS` e URLs absolutas |
| `X-Real-IP` | `$remote_addr` | IP do cliente |
| `X-Forwarded-For` | `$remote_addr` | `get_client_ip()` em `apps/api/autenticacao/utils.py` |
| `X-Forwarded-Proto` | `$scheme` | `SECURE_PROXY_SSL_HEADER` |
| `X-Forwarded-Host` / `-Port` | `$host` / `$server_port` | `USE_X_FORWARDED_HOST` / `_PORT` |
| `X-Request-ID` | header do cliente, ou `$request_id` | correlation id (`apps/api/core/request_id.py`) |
| `Upgrade` / `Connection` | `$http_upgrade` / map | handshake WebSocket |

Todos são **sobrescritos**: o que o cliente mandar é descartado. Sem isso
qualquer um poderia forjar `X-Forwarded-Proto: https` e passar pelas checagens
de conexão segura.

`X-Forwarded-For` recebe `$remote_addr`, e não `$proxy_add_x_forwarded_for`,
porque este nginx é a borda — a lista teria como primeiro elemento um valor
escolhido pelo cliente, justamente o que `get_client_ip()` lê. Com um load
balancer confiável na frente (ALB, Cloudflare), declare-o no server block:

```nginx
set_real_ip_from 10.0.0.0/8;   # faixa do LB
real_ip_header X-Forwarded-For;
real_ip_recursive on;
```

Aí o `$remote_addr` já passa a ser o IP real do cliente e a configuração
continua correta.

Do lado do Django, `DJANGO_BEHIND_PROXY` (padrão `True`) liga
`SECURE_PROXY_SSL_HEADER`, `USE_X_FORWARDED_HOST` e `USE_X_FORWARDED_PORT`. Só
desligue se o gunicorn for exposto sem proxy.

## WebSocket

O handshake exige `Connection: upgrade`, mas mandar esse valor em toda request
derrubaria o keepalive com o upstream. O `map` em `nginx.conf` resolve os dois
casos com um único `proxy_set_header`:

```nginx
map $http_upgrade $upstream_connection {
    default upgrade;   # cliente pediu upgrade -> vira WebSocket
    ''      '';        # request normal       -> keepalive preservado
}
```

Por isso os headers de upgrade estão em `proxy.conf` e valem em qualquer rota.
O que é exclusivo de `/ws/` são os ajustes de `websocket.conf`: read/send
timeout de 3600s (uma conexão ociosa entre mensagens seria derrubada pelos 60s
padrão) e `proxy_buffering off` (buffering seguraria cada mensagem até encher o
buffer).

Mantenha o ping/keepalive da aplicação abaixo dos 3600s.

### O upstream precisa falar ASGI

O proxy está pronto, mas quem conclui o handshake é o servidor de aplicação, e
WSGI não tem como fazer isso — o protocolo não prevê conexões de longa duração.
Hoje o projeto é WSGI nos dois ambientes: gunicorn com `worker_class = sync` em
produção e `manage.py runserver` (que continua WSGI no Django 5.2) em
desenvolvimento. O upgrade chega ao upstream, mas a conexão não vira WebSocket.

Para habilitar WebSocket de ponta a ponta em produção:

```bash
uv add uvicorn
```

```bash
GUNICORN_WORKER_CLASS=uvicorn.workers.UvicornWorker
```

e troque o `CMD` do `Dockerfile` para `api.asgi:application` — o `api/asgi.py`
já existe. Em desenvolvimento, o equivalente é subir `uvicorn api.asgi:application`
no lugar do `runserver` (o `sites/development/default.conf` aponta para
`app:8000` de qualquer jeito).

Em ambos os casos, ter as *rotas* de WebSocket exige django-channels ou um
roteador ASGI próprio: `get_asgi_application()` sozinho só atende HTTP.

## `/metrics`

A checagem de rede em `apps/api/core/metrics.py` usa o `REMOTE_ADDR`, que atrás
do proxy é sempre o IP do container do nginx — dentro das faixas internas
permitidas. Sem uma ACL no proxy, o `/metrics` ficaria aberto para quem alcança
a porta pública. Por isso o `sites/production/default.conf` restringe a rota às
faixas privadas.

O Prometheus da stack de observabilidade raspa `web:80` direto pela rede do
compose e não passa pelo nginx.

## Operação

```bash
make stack          # sobe tudo, API em http://localhost:8000
make nginx-test     # valida os dois ambientes sem subir a stack
make nginx-reload   # aplica alterações sem derrubar conexões
```

`make nginx-test` roda `nginx -t` num container descartável, com `--add-host`
para os nomes `web` e `app` — o nginx resolve os nomes do bloco `upstream` já
na validação da configuração e falharia fora da rede do compose.

Alterações em `nginx.conf` (mount de arquivo, não de diretório) podem exigir
`docker compose up -d --force-recreate nginx` em vez de `make nginx-reload`.

`NGINX_PORT` muda a porta publicada no host; ao alterá-la, ajuste também
`DJANGO_CSRF_TRUSTED_ORIGINS`.

No virtual host MCP, `Host` e `X-Forwarded-Host` usam `$http_host`, preservando
a porta pública de `http://mcp.localhost:8000/mcp`. O proxy não publica
`/live`; encaminha apenas `/mcp` e a Protected Resource Metadata. Seus limites
de corpo, conexão e taxa não afetam o virtual host da API REST.

## TLS

Termine o TLS no nginx apenas quando não houver um load balancer fazendo isso.
O `sites/production/default.conf` traz um server `:443` comentado como ponto de
partida; com um LB na frente, mantenha só o `:80` e declare o LB como proxy
confiável (`set_real_ip_from`).
