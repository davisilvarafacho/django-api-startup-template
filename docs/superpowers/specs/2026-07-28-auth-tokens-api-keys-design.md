# Tokens, sessões e API keys

**Status:** aprovado em 2026-07-28.

## Objetivo

Substituir `knox.AuthToken` por um modelo próprio configurado por
`KNOX_TOKEN_MODEL`, preservando o contrato de autenticação do Knox e
centralizando sessões, reset de senha e API keys num único modelo.

O login, logout e gerenciamento de credenciais serão reorganizados em
interfaces explícitas. `TokenMetaData` continuará existindo como relação 1:1
para informações operacionais do uso da credencial.

## Escopo

- Modelo de token swappable compatível com django-rest-knox.
- Tipos numéricos:
  - `1`: sessão;
  - `2`: reset de senha;
  - `999`: API key.
- Sessões e dispositivos.
- API keys independentes, vinculadas a uma organização.
- Scopes públicos no formato `resource:action`.
- Login, logout, reautenticação recente e gerenciamento de credenciais.
- Refatoração dos campos comuns da `Base`.
- Auditoria, erros estruturados e contrato OpenAPI.

MFA, cache de permissions, field-level permissions e ciclo completo de conta
terão specs próprias. Este desenho apenas define os pontos de integração com
MFA e com o sistema de permissions.

## Decisões principais

- Haverá um único modelo de token. Não haverá um segundo modelo lógico
  `APIKey`.
- A rotação cria uma nova linha de token e revoga a anterior.
- O segredo anterior deixa de funcionar imediatamente.
- O plain token aparece somente na criação ou rotação.
- `TokenMetaData` aponta para `settings.KNOX_TOKEN_MODEL`.
- `type` e `scopes` pertencem ao token, não ao metadata.
- API keys pertencem a exatamente uma organização.
- API keys têm permissions próprias e não herdam permissions pessoais do
  responsável.
- O responsável fornece accountability humana, não autoridade à credencial.
- O tenant de uma API key sempre vem da própria credencial.
- API keys não podem administrar outras credenciais.
- Identificadores públicos usam UUID; `digest` e `token_key` são internos.
- Não serão mantidas migrations incrementais nesta fase. O projeto fará um
  reset completo das migrations antes do lançamento.

## Campos comuns da `Base`

O modelo abstrato `Base` deixará de usar `owner` e os quatro campos separados
de data/hora.

Campos comuns:

- `created_by`: FK anulável e protegida para o usuário que criou o registro;
- `created_at`: `DateTimeField(auto_now_add=True)`;
- `last_modified_at`: `DateTimeField(auto_now=True)`.

Não haverá `last_modified_by`.

Essa mudança é transversal e será tratada como pré-requisito. Nenhuma migration
de compatibilidade será criada; o reset completo das migrations ocorrerá antes
do lançamento. Os três campos viverão num mixin abstrato reutilizado por
`Base`; assim modelos de infraestrutura, como o token, poderão adotar o mesmo
padrão sem herdar os demais comportamentos de domínio de `Base`.

## Modelo de token

O novo modelo será uma implementação concreta compatível com a interface do
Knox, selecionada por `KNOX_TOKEN_MODEL` e declarada como swappable. Ele deve
satisfazer o contrato usado pelo Knox:

- `digest` como chave primária;
- `token_key`;
- `responsavel` como FK física com o reverse name `auth_token_set`;
- `created_at` e expiração;
- manager cujo `create()` retorne `(instance, plain_token)`;
- compatibilidade com `get_token_model()`, `TokenAuthentication` e as views
  substitutas de login/logout.

Para compatibilidade com o código do Knox, o modelo exporá aliases internos
`user` e `created`, resolvidos respectivamente para `responsavel` e
`created_at`. O manager aceitará o argumento Knox `user=`, mas persistirá a FK
em `responsavel`. Esses aliases não serão campos públicos da API.

