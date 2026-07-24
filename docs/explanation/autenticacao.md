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
