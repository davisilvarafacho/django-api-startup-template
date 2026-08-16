# Built-in MCP Consistency Hardening Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task by task and `test-driven-development` for every behavior change.

**Goal:** Concluir o servidor MCP built-in corrigindo as divergências entre a spec de 2026-08-15, o plano original e o código entregue, com startup HTTP fail-closed, autenticação JWT/JWKS interoperável, proteção de transporte completa e documentação operacional verificável.

**Architecture:** Um único `MCPServer` continua servindo `stdio` e Streamable HTTP. O adapter HTTP inicializa Django antes de ler settings, valida uma configuração canônica em que resource URL e JWT audience são idênticos, delega autenticação e metadata ao SDK MCP e acrescenta somente os limites de transporte que o SDK não cobre. O `apps.api.core` permanece a única origem dos checks de banco/cache.

**Tech Stack:** Python 3.12, Django 5.2, MCP Python SDK 2.x, PyJWT/JWKS, Starlette, Uvicorn, pytest/AnyIO/httpx, Docker Compose, Nginx, MkDocs.

## Relação com o plano anterior

- As tarefas 1–4 de `.ai/brainstorming/plan/2026-08-15-built-in-mcp-server.md` foram parcialmente entregues e serão endurecidas aqui.
- As tarefas 5–9 daquele plano ficam substituídas por este documento. O plano antigo permanece sem edição como registro histórico.
- A spec ativa `.ai/brainstorming/spec/2026-08-15-built-in-mcp-server.md` será corrigida primeiro e continuará sendo a fonte de verdade do design.
- Não criar commits durante a execução sem pedido explícito do usuário; os checkpoints abaixo são unidades revisáveis, não autorização para commit.

## Progresso em 2026-08-16

- Tasks 1–8 concluídas, incluindo matriz JWT, smoke HTTP de rede real, paridade stdio, imagem de produção, profile Compose, proxy Nginx e documentação operacional.
- A Task 7 foi adaptada à estrutura real: o Compose usa `docker/nginx/sites/production/default.conf`; não existem os arquivos `local.conf`, `staging.conf` e `test.conf` citados no rascunho inicial.
- A Task 8 usa as referências canônicas existentes (`docs/reference/estrutura-de-diretorios.md` e `docs/how-to/proxy-nginx.md`), pois `docs/reference/settings.md` e `docs/how-to/deploy.md` não existem neste repositório.
- Task 9: testes focados, gates estáticos, Django, imagem, Compose, Nginx, docs e auditoria dos critérios concluídos. A execução global isolada aprovou 1.141 testes e falhou em 7 testes de tokens/auditoria e cache de permissões alterados concorrentemente, fora do escopo MCP; o teste de nome default do banco foi separado para evitar colisão e também passou. O gate global permanece explicitamente não verde, sem falhas nos arquivos MCP.

## Decisões corretivas

1. `create_asgi_app()` chama `setup_django()` antes de importar/ler settings e antes de construir auth ou servidor.
2. `MCP_SERVER_URL` é o resource canônico e deve ser byte a byte igual a `MCP_AUTH_AUDIENCE`; divergência aborta o startup.
3. `AuthSettings` recebe strings já validadas, sem reconstrução por `AnyHttpUrl`, preservando issuer/resource sem barra adicionada.
4. O verificador suporta somente access tokens JWT assinados por algoritmo assimétrico e JWKS. A documentação não prometerá compatibilidade com tokens opacos de qualquer provedor OIDC.
5. `aud` pode ser string ou lista; a audience configurada precisa estar presente. Não usar `strict_aud=True`.
6. `health:read` é scope global do endpoint MCP enquanto `health` for a única tool. Novas tools exigirão revisar o modelo de autorização.
7. Allowlists rejeitam wildcard, URL/path onde se espera host e Origin não canônica. HTTP só é aceito em loopback/localhost fora de produção.
8. CORS de `/mcp` inclui os headers MCP v2 (`Mcp-Method`, `Mcp-Name`) e expõe `WWW-Authenticate`; Protected Resource Metadata conserva o CORS público do SDK como exceção documentada.
9. Não reimplementar o middleware OAuth do SDK apenas para acrescentar `scope=` ao desafio 403. Registrar essa limitação do SDK e publicar `scopes_supported` na metadata.
10. Starlette e Uvicorn são dependências runtime diretas porque o projeto importa/executa ambos diretamente.
11. A chave do check compartilhado deixa de carregar o nome do consumidor MCP e `delete() is False` é falha de cleanup.
12. A lista runtime `ENVS`, sem consumidor e já divergente do `Literal`, será removida; `EnviromentVar` permanece como contrato de tipagem dos helpers.

