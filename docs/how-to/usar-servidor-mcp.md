# Usar o servidor MCP

O template expõe a tool `health` por dois transportes que compartilham o mesmo
servidor lógico:

- `stdio`, para um cliente local iniciar um subprocesso sem porta ou OAuth;
- Streamable HTTP, para acesso remoto autenticado por OAuth 2.1.

A tool verifica todos os aliases de banco e cache configurados no Django. Ela
retorna apenas status e tipo de erro; mensagens de exceção, hosts, credenciais e
traces não fazem parte da resposta.

## Executar localmente por `stdio`

No diretório do projeto, execute:

```bash
uv run --frozen python -m apps.api.mcp_server
```

`stdin` e `stdout` ficam reservados ao protocolo MCP. Logs e warnings são
escritos em `stderr`. O bootstrap carrega `.env` sem sobrescrever variáveis já
definidas pelo processo.

Um cliente que aceite configuração JSON pode iniciar o servidor assim:

```json
{
  "mcpServers": {
    "django-api": {
      "command": "uv",
      "args": [
        "run",
        "--frozen",
        "python",
        "-m",
        "apps.api.mcp_server"
      ],
      "cwd": "/caminho/absoluto/para/o/projeto"
    }
  }
}
```

Esse transporte não aplica OAuth: a fronteira de segurança é o usuário e o
processo local que recebeu permissão para executar o comando. Banco e Redis
continuam necessários para que `health` os reporte como saudáveis.

## Configurar Streamable HTTP

O processo HTTP falha no startup se a configuração estiver incompleta ou
inconsistente. Configure no `.env`:

| Variável | Contrato |
|---|---|
| `MCP_SERVER_URL` | URL pública canônica terminada em `/mcp`. |
| `MCP_AUTH_ISSUER_URL` | Issuer exato presente no claim `iss`. |
| `MCP_AUTH_AUDIENCE` | Deve ser idêntica a `MCP_SERVER_URL`. |
| `MCP_AUTH_JWKS_URL` | Endpoint JWKS usado para validar assinatura e rotação de chaves. |
| `MCP_AUTH_ALGORITHMS` | Algoritmos assimétricos aceitos, separados por vírgula; padrão `RS256`. |
| `MCP_ALLOWED_HOSTS` | `host[:port]` exatos aceitos pelo transporte. |
| `MCP_ALLOWED_ORIGINS` | Origins de browser exatas; deixe vazio sem cliente browser. |

Exemplo local com Authorization Server também local:

```dotenv
MCP_SERVER_URL=http://mcp.localhost:8000/mcp
MCP_AUTH_ISSUER_URL=http://localhost:9000
MCP_AUTH_AUDIENCE=http://mcp.localhost:8000/mcp
MCP_AUTH_JWKS_URL=http://localhost:9000/.well-known/jwks.json
MCP_AUTH_ALGORITHMS=RS256
MCP_ALLOWED_HOSTS=mcp.localhost:8000
MCP_ALLOWED_ORIGINS=http://localhost:3000
```

Esse exemplo de Authorization Server em `localhost` pressupõe que o Uvicorn
também esteja rodando diretamente no host. Dentro do Compose, `localhost`
aponta para o próprio container MCP; nesse caso, use URLs HTTPS do provedor que
sejam alcançáveis tanto pelo container quanto pelos clientes.

HTTP fora de produção só é permitido para `localhost`, subdomínios
`.localhost` e endereços de loopback. Em produção, todas as URLs e origins usam
HTTPS:

```dotenv
MCP_SERVER_URL=https://mcp.example.com/mcp
MCP_AUTH_ISSUER_URL=https://auth.example.com
MCP_AUTH_AUDIENCE=https://mcp.example.com/mcp
MCP_AUTH_JWKS_URL=https://auth.example.com/.well-known/jwks.json
MCP_AUTH_ALGORITHMS=RS256
MCP_ALLOWED_HOSTS=mcp.example.com
MCP_ALLOWED_ORIGINS=https://client.example.com
```

Não use `*`, paths em hosts ou paths/query/fragment em origins. O hostname e a
porta de `MCP_SERVER_URL` precisam aparecer exatamente em
`MCP_ALLOWED_HOSTS`.

## Perfil do access token

O verifier built-in aceita **access tokens JWT assinados assimetricamente**,
com chave publicada no JWKS. O token precisa conter:

