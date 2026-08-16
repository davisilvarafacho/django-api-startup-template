# Servidor MCP built-in

## Status e contexto

Esta spec substitui
`.ai/brainstorming/spec/2026-08-13-mini-mcp-django-health-design.md`. A proposta
anterior tratava o MCP como uma ferramenta exclusivamente local, executada por
`stdio`. O objetivo aprovado é diferente: todo projeto criado a partir do
template deve nascer capaz de servir MCP em produção, mantendo também uma entrada
local simples.

O servidor usará a linha estável 2 do SDK Python oficial. Nessa linha, a
interface de alto nível é `MCPServer`; `FastMCP` não existe mais. A primeira
tool será `health`, apoiada nas verificações de Django já mantidas pelo `core`.

Revisão de consistência de 2026-08-16: esta spec incorpora as correções de
bootstrap, resource/audience, JWT/JWKS, CORS e operação descritas em
`.ai/brainstorming/plan/2026-08-16-built-in-mcp-consistency-hardening.md`. As
tasks 5–9 do plano de 2026-08-15 ficam substituídas por esse plano corretivo.

## Objetivos

- Entregar um servidor MCP remoto pronto para produção via Streamable HTTP.
- Oferecer `stdio` para integração local sem manter um segundo servidor lógico.
- Colocar a interface MCP em um app Django de infraestrutura, `apps.api.mcp_server`.
- Compartilhar registro, schema, tools e regras de erro entre os transportes.
- Reutilizar as verificações de banco e cache do `apps.api.core`.
- Proteger o transporte HTTP como OAuth 2.1 Resource Server.
- Manter a API REST existente no Gunicorn WSGI, isolada do ciclo de vida ASGI
  do MCP.

## Alternativas consideradas

### Um servidor lógico com dois transportes

Escolhida. Uma única factory cria o `MCPServer` e registra as tools. Um
entrypoint executa `stdio` localmente; outro expõe a aplicação ASGI por
Streamable HTTP. Produção executa somente o segundo.

O custo adicional do `stdio` fica restrito a um entrypoint e um smoke test. Em
troca, clientes locais podem iniciar o projeto diretamente por `command`, sem
porta, proxy ou OAuth local.

### Somente Streamable HTTP

Descartada. Teria a menor superfície, mas todo uso local exigiria subir um
daemon, reservar uma porta e definir uma política de autenticação de
desenvolvimento. A economia de um entrypoint pequeno não compensa a piora na
experiência local do template.

### Dois servidores MCP independentes

Descartada. Duplicaria registro de tools, configuração, schemas e testes, com
risco de divergência entre desenvolvimento e produção.

### Montar o MCP no processo web existente

Descartada. `streamable_http_app()` é ASGI, enquanto a API atual executa
`api.wsgi:application` em Gunicorn sync. Migrar todo o processo web para ASGI
ampliaria o impacto de uma funcionalidade independente. Um serviço MCP
dedicado preserva o comportamento operacional da API.

## Localidade e estrutura

`apps/api/` é o agrupador dos apps de infraestrutura entregues pelo template.
O MCP conhece Django, tem configuração e ciclo de vida próprios e, portanto,
será um app instalado chamado `apps.api.mcp_server`, criado pelo comando
`start_api_app` do projeto e registrado alfabeticamente em `BUSINESS_APPS`.
O sufixo `_server` é necessário: como `apps/api/` é um agrupador sem
`__init__.py`, um diretório local chamado apenas `mcp` sombreia o SDK externo
`mcp` durante a coleta dos testes do próprio app.

Os arquivos ativos do app serão:

```text
apps/api/mcp_server/
├── __init__.py
├── apps.py
├── bootstrap.py
├── authentications.py
├── server.py
├── tools.py
├── asgi.py
├── __main__.py
├── migrations/
│   └── __init__.py
└── tests/
    ├── __init__.py
    ├── settings.py
    ├── test_authentications.py
    ├── test_server.py
    ├── test_http.py
    └── test_stdio.py
```

Arquivos vazios adicionais criados pelo template canônico de apps permanecem
inertes; não receberão pass-throughs nem reexportações. O app não terá models
nem migrations de schema.

Responsabilidades:

- `bootstrap.py`: carregar `.env` sem sobrescrever o processo, configurar
  `DJANGO_SETTINGS_MODULE` e executar `django.setup()` na ordem correta;
- `authentications.py`: validar tokens OIDC e construir a configuração OAuth
  usada exclusivamente pelo transporte HTTP;