### Endurecimentos incorporados após revisão independente

13. Metadata e liveness removem `Authorization` antes do middleware OAuth; CORS restrito envolve somente `/mcp`, preservando também o preflight público do SDK.
14. O verifier exige o marcador RFC 9068 de access token, captura numeric dates fora de faixa e limita refresh JWKS por `kid` desconhecido com cache negativo, trava e cooldown.
15. Web, MCP e workers compartilham a tag `APP_IMAGE`; o proxy aplica limites a todo o hostname MCP e remove bearer da location de metadata.
16. O smoke de bootstrap executa o comando Uvicorn `--factory` real, não apenas uma chamada direta da função Python.

### Task 1: Alinhar spec, mapa de arquivos e invariantes existentes — concluída

**Files:**
- Modify: `.ai/brainstorming/spec/2026-08-15-built-in-mcp-server.md`
- Modify: `tests/architecture/test_mcp_runtime.py`
- Modify: `api/settings.py`
- Modify: `docs/reference/estrutura-de-diretorios.md`

- [x] **Step 1: Atualizar a spec com as 12 decisões corretivas**

Corrigir o mapa para incluir `tests/settings.py` e `test_authentications.py`; esclarecer JWT/JWKS, scope global, resource/audience canônicos, CORS, bootstrap ASGI, limites de proxy e URL local com `:8000`.

- [x] **Step 2: Escrever teste arquitetural RED para ordem de `BUSINESS_APPS`**

```python
def test_business_apps_are_alphabetical():
    assert settings.BUSINESS_APPS == sorted(settings.BUSINESS_APPS)
```

Run: `uv run pytest tests/architecture/test_mcp_runtime.py -q`

Expected: FAIL porque `apps.api.metadata` está depois de `apps.organizacoes`.

- [x] **Step 3: Ordenar settings e exemplo documental**

Mover `apps.api.metadata` para antes de `apps.logs` em `api/settings.py` e no trecho correspondente da referência de diretórios.

- [x] **Step 4: Verificar GREEN**

Run: `uv run pytest tests/architecture/test_mcp_runtime.py -q`

### Task 2: Endurecer o health compartilhado — concluída

**Files:**
- Modify: `apps/api/core/tests/test_dependency_health.py`
- Modify: `apps/api/core/dependency_health.py`

- [x] **Step 1: Escrever testes RED para leitura divergente e delete falso**

Cobrir separadamente:

```python
with pytest.raises(CacheRoundTripError):
    modulo.check_cache("default")
```

quando `get()` diverge, e:

```python
with pytest.raises(CacheCleanupError):
    modulo.check_cache("default")
```

quando `delete()` retorna `False`.

Run: `uv run pytest apps/api/core/tests/test_dependency_health.py -q`

Expected: leitura divergente já passa; delete falso falha porque o retorno é ignorado.

- [x] **Step 2: Implementar cleanup explícito e prefixo genérico**

Adicionar `CacheCleanupError`, usar `dependency-health:<uuid>` e converter somente `delete() is False` em falha. Preservar uma falha primária quando cleanup também falhar.

- [x] **Step 3: Verificar GREEN**

Run: `uv run pytest apps/api/core/tests/test_dependency_health.py apps/api/mcp_server/tests/test_server.py -q`

### Task 3: Tornar as dependências e settings MCP explícitas — concluída

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `utils/env.py`
- Modify: `api/settings.py`
- Modify: `.env.example`
- Modify: `tests/architecture/test_mcp_runtime.py`

- [x] **Step 1: Ampliar teste de dependências runtime (RED)**

Exigir `mcp`, `pyjwt`, `starlette` e `uvicorn` em `[project].dependencies`.

Run: `uv run pytest tests/architecture/test_mcp_runtime.py -q`

Expected: FAIL para Starlette/Uvicorn, hoje apenas transitivas.

- [x] **Step 2: Declarar dependências diretas**

Run: `uv add 'starlette>=1.6.0,<2' 'uvicorn>=0.52.3,<1'`

- [x] **Step 3: Remover redundância de env e declarar settings MCP**

Remover `ENVS`, corrigir a docstring de `get_int_from_env`, acrescentar as sete chaves MCP ao `EnviromentVar` e expor listas/strings em `api/settings.py` pelos helpers existentes.

- [x] **Step 4: Documentar defaults seguros em `.env.example`**

Provider/resource/hosts ficam vazios; algoritmo default `RS256`; comentar que HTTP falha fechado e `stdio` não depende dessas variáveis.

