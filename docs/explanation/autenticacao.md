# Autenticação passthrough

A autenticação por token acontece num **middleware**, não nas classes de
autenticação do DRF. O DRF apenas reaproveita o resultado.

## Por quê

A autenticação padrão do DRF roda dentro do `dispatch()` da view, ou seja,
**depois** de toda a cadeia de middlewares. Qualquer middleware que precise do
usuário — auditlog, PostHog, tenancy/RLS — enxergaria `AnonymousUser`.

Resolvendo o token logo no começo da cadeia, todo middleware interno já recebe
`request.user` preenchido, e o token é validado uma vez por request em vez de
duas.

## As peças

| Peça | Arquivo | Papel |
| --- | --- | --- |
| `RouteRegistry` | `apps/api/core/routes_registry.py` | Coleta, no boot, os prefixos declarados por cada app |
| `PUBLIC_ROUTES` | `<app>/public_routes.py` | Rotas que dispensam token |
| `AuthenticationMiddleware` | `apps/api/autenticacao/middleware.py` | Valida o token e resolve o usuário |
| `TypedTokenAuthentication` | `apps/api/autenticacao/authentications.py` | Valida Knox e bloqueia tipos de token que não podem acessar a API |
| `PassthroughAuthentication` | `apps/api/autenticacao/authentications.py` | Entrega ao DRF o que o middleware resolveu |

## O fluxo

1. `CoreConfig.ready()` chama `routes_registry.discover()`, que varre os
   `BUSINESS_APPS` atrás de um módulo `public_routes` com a lista
   `PUBLIC_ROUTES`. Defaults, sem descoberta: `/admin/` e `/health/`.
2. O `AuthenticationMiddleware` decide, para cada request:
   - rota de debug (`/silk/`, `/api/docs/`, …) **e** `DEBUG=True` → segue sem token;
   - `routes_registry.matches(path)` → segue sem token;
   - caso contrário, tenta os autenticadores em ordem (`TypedTokenAuthentication`,
     depois `QueryParamTokenAuthentication`). Token inválido, ausente ou de tipo
     não aceito para API encerra a request com **401** — ela nunca chega ao DRF.
3. Em caso de sucesso o middleware grava `request.user`, `request.auth` e o
   marcador `request.autenticacao_resolvida`.
4. A `PassthroughAuthentication`, única em `DEFAULT_AUTHENTICATION_CLASSES`, lê
   o marcador e devolve `(user, token)` ao DRF — ou `None` em rota pública.

## Tipos de token

O Knox continua sendo o mecanismo de token. A classificação operacional fica em
`TokenMetaData.type`, um metadado 1:1 do token, porque proxy models do Django não
podem adicionar colunas físicas.

Valores públicos:

- `1` — token de sessão/API normal.
- `2` — reset de senha; não autentica endpoints da API.
- `999` — API key; autentica endpoints da API e servirá de base para scoped API
  tokens.

## Scoped API Tokens

Escopos vivem em `TokenMetaData.scopes` como lista de strings. Eles só limitam
tokens do tipo `999` (`API_KEY`); tokens de sessão continuam dependendo das
permissions normais do Django/guardian/rules.

Uma view pode declarar escopos de duas formas:

```python
required_token_scopes = ["org:read"]
```

ou por método/action:

```python
required_token_scopes = {
    "GET": ["org:read"],
    "POST": ["org:write"],
    "create": ["org:write"],
}
```

`TokenScopePermission` roda globalmente antes das permissões de modelo. Se a view
não declarar escopos, ela não exige nada extra do token.

## Cache de fatos de autorização

O cache compartilhado acelera quatro famílias de fatos sem mudar as APIs de
autorização existentes:

- permissões globais do Django, tanto diretas quanto herdadas de grupos;
- vínculo e papel do usuário em cada organização;
- permissões por objeto do Guardian, diretas e por grupo;
- fatos estáveis usados por predicates do Rules, como o papel no tenant.

Predicates arbitrários do Rules continuam sendo avaliados normalmente. Os
backends cacheados preservam `user.has_perm()`, `has_perms()` e
`has_module_perms()` em requests DRF, no admin, em tasks Celery, em management
commands e em chamadas diretas de domínio.