Imports diretos de `knox.AuthToken` serão removidos. Todo acesso usará
`get_token_model()` ou `settings.KNOX_TOKEN_MODEL`.

### Campos adicionais

- `uuid`: identificador público único;
- `type`: tipo numérico da credencial;
- `name`: nome administrativo, obrigatório para API keys;
- `organization`: obrigatório apenas para API keys;
- `created_by`: criador original, fornecido pelo mixin comum;
- `responsavel`: usuário atualmente responsável e usuário autenticável para o
  contrato interno do Knox;
- `scopes`: lista de scopes concedidos à API key;
- `revoked_at` e `revoked_by`;
- `suspended_at`, `suspended_by` e `suspension_reason`;
- `replaced_by`: referência à credencial criada por uma rotação.

O contrato interno exigido pelo Knox continuará expondo o usuário autenticável
esperado por `request.user`. Para uma API key, esse usuário corresponde ao
`responsavel`; a autorização da key continuará sendo determinada por seus
próprios scopes.

### Invariantes

Para `type=999`:

- `organization`, `name`, `created_by` e `responsavel` são obrigatórios;
- o responsável deve ser um membro ativo da organização;
- scopes devem existir no registry;
- o tenant não pode ser sobrescrito por header;
- a key não autentica se estiver expirada, suspensa ou revogada;
- a key fica suspensa se o responsável ficar inativo ou perder o vínculo com
  a organização;
- a retomada exige um responsável válido.

Para sessão e reset:

- `organization` e scopes de API key não são permitidos;
- reset de senha nunca autentica endpoints comuns;
- sessões continuam usando o usuário autenticado como responsável.

As invariantes serão aplicadas na camada de modelo/serviço e por constraints
de banco quando puderem ser expressas localmente.

O autenticador próprio substituirá o comportamento de limpeza do Knox que
apaga tokens expirados. API keys expiradas, suspensas, revogadas ou substituídas
serão recusadas, mas permanecerão no banco para auditoria. Limpeza física de
sessões expiradas poderá ocorrer por política operacional separada.

## `TokenMetaData`

`TokenMetaData` continuará 1:1 com o token e guardará:

- identificação de dispositivo;
- sistema operacional e cliente;
- IP e localização aproximada;
- primeiro e último uso;
- contador de uso;
- sinais de risco;
- versão do app e token de push;
- metadados adicionais.

O relacionamento usará `settings.KNOX_TOKEN_MODEL`. `type` e `scopes` serão
removidos do metadata para evitar duas fontes de verdade.

Metadata e token serão criados na mesma transação.

## Rotação

A rotação não altera o `digest` da linha existente, pois ele é a chave
primária do Knox.

Dentro de `transaction.atomic()`:

1. carregar e bloquear a credencial atual com `select_for_update()`;
2. validar estado, organização e permission;
3. criar uma nova linha pelo manager do token;
4. copiar nome, organização, criador, responsável, scopes e expiração;
5. criar o novo `TokenMetaData`;
6. marcar a anterior como revogada e preencher `replaced_by`;
7. retornar o novo plain token uma única vez.

O segredo antigo perde validade imediatamente. Registros revogados não são
apagados, preservando o histórico da credencial.

## Suspensão e revogação

Suspensão é reversível:

```text
ativa <-> suspensa
```

Pode ser manual ou automática. Retomar exige que organização e responsável
estejam válidos.

Revogação é permanente:

```text
ativa/suspensa -> revogada
```

`DELETE` faz revogação lógica. O registro permanece para auditoria e não pode
ser reativado.

## Organização e RLS

- Sessões humanas continuam selecionando tenant via `X-Organization`.
- API keys derivam o tenant do próprio token.
- API keys não precisam enviar `X-Organization`.
- Um header conflitante é rejeitado com `organizations.tenant_mismatch`.
- Uma API key nunca pode operar em outra organização.
- O contexto RLS deve estar aplicado antes da autorização do endpoint.

### Pipeline de autorização