- `server.py`: expor `create_mcp_server()`, a única origem do servidor e do
  registro das tools;
- `tools.py`: adaptar resultados do `core` aos tipos estruturados MCP;
- `asgi.py`: construir a aplicação Streamable HTTP para o servidor ASGI;
- `__main__.py`: iniciar o mesmo servidor por `stdio`.

`apps.py` não iniciará transportes nem fará I/O em `ready()`. Importar o app
durante `django.setup()` deve ser inerte.

## Servidor lógico e transportes

`create_mcp_server()` construirá um `MCPServer` e registrará exatamente a mesma
tool `health` para ambos os transportes. A factory aceitará opcionalmente a
configuração OAuth construída pelo adapter HTTP; `stdio` a chamará sem auth.
Nenhum entrypoint conterá regra de tool, schema ou agregação de health.

### `stdio`

O comando local será:

```bash
uv run --frozen python -m apps.api.mcp_server
```

O cliente inicia um processo exclusivo. `stdin` e `stdout` ficam reservados ao
protocolo durante todo o bootstrap e a execução; logs usam `stderr`. O modo
`stdio` não abre porta e não aplica OAuth, pois sua fronteira de segurança é o
usuário, o ambiente e o processo que o iniciou.

### Streamable HTTP

Produção executará
`uvicorn apps.api.mcp_server.asgi:create_asgi_app --factory`. Usar a factory adia a
validação da configuração OAuth para o startup do processo, sem efeitos
colaterais ao importar o módulo. A primeira ação da factory será chamar
`setup_django()`; somente depois ela importará/lerá settings e construirá auth
e servidor. Isso torna o comando Uvicorn autossuficiente, sem depender de uma
`DJANGO_SETTINGS_MODULE` declarada pelo Compose. A aplicação será criada com:

```python
mcp.streamable_http_app(
    stateless_http=True,
    json_response=True,
    transport_security=transport_security,
)
```

O endpoint MCP padrão será `/mcp`. `json_response=True` é adequado à tool
curta e sem progresso, sampling, elicitation ou subscriptions.
`stateless_http=True` evita afinidade de sessão também para clientes de revisões
legadas do protocolo.

Host e Origin serão validados por allowlists exatas. Origin permanecerá vazio
quando não houver cliente browser; CORS somente será habilitado por configuração
explícita e com origins HTTPS específicos. Fora de produção, HTTP será aceito
somente para loopback/localhost. Wildcards, paths, query strings e fragments
serão rejeitados nas allowlists.

O preflight de `/mcp` aceitará `Authorization`, `Content-Type`,
`Mcp-Protocol-Version`, `Mcp-Method`, `Mcp-Name` e `Mcp-Session-Id`; o browser
poderá ler `Mcp-Session-Id` e `WWW-Authenticate`. A Protected Resource Metadata
é pública por definição e conserva o CORS público fornecido pelo SDK como
exceção explícita à allowlist do endpoint `/mcp`. Bearers recebidos em metadata
ou liveness serão descartados antes do middleware OAuth, evitando consultas
JWKS em rotas públicas.

## Dependência

`mcp>=2.0.0,<3` entrará em `[project].dependencies`. O `Dockerfile` executa
`uv sync --no-dev`, portanto o SDK não pode estar no grupo `dev` quando o
processo MCP faz parte da entrega de produção.

`pyjwt[crypto]`, Starlette e Uvicorn também serão dependências runtime diretas:
o projeto importa PyJWT/Starlette e executa Uvicorn diretamente, portanto não
dependerá desses pacotes apenas por transitividade do SDK.

O extra `mcp[cli]` não será instalado. Os comandos de desenvolvimento do SDK
não são necessários para executar diretamente os dois entrypoints.

## Autenticação e autorização HTTP

O servidor HTTP agirá como OAuth 2.1 Resource Server. Login, consentimento e
emissão de tokens pertencem a um Authorization Server externo; o template não
tentará implementar essas funções. O verifier built-in aceita especificamente
access tokens JWT assinados assimetricamente e publicados por JWKS. Um provedor
OIDC que emita access tokens opacos precisará de outro verifier/introspection e
não é compatível automaticamente com este perfil.

Configuração prevista:

```dotenv
MCP_SERVER_URL=https://mcp.example.com/mcp
MCP_AUTH_ISSUER_URL=https://auth.example.com
MCP_AUTH_AUDIENCE=https://mcp.example.com/mcp
MCP_AUTH_JWKS_URL=https://auth.example.com/.well-known/jwks.json
MCP_AUTH_ALGORITHMS=RS256
MCP_ALLOWED_HOSTS=mcp.example.com
MCP_ALLOWED_ORIGINS=
```

