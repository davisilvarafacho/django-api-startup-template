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

## Modelo de token: swappable, não proxy

O Knox aceita um modelo de token próprio via `settings.KNOX_TOKEN_MODEL =
"autenticacao.AuthToken"` (`Meta.swappable`), do mesmo jeito que o Django
permite trocar o model de usuário. Isso evita a limitação de proxy model (sem
colunas físicas extras) que motivava guardar tipo/organização/scopes só no
`TokenMetaData`.

Nunca importe `knox.models.AuthToken` diretamente — sempre
`knox.models.get_token_model()`. `AuthToken` tem `user`/`created` como
propriedades Python de compatibilidade sobre os campos reais `responsavel`/
`created_at`; código novo usa os nomes reais.

`TokenMetaData` continua existindo 1:1 com o token, mas só para dados
operacionais (dispositivo, geolocalização, risco, `reauthenticated_at`)  —
tipo, organização e scopes são colunas do próprio `AuthToken`.

## Tipos de token

A classificação vive em `AuthToken.type`:

- `1` (`TokenType.TOKEN`) — sessão de login.
- `2` (`TokenType.RESET_PASSWORD`) — reset de senha; não autentica endpoints da API.
- `999` (`TokenType.API_KEY`) — API key de organização; ver seção própria.

Um token inválido, expirado, revogado ou suspenso nunca é apagado do banco:
`TypedTokenAuthentication` desliga a limpeza automática do Knox
(`_cleanup_token`) e transforma cada estado num `APIError` tipado
(`auth.expired_token`, `auth.revoked_token`, `auth.api_key_suspended`,
`auth.responsible_inactive`), preservando o registro para auditoria.

## Scoped API Tokens

Escopos vivem em `AuthToken.scopes` como lista de strings. Eles só limitam
tokens do tipo `999` (`API_KEY`); tokens de sessão continuam dependendo das
permissions normais do Django/guardian/rules.

`TokenScopePermission` roda globalmente antes das permissões de modelo. Se a view
não exigir nenhum scope, uma API key é recusada por padrão
(`auth.insufficient_scope`); sessões pessoais não são limitadas por scopes.

### A linguagem pública: `resource:action`

Scopes e permissions humanas falam a mesma língua estável: `resource:action`
(ex.: `teams:read`, `invitations:accept`). Por baixo, isso é traduzido para os
codenames internos do Django (`app_label.codename`) pelo registry em
`apps.api.core.scope_registry`; clientes e documentação nunca veem o codename.

- **Actions CRUD**: `read`, `create`, `update`, `delete` — mapeadas para
  `view_/add_/change_/delete_<model>`. Actions customizadas também são
  permitidas (ex.: `invitations:accept`).
- **Wildcards**: `resource:*` (qualquer action daquele recurso) e `*` (qualquer
  recurso e action).

### Declarando o recurso de um model

```python
class Time(BaseGlobal):
    api_scope_resource = "teams"
```

`None` (o default de `BaseGlobal`) significa que o model não é exposto pelo
registry. Um ViewSet pode sobrescrever o recurso público quando ele diverge do
model consultado (ex.: sem `queryset` estático):

```python
class OrganizacaoViewSet(ScopeResourceMixin, ...):
    scope_resource = "organizations"
```

`UtilsViewSetMixin`/`BaseModelViewSet` já incluem esse mixin; ViewSets que não
herdam dele (como os de `apps.organizacoes`) usam `ScopeResourceMixin`
diretamente.

### Actions customizadas

`get_required_token_scopes()` deriva o scope CRUD automaticamente a partir da
action padrão (`list`/`retrieve`/`create`/`update`/`partial_update`/`destroy`).
Para uma `@action` customizada, declare o scope explicitamente:

```python
@action(detail=False, methods=["post"], url_path="aceitar")
@require_token_scopes("invitations:accept")
def aceitar(self, request):
    ...
```

O model registra a tradução da action para a permission Django usada na
delegação:

```python
api_scope_custom_actions = {"accept": "can_accept_convite"}
```

Declarar apenas o decorator não torna a action delegável.

### Compatibilidade

O atributo estático legado ainda funciona quando a view não define
`get_required_token_scopes()` (nem herda o mixin):

```python
required_token_scopes = ["org:read"]
# ou
required_token_scopes = {"GET": ["org:read"], "POST": ["org:write"]}
```

### Delegação: um usuário só concede o que ele mesmo pode fazer