Para sessões humanas:

1. autenticar a sessão;
2. resolver organização pelo header;
3. aplicar RLS;
4. validar permissions Django/Guardian/rules.

Para API keys:

1. autenticar a key e validar seu estado;
2. resolver a organização pelo próprio token;
3. aplicar RLS;
4. validar os scopes da key.

As permissions pessoais do `responsavel` são ignoradas no segundo pipeline.
Endpoints administrativos de credenciais recusam API keys antes da avaliação
de scopes.

## Permissions humanas

Gerenciamento de API keys usa permissions explícitas do Django:

- `view_apikey`;
- `add_apikey`;
- `change_apikey`;
- `delete_apikey`;
- `rotate_apikey`.

Ser criador ou responsável não concede permission automaticamente.

## Scopes

Scopes são a interface pública de autorização para API keys:

```text
organizations:read
organizations:create
organizations:update
organizations:delete
organizations:*
*
```

Recursos públicos são estáveis e desacoplados de nomes internos de apps e
models.

### Resolução automática

Models poderão declarar um recurso padrão:

```python
api_scope_resource = "users"
```

ViewSets herdam esse recurso, mas podem sobrescrevê-lo:

```python
scope_resource = "profile"
```

Isso permite que um mesmo model apareça como `users:*` numa API administrativa
e `profile:*` no self-service.

O `BaseModelViewSet` mapeia:

- `list` e `retrieve` para `resource:read`;
- `create` para `resource:create`;
- `update` e `partial_update` para `resource:update`;
- `destroy` para `resource:delete`.

Actions personalizadas declaram scopes próprios, como `reports:export` e
`payments:refund`. Cada action também declara no model seu codename Django
correspondente, por exemplo:

```python
api_scope_custom_actions = {"accept": "can_accept_convite"}
```

Uma action sem esse mapeamento não é delegável.

### Registry e linguagem unificada

Um registry central:

- valida recursos, ações e curingas;
- impede recursos duplicados;
- expõe scopes no OpenAPI;
- oferece constantes para evitar strings dispersas;
- fornece system checks.

`resource:action` também será a linguagem mostrada para permissions humanas.
Internamente, o registry traduz para os codenames Django:

```text
users:read   -> usuarios.view_usuario
users:create -> usuarios.add_usuario
users:update -> usuarios.change_usuario
users:delete -> usuarios.delete_usuario
```

Os codenames Django não serão substituídos, preservando compatibilidade com
Django, Guardian, admin e bibliotecas externas.

### Delegação de scopes

Conceder scopes não pode elevar os privilégios do concedente:

- cada scope exige a permission Django equivalente de quem cria ou altera a
  key;
- `resource:*` exige todas as permissions mapeadas para aquele recurso;
- `*` exige superusuário ou a permission especial
  `grant_unrestricted_apikey`;
- o registry faz a tradução e a validação da delegação;
- depois de concedidos, os scopes pertencem à key e não acompanham futuras
  alterações nas permissions do criador ou do responsável.

O responsável continua sendo uma referência de accountability. Suas
permissions pessoais não limitam nem ampliam a key.

## Reautenticação recente

Ações sensíveis exigem confirmação recente da identidade, no estilo step-up
authentication.

Fluxo:

1. a ação sensível detecta que a sessão não foi verificada recentemente;
2. responde `auth.reauthentication_required`;
3. o cliente chama `POST /auth/reauthenticate/` com senha e, quando aplicável,
   código MFA;
4. a sessão atual recebe `reauthenticated_at` no metadata;
5. a ação pode ser repetida durante a janela padrão de cinco minutos.

Somente tokens de sessão podem fazer reautenticação. API keys não podem usar
esse fluxo.

O mecanismo será declarativo:

```python
@require_recent_auth()
```

O decorator funcionará em actions, métodos e classes. Aceitará configuração:

```python
@require_recent_auth(max_age=300, require_mfa=True)
```