### Tenant na request

Depois de `TenantPermission`, a interface canônica é a dataclass imutável
`request.tenant`, com os campos:

- `organization_id`;
- `organization_slug`;
- `membership_id`;
- `role`;
- o helper `has_minimum_role(...)`.

Views devem filtrar e salvar pelo `organization_id`; serializers devem comparar
`role`; e o contexto de RLS recebe exatamente
`request.tenant.organization_id`. Fora de uma request, use os resolvedores
diretamente e abra o contexto RLS apropriado quando consultar modelos isolados.

### Validade e falhas

Snapshots expiram em 30 minutos. Uma mutação confirmada incrementa os epochs
correspondentes imediatamente após o commit, tornando as chaves antigas
inalcançáveis sem varrer o Redis. O TTL é uma rede de segurança, não o caminho
normal de invalidação.

O Redis nunca é fonte de verdade: erro de leitura ou escrita faz o resolvedor
consultar o banco e registrar o fallback; erro do banco continua sendo
propagado, sem conceder acesso com um valor antigo. Se o Redis estiver fora
justamente quando uma invalidação pós-commit ocorrer, um snapshot anterior pode
voltar a ser observado quando ele retornar. A janela de indisponibilidade aceita
é o TTL restante, limitada a 30 minutos.

### Mutações em massa

Não use `QuerySet.update()`, `bulk_create()` ou `bulk_update()` diretamente em
dados que alteram autorização: essas APIs não emitem todos os signals exigidos
para invalidar os epochs. Os wrappers oficiais de
`common.permission_cache.mutations` são:

- tenant: `bulk_create_memberships`, `bulk_update_memberships` e
  `update_memberships`;
- Guardian: `guardian_bulk_assign`, `guardian_assign_to_many`,
  `guardian_bulk_remove` e `guardian_remove_from_many`;
- Django: `bulk_create_permissions`, `bulk_update_permissions` e
  `update_user_authorization_state`.

SQL cru e escritores externos também precisam chamar uma invalidação explícita.

### Cachalot e operação

O Cachalot continua sendo um cache de queries independente. A recomposição de
um fato de autorização o desabilita apenas durante a consulta que precisa ler a
fonte de verdade. O alias `permissions` usa o database Redis `/4`, enquanto o
alias `cachalot` usa `/3`; invalidar permissões não limpa nem interfere nas
chaves do Cachalot.

Em um incidente, defina `AUTHORIZATION_CACHE_ENABLED=false` para acionar o kill
switch: os mesmos backends permanecem instalados, mas passam a consultar o banco
sem ler ou gravar snapshots. Para invalidar todos os snapshots logicamente em
O(1), execute:

```bash
uv run python manage.py invalidate_permission_cache
```

O comando incrementa o epoch global e não usa `FLUSHDB`, varredura de chaves nem
`delete_pattern`.

## Declarando uma rota pública

```python
# apps/meu_app/public_routes.py
PUBLIC_ROUTES = [
    "/v1/meu-endpoint/publico/",
]
```

A comparação é `startswith`. **Declare prefixos granulares**: `/auth/` tornaria
pública inclusive `/auth/logout/`, que exige token.

## Cuidados

- O mecanismo depende do middleware estar na cadeia. Se ele sumir, a
  `PassthroughAuthentication` levanta `RuntimeError` em vez de liberar a request
  silenciosamente.
- O `AuthenticationMiddleware` precisa vir **depois** do `ThreadLocalMiddleware`
  e **antes** de auditlog, PostHog e `OrganizacaoMiddleware`.
- Uma view com `permission_classes = [AllowAny]` cujo caminho **não** esteja em
  `PUBLIC_ROUTES` ainda recebe 401 do middleware. O `AllowAny` não salva.
- O marcador vive como atributo da `HttpRequest`, e não em threadlocal:
  `threadlocals.set_request_variable` cai num fallback global da thread quando
  não há request corrente, o que vazaria a decisão de uma request para a
  seguinte no mesmo worker.