- header `typ` igual a `at+jwt` ou `application/at+jwt`, conforme RFC 9068;
- `iss` igual a `MCP_AUTH_ISSUER_URL`;
- `aud` contendo `MCP_SERVER_URL`, como string ou item de uma lista;
- `exp` válido e `nbf` válido quando presente;
- `client_id`, ou `azp` como fallback;
- `scope` como string separada por espaços, contendo `health:read`.

Um provedor OIDC pode emitir access tokens opacos. OIDC por si só não garante
JWT; nesse caso, este verifier não serve e será necessário implementar
introspection ou outro `TokenVerifier`.

Enquanto `health` for a única tool, `health:read` é o scope global do endpoint
MCP. Antes de adicionar tools com permissões diferentes, revise a autorização:
`AuthSettings.required_scopes` é aplicado ao endpoint inteiro.

### Obter token por client credentials

O endpoint e os nomes dos parâmetros variam por provedor. Um pedido típico é:

```bash
curl --request POST "${TOKEN_ENDPOINT}" \
  --user "${CLIENT_ID}:${CLIENT_SECRET}" \
  --header "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "grant_type=client_credentials" \
  --data-urlencode "scope=health:read" \
  --data-urlencode "resource=https://mcp.example.com/mcp"
```

`resource` é o parâmetro interoperável do RFC 8707. Alguns provedores também
exigem um parâmetro proprietário `audience`; envie-o adicionalmente quando a
documentação do Authorization Server exigir. Confirme o header `typ` e os
claims do JWT antes do deploy. Não registre `CLIENT_SECRET` nem o token em logs
ou arquivos versionados.

## Subir o serviço

O serviço é opt-in para que a stack continue utilizável sem um provedor OAuth:

```bash
docker compose --profile mcp up -d --build mcp nginx
docker compose --profile mcp ps
```

`APP_IMAGE` define uma única tag usada por web, MCP e workers. Em deploy,
aponte-a para uma tag imutável ou digest do registry em vez de reconstruir a
imagem separadamente por processo.

No Compose local, o endpoint público é:

```text
http://mcp.localhost:8000/mcp
```

O Nginx encaminha `/mcp` e
`/.well-known/oauth-protected-resource/mcp` para o processo Uvicorn. A porta
8001 fica somente na rede interna. O virtual host aplica corpo máximo de 1 MiB,
até 20 conexões por IP e taxa de 10 requests/s com burst de 20.

Para produção, troque `server_name mcp.localhost` em
`docker/nginx/sites/production/default.conf` pelo hostname real, configure as
variáveis HTTPS correspondentes e termine TLS no Nginx ou no load balancer
confiável imediatamente à frente.

## CORS e metadata OAuth

CORS de `/mcp` só é habilitado quando `MCP_ALLOWED_ORIGINS` não está vazio. O
preflight aceita os headers usados pelo protocolo MCP v2, incluindo
`Mcp-Method`, `Mcp-Name`, `Mcp-Protocol-Version` e `Mcp-Session-Id`. O browser
pode ler `Mcp-Session-Id` e `WWW-Authenticate`.

A Protected Resource Metadata é pública por RFC 9728 e conserva o CORS público
fornecido pelo SDK. Isso é uma exceção deliberada: não torna `/mcp` público nem
ignora sua allowlist. Headers `Authorization` enviados à metadata ou a `/live`
são removidos antes da autenticação para não provocar consultas JWKS inúteis.

Na versão 2 atual do SDK, a metadata informa `scopes_supported`, mas o desafio
403 não inclui o parâmetro `scope=`. Clientes devem descobrir o scope pela
metadata; o projeto não duplica o middleware OAuth do SDK apenas para alterar
esse header.

## Liveness e diagnóstico

`/live` é uma liveness mínima do processo e responde apenas `{"ok": true}`. O
Nginx público devolve 404 para essa rota; o healthcheck do container a consulta
diretamente na rede interna. Ela não substitui a tool autenticada `health`.

Erros comuns:

| Sintoma | Causa provável |
|---|---|
| startup falha | variável ausente, HTTP em produção, resource/audience diferentes ou allowlist inválida |
| `401` | bearer ausente, assinatura/issuer/audience/validade inválidos |
| `403` | token válido sem `health:read`, ou Origin fora da allowlist |
| `421` | `Host` não aparece exatamente em `MCP_ALLOWED_HOSTS` |
| `502` no hostname MCP | profile `mcp` desligado ou processo Uvicorn indisponível |

Consulte logs sem imprimir o token:

```bash
docker compose --profile mcp logs --tail=100 mcp
docker compose logs --tail=100 nginx
```

Valide configuração e proxy antes de publicar:

```bash
docker compose --profile mcp config --quiet
make nginx-test
```