Sem argumentos, usa cinco minutos e exige MFA somente quando o usuário tiver
MFA ativo. O decorator apenas marca o requisito; uma permission global fará a
validação.

Criação, rotação e alterações de responsável/scopes exigem autenticação
recente.

## Endpoints de sessão

- `POST /auth/login/`
- `POST /auth/reauthenticate/`
- `POST /auth/logout/`
- `POST /auth/logout_all/`
- `GET /auth/sessions/`
- `GET /auth/sessions/current/`
- `PATCH /auth/sessions/{uuid}/`
- `DELETE /auth/sessions/{uuid}/`
- `POST /auth/sessions/revoke_all_except_current/`

O `PATCH` permite renomear o dispositivo. As respostas expõem UUID,
dispositivo, localização aproximada, criação, último uso, expiração, risco e
se a sessão é a atual.

Digest, token key e plain token nunca aparecem em listagens.

Logout e revogação em massa filtram exclusivamente tokens de sessão. Essas
operações nunca removem API keys nem tokens de reset pertencentes ao mesmo
responsável.

## Endpoints de API key

- `GET /auth/api_keys/`
- `POST /auth/api_keys/`
- `GET /auth/api_keys/{uuid}/`
- `PATCH /auth/api_keys/{uuid}/`
- `DELETE /auth/api_keys/{uuid}/`
- `POST /auth/api_keys/{uuid}/rotate/`
- `POST /auth/api_keys/{uuid}/suspend/`
- `POST /auth/api_keys/{uuid}/resume/`

O padrão de URL do projeto usa `_`, não `-`.

Criação e rotação retornam o plain token uma única vez. As demais respostas
nunca retornam segredo.

## Refatoração das views

As views atuais concentram emissão, metadata, geolocalização, risco e analytics.
O novo desenho separa:

- serializers de entrada e saída;
- serviço de emissão/rotação/revogação;
- serviço de metadata do dispositivo;
- serviço de avaliação de risco;
- policy/permissions;
- views finas que coordenam o caso de uso.

O login, logout e formato do header `Authorization: Bearer ...` permanecem
compatíveis com os clientes atuais.

## Erros

Os endpoints usarão a base padronizada de erros da API, incluindo:

- `auth.invalid_token`;
- `auth.expired_token`;
- `auth.revoked_token`;
- `auth.api_key_suspended`;
- `auth.responsible_inactive`;
- `auth.insufficient_scope`;
- `auth.reauthentication_required`;
- `organizations.tenant_mismatch`.

Esses códigos serão membros de `models.TextChoices` definidos no `errors.py`
do app proprietário, principalmente
`apps/api/autenticacao/errors.py` e `apps/organizacoes/errors.py`. Views,
serializers, services e permissions não usarão códigos como strings soltas.

## Auditoria e observabilidade

Eventos auditados:

- criação;
- rotação;
- suspensão e retomada;
- revogação;
- mudança de responsável;
- mudança de scopes.

A auditoria registra simultaneamente:

- a API key usada, quando houver;
- seu responsável;
- seu criador;
- a organização;
- o request ID.

Plain token, digest e token key não serão enviados a logs, Sentry, PostHog ou
traces.

## Testes

Cobertura mínima do desenho:

- contrato do modelo swappable com Knox;
- emissão e autenticação de cada tipo;
- reset de senha recusado na autenticação comum;
- constraints por tipo;
- criação atômica de metadata;
- login, logout e gerenciamento de sessões;
- UUID como identificador público;
- ausência de segredos nas respostas;
- rotação atômica e invalidação imediata;
- suspensão manual e automática;
- responsável inativo ou fora da organização;
- isolamento RLS e header conflitante;
- permissions administrativas;
- scopes CRUD, customizados e curingas;
- tradução `resource:action` para permission Django;
- decorators de scope e autenticação recente;
- throttling de login e reautenticação;
- códigos padronizados de erro;
- scrub de credenciais em logs e analytics.