Essas variáveis serão lidas nas settings pelos helpers de `utils/env.py`, nunca
por acessos dispersos a `os.environ`.

`MCP_SERVER_URL` é o identificador canônico do resource protegido e deve ser
idêntico a `MCP_AUTH_AUDIENCE`; qualquer divergência impede o startup. Seu
`host[:port]` também precisa estar em `MCP_ALLOWED_HOSTS`. URLs e allowlists são
validadas antes de construir o SDK. Os valores de issuer/resource já validados
são passados diretamente a `AuthSettings`, sem reconstrução que acrescente uma
barra ao issuer.

O verificador aceitará somente algoritmos assimétricos declarados e access
tokens com `typ=at+jwt` (ou `application/at+jwt`) conforme RFC 9068. Ele
acompanhará rotação de chaves via JWKS e validará assinatura, issuer, presença
da audience canônica, expiração e início de validade. `aud` poderá ser string
ou lista. Chaves desconhecidas terão cache negativo limitado e cooldown global
de refresh, impedindo que valores `kid` controlados pelo cliente amplifiquem
consultas ao provedor. A
tool `health` exigirá `health:read`; como `AuthSettings.required_scopes` atua no
endpoint inteiro, esse scope é global ao MCP enquanto `health` for a única
tool. Adicionar tools exige revisar esse modelo de autorização.

O SDK publicará RFC 9728 Protected Resource Metadata e desafios
`WWW-Authenticate`. Ausência ou invalidade de credencial produz `401`; token
válido sem o scope exigido produz `403`. A autenticação ocorre antes da tool.
Na linha 2 atual, a metadata publica `scopes_supported`, mas o desafio 403 do
SDK não inclui `scope=`. Essa limitação será documentada em vez de duplicar o
middleware OAuth do SDK.

Todo processo Streamable HTTP exigirá configuração OAuth completa; ausência ou
inconsistência impedirá seu início, inclusive em desenvolvimento. `stdio`
continuará disponível sem essas variáveis. Knox e API keys existentes continuam
exclusivos da API REST e não serão adaptados para o MCP.

## Deploy

A mesma imagem de aplicação alimentará processos distintos:

```text
nginx
├── api.example.com → web:80 → Gunicorn WSGI → Django REST
└── mcp.example.com → mcp:8001/mcp → Uvicorn ASGI → MCPServer
```

O Compose ganhará um serviço `mcp` que:

- usa a mesma tag de imagem, `.env`, PostgreSQL e Redis do serviço `web`;
- pertence ao profile explícito `mcp`, pois não pode iniciar antes de o
  operador configurar um provedor OIDC;
- expõe a porta somente na rede interna, sem `ports` no host;
- depende da saúde de PostgreSQL e Redis;
- executa um worker por container e escala por réplicas;
- possui rota de liveness própria, sem I/O externo nem detalhes internos.

O Nginx ganhará hostname e upstream exclusivos para MCP. Encaminhará `/mcp`,
Protected Resource Metadata e os headers necessários. TLS terminará no Nginx
ou em um load balancer confiável imediatamente à frente. A porta interna nunca
será a interface pública. O hostname MCP limitará corpo a 1 MiB, conexões
concorrentes e taxa por IP sem alterar o virtual host da API REST.

O upstream usará resolução DNS tardia para que o Nginx da API continue
iniciando quando o profile `mcp` estiver desligado. Nesse estado, somente o
hostname MCP responde `502`; a API REST permanece disponível.

A rota de liveness do processo não será publicada pelo proxy. Ela responde
somente `ok` e não substitui a tool autenticada `health`.

## Tool `health`

`health` não recebe argumentos. Ela verifica todos os aliases presentes em
`settings.DATABASES` e `settings.CACHES`, continuando depois de falhas
individuais.

Para bancos, o `core` executa `SELECT 1` por alias. Para caches, cria uma chave
exclusiva, grava com expiração curta, lê o valor e tenta apagar a chave em
`finally`. Leitura divergente, exceção no cleanup e retorno `False` de
`delete()` tornam o alias unhealthy. O prefixo da chave é genérico ao módulo de
dependency health, não ao consumidor MCP.

O `core` concentrará os primitivos de verificação e retornará dados Python,
sem importar MCP ou construir `JsonResponse`. O readiness REST existente
continuará verificando banco, cache, broker e storage. REST e MCP compartilharão
os primitivos de banco e cache, mas conservarão contratos de apresentação
próprios.

