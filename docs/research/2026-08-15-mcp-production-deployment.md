# Implantação do mini MCP de saúde em produção

Data da pesquisa: 2026-08-15

Escopo: MCP Python SDK 2.0.0, revisão MCP 2026-07-28 e infraestrutura atual
do repositório.

Fontes externas: somente especificação, SDK, PyPI, RFCs e documentação oficial
do Django.

## Decisão

Se o mini MCP for **exclusivamente local**, iniciado pelo cliente como
subprocesso via `stdio`, `mcp` pode ficar no grupo `dev`. Se ele for oferecido
como um serviço remoto em produção, essa recomendação deixa de valer: o SDK é
uma **dependência de runtime** e deve estar presente na imagem que executa o
servidor.

Para este repositório, o desenho recomendado é:

```text
cliente MCP
    |
    | HTTPS + OAuth 2.1 Bearer
    v
load balancer/TLS ou nginx
    |
    | /mcp e /.well-known/oauth-protected-resource/mcp
    v
serviço/container MCP dedicado (ASGI/Starlette, Streamable HTTP)
    |
    | importa o Django e reutiliza os checks de apps/api/core
    +----> PostgreSQL
    +----> Redis

API REST existente -> nginx -> serviço web existente (Gunicorn WSGI)
```

O MCP não deve ser servido por `stdio` num daemon remoto nem injetado no
Gunicorn WSGI atual. Deve ser um processo ASGI independente, construído da mesma
imagem ou de um target específico, publicado somente pelo proxy reverso. Isso
isola o ciclo de vida e evita converter toda a API Django para ASGI apenas para
adicionar um endpoint pequeno.

Configuração de transporte recomendada para este tool:

```python
app = mcp.streamable_http_app(
    stateless_http=True,
    json_response=True,
    transport_security=transport_security,
)
```

- `json_response=True` é apropriado porque o health é uma chamada curta, sem
  progresso, subscriptions ou interação servidor-cliente durante a execução;
- na revisão 2026-07-28 o protocolo já é sem sessão; no SDK 2,
  `stateless_http=True` afeta somente clientes legados (2025-11-25 e
  anteriores), evitando afinidade de sessão também para eles;
- o endpoint público deve ser uma URL canônica HTTPS, por exemplo
  `https://mcp.example.com/mcp`.

## Por que `dev` estava correto antes e fica incorreto em produção

A validação anterior partiu da frase da spec que limitava o servidor a uso
local. Nesse cenário, o host MCP inicia um subprocesso, fala por `stdin`/`stdout`
e o servidor vive junto do ambiente de desenvolvimento. A recomendação era
coerente com esse escopo, mas não é uma propriedade do MCP.

O SDK oficial distingue explicitamente os casos:

| Aspecto | `stdio` local | Streamable HTTP remoto |
| --- | --- | --- |
| Quem inicia | O cliente inicia um subprocesso | O servidor roda como serviço independente |
| Endereço | Não há porta nem URL | Um endpoint HTTP, normalmente `/mcp` |
| Clientes | Um cliente por processo/pipe | Múltiplas conexões remotas |
| Fronteira de segurança | Processo, usuário e ambiente do launcher | TLS, autenticação, autorização, Host e Origin |
| Autenticação MCP | OAuth não se aplica; credenciais vêm do ambiente | OAuth 2.1 Bearer no HTTP |
| Lugar da dependência | `dev`, se nenhuma entrega de produção a usa | Runtime da imagem/serviço MCP |