`apps.api.autenticacao.scope_delegation.validate_scope_delegation(user, scopes)`
impede que uma API key receba mais poder do que o ator que a cria ou altera:
cada scope concreto exige a permission Django equivalente do concedente
(`user.has_perm(...)`); o wildcard global `*` exige superuser ou a permission
especial `autenticacao.grant_unrestricted_apikey`. Falhas geram
`APIError(auth.scope_not_delegable)`.

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

## Sessões (`/auth/sessions/`)

`SessionViewSet` só lista/gerencia tokens do tipo sessão do próprio usuário
autenticado — nunca API keys ou tokens de reset. `PATCH` só aceita
`device_name`; `DELETE` e `/auth/logout/`/`/auth/logout_all/` revogam
logicamente (`revoked_at`/`revoked_by`), nunca apagam. Todas essas rotas
recusam API keys de saída (`session_only = True` em
`SessionScopedViewMixin`).

`services.issue_token()` é o único ponto de entrada para criar um `AuthToken`:
ele cria o token e o `TokenMetaData` na mesma transação, então uma falha na
segunda escrita também desfaz a primeira.

## Autenticação recente (step-up)

Ações sensíveis (criar/renomear uma API key, por exemplo) exigem que a sessão
tenha confirmado a senha há pouco tempo:

```python
@require_recent_auth(max_age=300)
def create(self, request, *args, **kwargs):
    ...
```

`@require_recent_auth` só grava a configuração no método/action/classe
decorada; quem de fato valida é `RecentAuthenticationPermission`
(`apps.api.autenticacao.recent_auth`), incluída nas permissions da view. Sem o
decorator em lugar nenhum ela é um no-op. `POST /auth/reauthenticate/`
confirma a senha (e MFA, quando o usuário tiver — a integração de MFA em si
ainda não existe: `user_has_mfa_enabled()`/`verify_mfa_code()` são stubs
seguros) e atualiza `TokenMetaData.reauthenticated_at` da sessão atual. Uma
API key nunca satisfaz o requisito — só sessão pode reautenticar.

Login e reautenticação têm throttles próprios, além dos limites globais:
`auth_login` (`10/min` por origem anônima) e `auth_reauthenticate` (`5/min`
por usuário).

## API keys (`/auth/api_keys/`)

Uma API key é um `AuthToken` do tipo `999`, presa a exatamente uma
organização (`organization`, obrigatório para esse tipo via `CheckConstraint`)
e identificada por UUID nas URLs. `APIKeyViewSet` expõe:

| Rota | Efeito |
| --- | --- |
| `POST /auth/api_keys/` | Cria; exige autenticação recente, vínculo ativo do `responsavel` na organização e `validate_scope_delegation()` |
| `PATCH /auth/api_keys/{uuid}/` | Renomeia/realoca responsável/scopes; exige autenticação recente |
| `DELETE /auth/api_keys/{uuid}/` | Revoga logicamente (nunca apaga) |
| `POST .../rotate/` | Emite uma credencial nova e revoga a atual, atomicamente (`select_for_update`); segredo antigo já não funciona na resposta |
| `POST .../suspend/` | Suspende (reversível), com motivo opcional |
| `POST .../resume/` | Retoma; recusa se o `responsavel` perdeu o vínculo ou está inativo, ou se a key já foi revogada |

O plain token só aparece na resposta de criação e de rotação — nunca em
listagem, detalhe ou PATCH.

### Tenancy de API key

Uma API key nunca usa `X-Organization` para escolher tenant: o tenant vem da
própria credencial (`resolve_token_organization` em
`apps.organizacoes.middleware`). Um header divergente é rejeitado com
`organizations.tenant_mismatch` (409). `TenantPermission` também não usa o
`Vinculo` do responsável para decidir autorização de API key (isso é só por
scope), mas exige que ele exista e esteja ativo.
Mesmo em rotas normalmente sem tenant, a key continua presa à organização da
credencial: a listagem de organizações devolve apenas essa organização e um
convite de outro tenant é recusado.

### Suspensão automática (fail-closed)

Se o `responsavel` de uma API key fica inativo ou perde o vínculo com a
organização, `ensure_api_key_still_valid()` materializa isso como suspensão
(`suspended_at`/`suspension_reason`) na primeira request tenant-scoped
seguinte, em vez de só recusar a request sem deixar rastro — a trilha de
auditoria mostra a suspensão, e `resume_api_key()` recusa reativar enquanto o
vínculo não voltar.

### Permissions administrativas