O retorno MCP terá tipos estruturados e schema estável:

```json
{
  "status": "healthy",
  "checks": {
    "databases": {
      "default": {"status": "healthy"}
    },
    "caches": {
      "default": {"status": "healthy"}
    }
  }
}
```

O status geral será `healthy` somente quando todos os aliases forem saudáveis.
Uma falha inclui apenas `status: "unhealthy"` e `error_type`. Mensagens de
exceção, hosts, URLs, credenciais e traces nunca atravessam a interface MCP.

Como o servidor é um processo longo fora do ciclo HTTP do Django, wrappers de
conexão serão obtidos dentro da chamada. As conexões serão fechadas
explicitamente em `finally`, inclusive depois de falha.

Falhas de infraestrutura são resultados unhealthy, não erros do protocolo. Uma
falha inesperada no adapter será registrada com detalhes no servidor e
produzirá um erro MCP genérico.

## Testes

### Núcleo de health

Os testes do `apps.api.core` cobrirão todos os aliases, isolamento de falhas,
round-trip e cleanup de cache, falha no cleanup, fechamento de conexões,
agregação do estado geral e sanitização. Essa é a única camada que repete a
matriz completa de falhas de PostgreSQL e Redis.

### Servidor em memória

`Client(create_mcp_server())` provará descoberta, schema, chamada sem
argumentos, conteúdo estruturado e conversão do relatório. Um erro inesperado
deve chegar sem detalhes internos.

### Streamable HTTP

Testes ASGI e um smoke test de rede real cobrirão chamada por URL, token válido,
token ausente, audience incorreto, scope ausente, Protected Resource Metadata,
Host com token válido, Origin, preflight MCP v2, desafios visíveis ao browser,
liveness, bootstrap independente de Django e ciclo de vida ASGI.

### `stdio`

Um smoke test de subprocesso inicializará Django, descobrirá e chamará
`health`, confirmará que `stdout` contém somente protocolo, observará logs em
`stderr` e encerrará o processo de modo limpo.

Um teste de paridade confirmará que os dois transportes expõem o mesmo nome,
schema e resultado básico. Ele não repetirá a matriz de dependências.

### Infraestrutura e gates

A validação comprovará que o SDK é importável na imagem sem dependências
`dev`, validará o Compose e executará `nginx -t`. Também serão executados os
testes focados, a suíte relevante, Ruff e `mkdocs build --strict`.

## Documentação

A documentação do produto explicará:

- como executar e configurar `stdio` num cliente local;
- como configurar issuer, audience, JWKS, URL pública e allowlists;
- como iniciar o serviço MCP no Compose;
- como terminar TLS e encaminhar o hostname MCP;
- como obter um token com `health:read` no provedor escolhido;
- diferença entre liveness do container e a tool `health`.

No Compose local, a URL pública documentada será
`http://mcp.localhost:8000/mcp`, pois o Nginx publica a porta 8000 no host. O
guia de token será provider-neutral e deixará explícito que token opaco não é
aceito pelo verifier JWT/JWKS built-in.

`.env.example`, o guia principal do MCP, o guia do proxy Nginx e o mapa de
diretórios serão atualizados. O README somente apontará para o guia principal.

## Fora de escopo

- Implementar Authorization Server, login, consentimento ou emissão de tokens.
- Reutilizar Knox ou API keys da API REST no MCP.
- Migrar o processo web Django de WSGI para ASGI.
- Executar `stdio` como daemon ou serviço de produção.
- Expor prompts, resources, subscriptions, sampling ou elicitation.
- Adicionar tools além de `health`.
- Consultar models ou dados de domínio.
- Publicar a porta do container MCP diretamente na Internet.

## Critérios de aceitação

- `apps.api.mcp_server` é um app instalado, inerte no bootstrap comum do Django.
- `stdio` e Streamable HTTP usam a mesma factory e a mesma tool.
- Produção inicia somente o serviço Streamable HTTP protegido por OAuth.
- O processo HTTP falha fechado quando sua configuração de segurança está
  incompleta.
- O comando Uvicorn factory configura Django sozinho e valida que resource,
  audience e allowlists descrevem o mesmo endpoint canônico.
- `health` lista todos os aliases de banco e cache e nunca expõe mensagens de
  exceção.
- A API REST existente continua no Gunicorn WSGI e conserva seus endpoints de
  health.
- Testes profundos cobrem a implementação uma vez; cada transporte tem um
  smoke test real.
- A imagem, o proxy, a documentação e os gates do repositório validam sem erro.