- [x] **Step 5: Verificar GREEN**

Run: `uv run pytest tests/architecture/test_mcp_runtime.py utils/tests/test_env.py -q`

### Task 4: Implementar configuração canônica e verificador JWT/JWKS — concluída

**Files:**
- Create: `apps/api/mcp_server/authentications.py`
- Create: `apps/api/mcp_server/tests/test_authentications.py`

- [x] **Step 1: Escrever testes RED da configuração**

Cobrir configuração ausente, algoritmo simétrico, URL inválida, HTTP em produção, audience diferente do resource, host canônico ausente, wildcard/path em host, wildcard/path/origin HTTP público e preservação exata do issuer sem barra adicionada.

Run: `uv run pytest apps/api/mcp_server/tests/test_authentications.py -q`

Expected: import failure porque o módulo não existe.

- [x] **Step 2: Implementar `build_http_auth()` e validadores pequenos**

Produzir `HttpAuthConfiguration`, contendo verifier, `AuthSettings`, hosts e origins validados. A factory ASGI consumirá um único objeto para impedir leituras divergentes das settings.

- [x] **Step 3: Escrever testes RED do token real**

Gerar uma chave RSA efêmera e JWTs reais. Cobrir assinatura/issuer/audience/exp/nbf, audience em lista, `client_id` com fallback `azp`, scope string, subject opcional, algoritmo não permitido e ausência de claims obrigatórias. O único double será o cliente JWKS estático; o decode será real.

- [x] **Step 4: Implementar `OidcJwtTokenVerifier`**

Executar busca/decode bloqueantes via thread, nunca logar token/claims, retornar `None` em falha conhecida e preencher `AccessToken.resource` com o resource canônico.

- [x] **Step 5: Verificar GREEN e lint**

Run: `uv run pytest apps/api/mcp_server/tests/test_authentications.py -q`

Run: `uv run ruff check apps/api/mcp_server/authentications.py apps/api/mcp_server/tests/test_authentications.py utils/env.py api/settings.py`

### Task 5: Implementar factory ASGI com bootstrap correto — concluída

**Files:**
- Create: `apps/api/mcp_server/asgi.py`
- Create: `apps/api/mcp_server/tests/test_http.py`
- Modify: `apps/api/mcp_server/tests/settings.py`

- [x] **Step 1: Escrever teste RED do bootstrap por factory**

Subprocesso executa somente a factory Uvicorn com `DJANGO_SETTINGS_MODULE` ausente e configuração MCP completa; deve chegar ao startup sem `ImproperlyConfigured: settings are not configured`.

- [x] **Step 2: Escrever testes RED do app in-process**

Cobrir par incompleto verifier/auth, Host com bearer válido, Origin, liveness, metadata, issuer/resource exatos, CORS preflight com `Mcp-Method` e `Mcp-Name`, e `WWW-Authenticate` exposto.

- [x] **Step 3: Implementar `create_asgi_app()`**

Primeira instrução funcional: `setup_django()`. Só depois importar settings/auth/server. Criar Streamable HTTP stateless/JSON, rota `/live`, transport security e CORS explícito de `/mcp`.

- [x] **Step 4: Verificar GREEN**

Run: `uv run pytest apps/api/mcp_server/tests/test_http.py apps/api/mcp_server/tests/test_server.py -q`

### Task 6: Provar autenticação, autorização e paridade nos transportes — concluída

**Files:**
- Modify: `apps/api/mcp_server/tests/test_http.py`
- Modify: `apps/api/mcp_server/tests/test_stdio.py`

- [x] **Step 1: Escrever smoke HTTP de rede real**

Subir Uvicorn em porta efêmera, chamar `/mcp` pelo client oficial e verificar descoberta/chamada `health` com bearer válido.

- [x] **Step 2: Cobrir falhas de segurança pelo protocolo real**

Asserts distintos para 401 sem/invalid token, 403 sem `health:read`, 421 Host com token válido, 403 Origin inválida e metadata pública. Verificar `WWW-Authenticate` sem exigir `scope=` enquanto o SDK 2.x não o emitir.

- [x] **Step 3: Fortalecer stdio**

Capturar `stderr`, confirmar que logs não contaminam `stdout`, e comparar nome/schema/resultado básico com HTTP sem duplicar a matriz de health.

- [x] **Step 4: Verificar GREEN**

Run: `uv run pytest apps/api/mcp_server/tests apps/api/core/tests/test_dependency_health.py -q`

### Task 7: Completar serviço, proxy e smoke da imagem — concluída