A [especificação de `stdio`](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio)
define o subprocesso iniciado pelo cliente e reserva `stdout` para JSON-RPC. A
[especificação de Streamable HTTP](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)
define o servidor como processo independente, com cada mensagem enviada por
POST a um endpoint único. O próprio
[guia de execução do SDK](https://py.sdk.modelcontextprotocol.io/run/)
resume a escolha como `stdio` para servidor local e `streamable-http` para o
que é implantado.

### Colocação concreta no `pyproject.toml`

No estado atual, o `Dockerfile` executa:

```dockerfile
RUN uv sync --frozen --no-dev --no-install-project
```

Logo, qualquer pacote em `[dependency-groups].dev` é deliberadamente ausente
da imagem de produção. Para servir o MCP com a mesma imagem, a opção simples e
correta é declarar `mcp>=2.0.0,<3` em `[project].dependencies`. Não é
necessário instalar `mcp[cli]`: o extra adiciona `mcp dev`, `mcp run` e
`mcp install`, ferramentas de desenvolvimento, enquanto a biblioteca base já
inclui Starlette e Uvicorn. Isso consta nos
[metadados oficiais do `mcp` 2.0.0 no PyPI](https://pypi.org/pypi/mcp/2.0.0/json).

Uma alternativa de isolamento é criar um extra de projeto, por exemplo
`[project.optional-dependencies].mcp`, e um target Docker que execute
`uv sync --extra mcp`; isso só vale a complexidade se houver uma imagem MCP
separada de fato. Com o Dockerfile atual, usar o grupo `dev` não é uma
alternativa de produção.

## Processo e integração ASGI

`MCPServer` implementa o protocolo, mas não é um process manager. O
[guia oficial de deploy do SDK](https://py.sdk.modelcontextprotocol.io/run/deploy/)
explica que `mcp.run("streamable-http")` inicia exatamente um processo Uvicorn;
workers, TLS, limites e graceful shutdown pertencem ao servidor ASGI e à
plataforma. Para produção, a aplicação retornada por
`streamable_http_app()` deve ser entregue explicitamente a Uvicorn, Gunicorn
com worker ASGI ou ao process manager da plataforma.

O repositório hoje:

- inicia `gunicorn api.wsgi:application` com worker `sync`;
- possui `api/asgi.py`, mas não o usa em produção;
- encaminha o tráfego do nginx para o único upstream `web:80`;
- não possui serviço, upstream ou healthcheck do MCP.

Embora uma aplicação Starlette possa montar tanto o MCP quanto o ASGI do
Django, fazer isso aqui alteraria o servidor, o entrypoint e o comportamento
operacional de toda a API. Um serviço `mcp` dedicado no Compose é menor e mais
seguro: mesma configuração Django, banco e Redis; comando e porta próprios;
somente `expose`, nunca `ports`; publicação exclusiva pelo nginx.

Para o mini health, um worker por container é suficiente e simplifica a
operação. Escalar por réplicas também é seguro:

- clientes 2026-07-28 não têm sessão nem afinidade;
- clientes legados também ficam sem sessão com `stateless_http=True`;
- o tool não usa multi-round-trip request state nem subscriptions.

Se essas capacidades forem adicionadas no futuro, o deploy precisa ser
reavaliado: MRTR entre workers exige a mesma chave/audience de
`RequestStateSecurity`, e subscriptions entre processos exigem uma
implementação compartilhada de `SubscriptionBus`. O SDK não fornece esse bus
distribuído.

## Autenticação e autorização

Um endpoint de diagnóstico de infraestrutura não deve ficar anônimo na Internet.
Para Streamable HTTP protegido, o desenho interoperável é o MCP agir como
**OAuth 2.1 Resource Server**:

1. um Authorization Server externo autentica e emite o access token;
2. o MCP valida `Authorization: Bearer <token>` em **toda** request;
3. o token é validado quanto a assinatura/introspecção, expiração, emissor,
   audience do MCP e scopes;
4. o MCP publica RFC 9728 Protected Resource Metadata e devolve
   `WWW-Authenticate` no `401`;
5. o tool exige um scope mínimo, por exemplo `health:read`.

No SDK 2.0.0 isso é configurado com `TokenVerifier` e `AuthSettings` juntos.
`resource_server_url` deve ser a URL pública canônica HTTPS do endpoint, e
`required_scopes=["health:read"]` é suficiente enquanto houver um único tool.
O [guia oficial de autorização do SDK](https://py.sdk.modelcontextprotocol.io/run/authorization/)
mostra que o SDK implementa a metade Resource Server e a descoberta, mas não
fornece login, consentimento ou emissão de tokens.

A [especificação MCP 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)
torna a autorização opcional em geral, mas, quando usada no HTTP, define OAuth
2.1, bearer token em toda request, audience binding, RFC 9728 e status 401/403.
O [RFC 6750](https://www.rfc-editor.org/rfc/rfc6750.html) exige TLS para o uso de
bearer tokens.

### O que pode ser reaproveitado do repositório

O repositório possui Knox e API keys próprias, mas não possui Authorization
Server OAuth/OIDC, issuer metadata, JWKS ou introspecção. Além disso, a
autenticação atual vive no middleware Django/DRF; uma aplicação Starlette MCP
dedicada não atravessa esse middleware.

É tecnicamente possível escrever um `TokenVerifier` que consulte as API keys
Knox. Isso pode proteger um serviço interno com credencial pré-configurada, mas
não cria o fluxo OAuth MCP nem a descoberta de um Authorization Server. Para
um endpoint remoto interoperável, a recomendação é usar um provedor OAuth/OIDC
real (por exemplo, o provedor corporativo) e validar tokens emitidos
especificamente para o audience do MCP.

Para uma implantação estritamente privada, VPN/mTLS, ACL de rede e credencial
estática podem ser controles adicionais ou uma etapa transitória. Isso deve ser
documentado como protocolo de acesso privado, não como implementação completa
da autorização MCP. `stdio`, por sua vez, nunca chama `TokenVerifier`; sua
fronteira é o processo que o iniciou.

## Host, Origin, DNS rebinding e CORS

A revisão 2026-07-28 exige validação de `Origin` no Streamable HTTP e recomenda
autenticação em todas as conexões. O SDK adiciona também validação de `Host`.
Sem configuração explícita, `streamable_http_app()` aceita somente localhost;
um deploy em hostname real responde `421`.

Em produção, deve-se passar `TransportSecuritySettings` com allowlists exatas:

- `allowed_hosts`: o hostname público do MCP, com as variantes de porta
  realmente atendidas;
- `allowed_origins`: vazio se nenhum browser for cliente; caso haja browser,
  somente os origins HTTPS confiáveis;
- `enable_dns_rebinding_protection=True` explicitamente.

O nginx atual repassa `Host` como `$host`, portanto a allowlist do SDK deve
conter o hostname externo, não o nome Docker `mcp`. Embora o guia do SDK permita
desligar essa proteção quando o proxy já controla `Host`, mantê-la em ambas as
camadas é uma defesa barata. O server block do MCP também deve ter um
`server_name` explícito; o `server_name _` atual não é uma validação de host.

Se browsers acessarem o MCP, a política CORS do ASGI deve concordar com
`allowed_origins`. O
[guia ASGI oficial](https://py.sdk.modelcontextprotocol.io/run/asgi/#cors-for-browser-clients)
exige liberar os métodos e headers MCP usados, incluindo `Authorization` e
`Content-Type`, e expor `Mcp-Session-Id` para compatibilidade com clientes
legados. `*` não deve ser usado com credenciais em produção.

## TLS e proxy reverso

O arquivo `docker/nginx/sites/production/default.conf` atende somente HTTP; o
bloco HTTPS é um exemplo comentado. Antes de publicar o MCP, TLS deve terminar
num load balancer confiável ou ser habilitado no nginx. Entre o terminador TLS
e o container pode haver HTTP numa rede privada, desde que a borda preserve a
URL canônica HTTPS e sobrescreva headers encaminhados, como o snippet atual já
faz.

No nginx, um hostname dedicado é preferível. Ele deve encaminhar ao upstream
MCP pelo menos:

- `/mcp`;
- `/.well-known/oauth-protected-resource/mcp`;
- quaisquer endpoints adicionais de discovery exigidos pela configuração de
  autorização.

Com `json_response=True` e o tool de health atual, `proxy_buffering on` e o
timeout de leitura de 60 segundos não impedem o protocolo; o check deve terminar
em muito menos tempo. Se forem adicionadas respostas SSE ou
`subscriptions/listen`, será necessário desligar buffering nessa location,
alongar o read timeout e preservar keep-alives. A especificação recomenda
`X-Accel-Buffering: no` para SSE.

O SDK limita POSTs a 4 MiB por padrão, enquanto o nginx aceita 20 MiB. Para um
tool sem argumentos grandes, vale reduzir o limite do MCP/da location. Também
devem existir rate limit, limites de conexão e timeouts na borda; o SDK não os
fornece.

## Health do próprio serviço e ciclo de vida Django

O tool `health` é uma interface de diagnóstico autenticada; ele não substitui
o healthcheck do container. O serviço MCP precisa de uma rota HTTP simples de
liveness, sem banco ou Redis, usada internamente pelo Docker/orquestrador. O
SDK permite `@mcp.custom_route()`, mas alerta que custom routes não são
autenticadas. Portanto, essa rota deve retornar somente `ok`, não deve expor
aliases ou falhas e, idealmente, não deve ser roteada pelo nginx público.

O `Dockerfile` atual testa `http://localhost:80/health/`, que pertence ao
servidor Django existente. Um processo/container MCP dedicado precisa de
healthcheck próprio; reutilizar o atual testaria o processo errado.

Como o MCP é um processo longo fora do ciclo request/response do Django, o tool
deve chamar `django.db.close_old_connections()` antes/depois do trabalho ou
fechar explicitamente as conexões usadas. A
[documentação Django 5.2](https://docs.djangoproject.com/en/5.2/ref/databases/#persistent-connections)
alerta que conexões abertas fora do ciclo HTTP do Django permanecem até
fechamento explícito ou timeout. Os checks devem continuar reutilizando os
primitivos de `apps/api/core/health_check.py`, sem duplicar as consultas, e
sanitizar qualquer erro antes de devolvê-lo ao modelo.

## Lacunas atuais para esse deploy

| Área | Estado atual | Necessário para o MCP remoto |
| --- | --- | --- |
| Dependência | `mcp` ausente; imagem exclui `dev` | SDK no runtime da imagem MCP |
| Processo | Gunicorn WSGI `sync` | Processo ASGI dedicado |
| Compose | Sem serviço MCP | Serviço sem porta pública, dependente de DB/Redis |
| nginx | Um upstream `web`; host curinga | Host/location e upstream MCP explícitos |
| TLS | Apenas exemplo comentado | HTTPS real no LB ou nginx |
| Auth | Knox/DRF, sem OAuth AS | `TokenVerifier`, `AuthSettings`, issuer e scope |
| Segurança de transporte | Não se aplica hoje | Allowlist de Host/Origin e CORS estrito se necessário |
| Sessão | Não existe serviço | 2026 sessionless + legado `stateless_http=True` |
| Resposta | nginx faz buffering | Adequado para JSON; ajustar se houver SSE |
| Health do processo | Só web e nginx | Liveness independente do MCP |
| Conexões Django | Fechadas pelo ciclo HTTP do Django | Política explícita no processo MCP |

## Sequência recomendada de implementação

1. Corrigir a spec para assumir dois modos: `stdio` local e Streamable HTTP
   remoto, compartilhando o mesmo registro de tools.
2. Mover `mcp>=2.0.0,<3` para dependência de runtime; manter o extra `cli` fora
   da produção.
3. Criar a aplicação ASGI MCP com `json_response=True`,
   `stateless_http=True`, limites e `TransportSecuritySettings` explícitos.
4. Integrar um Authorization Server e implementar `TokenVerifier` com audience
   e scope `health:read`; não expor o endpoint antes disso.
5. Adicionar serviço/container MCP e liveness próprio, sem publicar porta.
6. Adicionar upstream/hostname MCP no nginx, discovery OAuth e TLS.
7. Testar em processo, por Streamable HTTP real, respostas 401/403/421,
   discovery RFC 9728, Host/Origin, proxy headers e falhas de DB/Redis.
8. Manter os endpoints REST `/health/` e `/health/ready/` existentes sem
   alteração.

## Conclusão

A resposta direta é: **`mcp` só pertence a `dev` enquanto o servidor for uma
ferramenta local via `stdio`. Se vamos servi-lo em produção, ele pertence ao
runtime.**

Neste template, servir em produção significa executar uma aplicação MCP ASGI
em serviço separado, usar Streamable HTTP stateless/JSON, expô-la apenas pelo
proxy HTTPS, validar Host e Origin, e protegê-la como OAuth 2.1 Resource Server.
O Gunicorn WSGI atual continua responsável pela API REST; o MCP apenas inicializa
Django e reutiliza os checks do `core`.