`APIKeyPermissions` usa codenames próprios do model
(`autenticacao.view_apikey`/`add_apikey`/`change_apikey`/`delete_apikey`/
`rotate_apikey`), distintos dos codenames automáticos do model subjacente
(`authtoken`): ser responsável ou criador de uma key não concede autoridade
administrativa sobre ela.

### Auditoria

`apps.api.autenticacao.audit.emit_api_key_event()` centraliza a emissão dos
eventos `create`, `rotate`, `suspend`, `resume`, `revoke`,
`responsible_changed` e `scopes_changed` — sempre chamada pelos services
depois da própria transação, nunca duplicada nas views. O payload inclui UUID
da key, responsável, criador, organização, ator e request ID; nunca inclui
plain token, digest ou `token_key`.

## Limpeza de tokens expirados

`cleanup_expired_tokens()` centraliza uma política idempotente e é a única
implementação usada tanto pela task Celery quanto pelo management command:

| Tipo | Critério de remoção |
| --- | --- |
| `PRE_AUTH` e `RESET_PASSWORD` | Imediatamente após `expiry`. |
| `TOKEN` | Depois de expirar e transcorrer a retenção de sessões. |
| `API_KEY` | Nunca é selecionada automaticamente. |
| Sem `expiry` | Nunca é selecionado automaticamente. |

As exclusões ocorrem em lotes curtos, com lock e transação por lote; metadados
dependentes são removidos por cascade. Os valores atuais de operação são
`AUTH_TOKEN_SESSION_RETENTION_DAYS = 90` e
`AUTH_TOKEN_CLEANUP_BATCH_SIZE = 500`. Eles estão em `api/settings.py`; não há
variável de ambiente correspondente, portanto `.env.example` não precisa nem
deve declarar configuração inativa. Em uma configuração de deployment, altere
os settings efetivamente carregados em vez de criar uma variável sem consumo.

Para inspecionar ou executar manualmente:

```bash
uv run python manage.py cleanup_expired_auth_tokens --dry-run
uv run python manage.py cleanup_expired_auth_tokens --batch-size 100
uv run python manage.py cleanup_expired_auth_tokens --session-retention-days 30
```

`--dry-run` mostra contagens por tipo e não remove dados. `--batch-size` precisa
ser maior que zero; `--session-retention-days` não pode ser negativo. Execute o
command primeiro em modo dry-run em operações de manutenção, acompanhe as
contagens e evite registrar ou copiar tokens puros em shell history, logs ou
chamados de suporte.

A task `autenticacao.cleanup_expired_tokens` chama o mesmo serviço, usa retry
com backoff somente para `OperationalError` e limita-se a três tentativas. Ela
registra apenas duração e contagens por tipo, nunca segredos de token. O
`CELERY_BEAT_SCHEDULE` a agenda diariamente às `00:00`; como
`CELERY_TIMEZONE = TIME_ZONE` e `TIME_ZONE = "America/Sao_Paulo"`, esse horário
é meia-noite em São Paulo. O scheduler configurado é o
`django_celery_beat.schedulers:DatabaseScheduler`; não é necessário cadastrar a
rotina manualmente no Django Admin.


## MFA opt-in

O usuário pode cadastrar fatores `totp`, `email` e `sms`; um fator de cada tipo
fica ativo somente depois de confirmado. Setup, confirmação, remoção, geração
de recovery codes e gestão de dispositivos confiáveis exigem uma sessão
`Bearer` reautenticada nos últimos cinco minutos. Secrets TOTP e telefones são
cifrados, OTPs são persistidos apenas como HMAC e recovery/trusted-device tokens
nunca são persistidos em texto puro.

Um login com fatores ativos responde `202` com `pre_auth_token` e `methods`.
Envie esse token como `Authorization: PreAuth <token>` exclusivamente para
`/auth/mfa/challenge/start/` e `/auth/mfa/challenge/verify/`; ele expira em
cinco minutos, não autoriza endpoints normais e é removido ao concluir o MFA.
Dispositivos confiáveis duram 30 dias, rodam o segredo a cada uso e podem ser
revogados em `/auth/trusted-devices/`.

Para SMS, configure `MFA_SMS_ENABLED=true` e um `MFA_SMS_BACKEND` de produção.
O deploy falha se Console/InMemory estiverem habilitados fora de DEBUG. Em perda
total de fatores, um operador com `usuarios.can_reset_mfa_usuario` usa
`POST /auth/mfa/admin-reset/`, reautenticado e com justificativa; a operação
revoga fatores, recovery codes, sessões e dispositivos confiáveis, preservando
a senha.