**Files:**
- Modify: `Dockerfile`
- Modify: `docker-compose.yml`
- Modify: `docker/nginx/nginx.conf`
- Modify: `docker/nginx/sites/production/default.conf`

- [x] **Step 1: Declarar serviço `mcp` no profile explícito**

Mesma imagem/env/dependências do web, comando Uvicorn factory, porta interna 8001, healthcheck `/live`, sem `ports` públicos.

- [x] **Step 2: Configurar proxy MCP por hostname**

Usar DNS tardio; preservar `Authorization`, `Host` e forwarded headers; encaminhar `/mcp` e `/.well-known/oauth-protected-resource`; não publicar `/live`.

- [x] **Step 3: Adicionar limites operacionais**

Limitar corpo a 1 MiB, conexões concorrentes por IP e taxa com burst conservador no hostname MCP, sem alterar o comportamento da API REST.

- [x] **Step 4: Validar infraestrutura**

Run: `docker compose --profile mcp config --quiet`

Run: `docker compose --profile mcp build mcp`

Run: `docker compose --profile mcp run --rm --no-deps mcp python -c "import mcp, jwt, starlette, uvicorn; from apps.api.mcp_server.asgi import create_asgi_app; create_asgi_app(); print('mcp-runtime-ok')"`

Run: `make nginx-test`

### Task 8: Corrigir documentação operacional — concluída

**Files:**
- Create: `docs/how-to/usar-servidor-mcp.md`
- Modify: `README.md`
- Modify: `docs/reference/estrutura-de-diretorios.md`
- Modify: `docs/how-to/proxy-nginx.md`
- Modify: `mkdocs.yml`

- [x] **Step 1: Documentar stdio e HTTP sem ambiguidades**

Usar `http://mcp.localhost:8000/mcp` no ambiente local; explicar JWT access token, audience/resource exatos, scope global e que token opaco exige outro verifier/introspection fora do escopo.

- [x] **Step 2: Documentar obtenção de token de forma provider-neutral**

Fornecer fluxo client credentials genérico com placeholders para token endpoint, client id/secret, audience/resource e `health:read`, sem prometer uma URL OIDC universal.

- [x] **Step 3: Documentar CORS, metadata e limites**

Distinguir allowlist de `/mcp`, CORS público da metadata RFC 9728, liveness privada e health autenticado. Registrar limitação do `scope=` no desafio 403 do SDK 2.x.

- [x] **Step 4: Validar docs**

Run: `uv run mkdocs build --strict`

### Task 9: Verificação final e auditoria de critérios

**Files:**
- Verify only: all changed files

- [x] **Step 1: Testes focados**

Run: `uv run pytest apps/api/mcp_server/tests apps/api/core/tests/test_dependency_health.py tests/architecture/test_mcp_runtime.py -q`

- [x] **Step 2: Gates estáticos e Django**

Run: `uv run ruff check .`

Run: `uv run python manage.py check`

Run: `uv run python manage.py makemigrations --check --dry-run`

- [ ] **Step 3: Suíte completa com infraestrutura**

Run: `make up && uv run pytest -q`

Se PostgreSQL/Redis não puderem ser iniciados no ambiente, registrar a limitação com a evidência do erro e não afirmar que a suíte global passou.

Execução adaptada em 2026-08-16: foram usados PostgreSQL e Redis já existentes, conforme solicitado. O HIBP foi desligado no processo de teste para não depender da API externa, e a tentativa final usou `TEST_DATABASE_NAME=base_test_mcp_20260816` para evitar colisão com outros agentes. A coleta parou em `apps/api/autenticacao/tests/tokens/test_account_deletion.py`, trabalho concorrente fora deste plano, por ausência de `revoke_all_user_credentials`.

Reexecução final: 1.141 testes passaram, 7 falharam e 1 foi desmarcado na execução com banco exclusivo. As 7 falhas pertencem a mudanças concorrentes em `apps/api/autenticacao` e `internal_frameworks/permission_cache`; o teste desmarcado apenas exige o nome default `base_test` e passou isoladamente sem override. O teste MCP de `nbf` que dependia do tempo de coleta foi corrigido e toda a seleção MCP passou novamente (55 testes).

- [x] **Step 4: Revisar diff e critérios da spec**

Run: `git diff --check && git status --short && git diff --stat`

Repassar cada critério de aceitação da spec e listar qualquer item ainda pendente antes do handoff.

Auditoria concluída: todos os critérios funcionais e operacionais MCP estão implementados e cobertos. O único gate pendente é tornar verde a suíte global depois que as mudanças concorrentes de autenticação/cache forem estabilizadas; não há item de implementação MCP pendente.
