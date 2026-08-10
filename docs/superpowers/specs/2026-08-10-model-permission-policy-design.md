# Autorização por recurso nos endpoints DRF

**Status:** aprovado em 2026-08-10.

## Objetivo

Reconstruir a autorização dos endpoints de negócio para que cada action tenha
uma política única, declarativa e auditável. Sessões humanas combinarão
permission Django e papel no tenant. API keys continuarão usando scopes
próprios, limitados às operações explicitamente disponíveis para integrações.

O desenho elimina a detecção de actions pela URL, impede que
`permission_classes` de uma view remova silenciosamente as proteções do model e
torna a mesma política a fonte dos scopes, da delegação e da documentação.

## Fora de escopo

- Alterar autenticação, emissão ou armazenamento dos tokens.
- Substituir permissions, grupos, Guardian, Rules ou o cache de autorização.
- Transformar scopes em autorização de sessões humanas.
- Criar autorização por campo.
- Tornar actions destrutivas indisponíveis para sessões humanas.

## Princípios

### Sessões humanas

Uma sessão só executa uma action de negócio quando todas as condições forem
satisfeitas:

```text
sessão autenticada
AND tenant e vínculo válidos
AND permission Django da action
AND papel mínimo naquela organização
AND objeto alcançável pelo queryset/RLS
```

As camadas respondem a perguntas diferentes:

- permission Django: a pessoa pode executar essa operação?
- papel: ela pode executá-la nesta organização?
- queryset e RLS: quais linhas da organização podem ser alcançadas?
- autenticação recente: a identidade precisa ser confirmada novamente?

Possuir a permission sem o papel, ou o papel sem a permission, não autoriza a
request.

### API keys

Uma API key segue outro pipeline:

```text
API key válida e presa ao tenant
AND action disponível para integrações
AND scope suficiente
AND objeto alcançável pelo queryset/RLS
```

Permissions e papéis pessoais do responsável não participam do uso da key. O
responsável fornece accountability; a autoridade da credencial está em seus
scopes. Permissions humanas participam somente da criação e administração da
key.

Um scope é um subconjunto de autoridade delegado a uma credencial. Uma
permission Django representa a autoridade da pessoa. Sessões humanas não serão
limitadas por scopes.

## `ResourcePolicy`

Cada ViewSet de model declara uma `ResourcePolicy` imutável no atributo de
classe `authorization_policy`.

```python
class VinculoViewSet(BaseModelViewSet):
    queryset = Vinculo.objects.all()
    serializer_class = VinculoSerializer

    authorization_policy = ResourcePolicy(
        resource="memberships",
        minimum_roles={
            "read": Papel.VISUALIZADOR,
            "update": Papel.ADMINISTRADOR,
            "activate": Papel.ADMINISTRADOR,
            "deactivate": Papel.ADMINISTRADOR,
            "delete": Papel.ADMINISTRADOR,
        },
        api_key_forbidden_actions={"delete"},
        custom_actions={
            "ativar": ActionPolicy(
                action="activate",
                permission="can_toggle_vinculo",
                api_key_allowed=True,
            ),
            "inativar": ActionPolicy(
                action="deactivate",
                permission="can_toggle_vinculo",
                api_key_allowed=True,
            ),
        },
    )
```

O model será derivado de `queryset.model`. Uma policy só declarará o model
explicitamente quando o ViewSet não tiver queryset estático; não haverá dois
atributos obrigatórios com a mesma informação.

### Actions convencionais

As actions do DRF serão traduzidas sem consultar a URL:

| Action do ViewSet | Operação lógica | Permission Django | Scope |
| --- | --- | --- | --- |
| `list`, `retrieve` | `read` | `view_<model>` | `<resource>:read` |
| `create` | `create` | `add_<model>` | `<resource>:create` |
| `update`, `partial_update` | `update` | `change_<model>` | `<resource>:update` |
| `destroy` | `delete` | `delete_<model>` | `<resource>:delete` |

A policy deve informar o papel mínimo de toda operação exposta a sessões. Para
uma operação tenant-free, a ausência de papel será explícita, não inferida por
omissão. Uma action sem regra completa falha na validação de inicialização.

### Actions customizadas

Uma action customizada declara:

- nome lógico e estável da operação;
- codename Django;
- papel mínimo;
- disponibilidade para API key.

`activate` e `deactivate` serão scopes separados, mesmo que usem o mesmo
codename Django. Isso preserva menor privilégio e melhora a auditoria.

```text
memberships:activate   -> organizacoes.can_toggle_vinculo
memberships:deactivate -> organizacoes.can_toggle_vinculo
```

Actions customizadas não ficam disponíveis para API keys por padrão. O opt-in
`api_key_allowed=True` é obrigatório.

## `ModelPermissionMixin`

`BaseModelViewSet` herdará automaticamente de `ModelPermissionMixin`. Todo
ViewSet concreto desse base deverá declarar `authorization_policy`.

O mixin sobrescreverá `get_permissions()` para combinar as proteções
obrigatórias com as permissions adicionais retornadas pelo DRF:

```text
IsAuthenticated
TenantPermission
ResourceAccessPermission
permissions adicionais da classe ou da @action
```

`ResourceAccessPermission` lê `view.authorization_policy`, resolve
`view.action` e escolhe o pipeline de sessão ou API key.

### Composição aditiva

Em classes que herdam de `ModelPermissionMixin`, `permission_classes` tem
semântica aditiva. Uma declaração na classe ou em `@action` complementa as
proteções obrigatórias; ela não as substitui, diferentemente do comportamento
padrão do DRF.

```python
class VinculoViewSet(BaseModelViewSet):
    permission_classes = [MinhaPermissionAdicional]
```

Produz:

```text
IsAuthenticated
TenantPermission
ResourceAccessPermission
MinhaPermissionAdicional
```

O mesmo vale para `@action(permission_classes=[...])`. Permissions repetidas
serão instanciadas e executadas uma única vez, preservando a ordem. Esse efeito
colateral intencional será documentado na classe base e no guia de criação de
endpoints.

`IsAuthenticated` não é uma carta branca e não desativa a policy. Endpoints de
self-service, como “meu perfil”, que não devem exigir `view_usuario`, não
herdam de `ModelPermissionMixin`; usam `APIView`, `GenericViewSet` ou um base
específico e restringem o objeto a `request.user`.

Views públicas ou sem política de model também não usam esse mixin.

## Rotas públicas e `AllowAny`

`AllowAny` e o middleware de autenticação atuam em momentos diferentes. A
declaração pública precisa configurar as duas camadas sem tornar o registry de
rotas obsoleto.

Para views próprias, `@public` será a fonte de verdade e configurará também
`permission_classes = [AllowAny]`. `public_routes.py` continuará existindo para
prefixos, views de bibliotecas e rotas que não podem ser decoradas.

System checks rejeitarão:

- `AllowAny` sem `@public` ou sem prefixo no registry;
- rota pública que preserve permissions restritivas;
- action pública dentro de `ModelPermissionMixin`.

Uma action pública de um recurso de model deve ser extraída para uma view
separada. Isso evita que uma configuração local remova inadvertidamente as
proteções de todo o recurso.

## Disponibilidade de API keys

Operações CRUD são delegáveis por padrão nos recursos que aceitam API keys. A
policy pode bloquear ações específicas:

```python
ResourcePolicy(
    resource="memberships",
    api_key_forbidden_actions={"delete"},
    ...,
)
```

Também será possível desligar API keys para o recurso inteiro com
`api_key_enabled=False`.

Regras:

- CRUD implementado é disponível, salvo se estiver em
  `api_key_forbidden_actions`;
- custom actions exigem `api_key_allowed=True`;
- actions não implementadas não geram scopes;
- a indisponibilidade é verificada antes do scope;
- `resource:*` contém somente ações disponíveis para aquele recurso;
- `*` nunca contorna uma action proibida ou um recurso desabilitado.

Assim, `memberships:delete` continua disponível para uma sessão com
`delete_vinculo` e papel suficiente, mas nunca para uma API key. Já
`memberships:activate` e `memberships:deactivate` podem ser delegados.

As restrições são por recurso. `delete` não será globalmente proibido para API
keys de modelos comuns.

## Reaproveitamento do `ScopeRegistry`

O `ScopeRegistry` existente continuará sendo o único registry de scopes. Não
será criado um segundo registry para policies.

O `ModelPermissionMixin` lê a policy diretamente durante a request. O
`ScopeRegistry` consome as mesmas policies para:

- validar recursos, actions e scopes;
- traduzir scopes para codenames Django;
- expandir wildcards;
- validar delegação;
- fornecer o catálogo de scopes;
- alimentar documentação e system checks.

O atributo legado `model.api_scope_resource` deixará de ser a fonte dos
recursos expostos por ViewSets. Durante a migração poderá existir apenas como
compatibilidade; ao final, `ResourcePolicy.resource` será a única declaração do
nome público.

## Delegação de scopes

A autoridade para administrar integrações será separada das permissions
operacionais do usuário.

- `add_apikey` permite criar API keys;
- sem autoridade especial, o usuário só delega scopes equivalentes às próprias
  permissions Django;
- `grant_api_scopes` permite delegar qualquer scope disponível no tenant, sem
  exigir todas as permissions operacionais correspondentes;
- `grant_unrestricted_apikey` continua obrigatório para o wildcard global `*`;
- nenhuma dessas permissions libera uma action proibida pela policy.

`grant_api_scopes` é uma permission administrativa forte. O criador recebe o
segredo da key e pode usá-lo; portanto, conceder essa permission equivale a
confiar que ele pode delegar as operações disponíveis para integrações.

## Catálogo de scopes

Um endpoint somente para sessões humanas permitirá montar a seleção de scopes:

```http
GET /auth/api_keys/scopes/
X-Organization: acme
```

Cada action informará separadamente a capacidade da plataforma e a autoridade
do usuário atual:

```json
{
  "resource": "memberships",
  "action": "delete",
  "scope": "memberships:delete",
  "available_for_api_key": false,
  "delegable_by_current_user": false,
  "reason": "session_only"
}
```

Estados válidos:

| Disponível para key | Delegável pelo usuário | Significado |
| --- | --- | --- |
| sim | sim | pode ser selecionado |
| sim | não | existe, mas o usuário não pode concedê-lo |
| não | não | operação exclusiva de sessão |

O estado “indisponível e delegável” é inválido. O catálogo também mostrará a
expansão de `resource:*`, excluindo ações proibidas.

O endpoint exige sessão, tenant e permission para administrar API keys. Uma API
key não pode consultar o catálogo nem administrar outras credenciais.

## Erros

Escritas em `/auth/api_keys/` validarão cada item de `scopes` e apontarão seu
índice no payload.

- `auth.invalid_scope`: formato inválido ou recurso/action desconhecido;
- `auth.scope_not_available`: action conhecida, mas indisponível para API key;
- `auth.scope_not_delegable`: action disponível, mas o usuário não pode
  concedê-la;
- `auth.insufficient_scope`: a key não possui o scope exigido pela request.

Uma action proibida será recusada antes de avaliar wildcards ou permissions do
responsável. Configurações incompletas falham fechadas.

## Validação de inicialização

System checks detectarão:

- `BaseModelViewSet` concreto sem `authorization_policy`;
- recurso ou scope duplicado;
- action sem permission Django ou papel resolvível;
- codename Django inexistente;
- action proibida que não existe no recurso;
- operação habilitada para API key sem scope válido;
- wildcard sem ações disponíveis;
- inconsistências entre `@public`, `AllowAny` e `public_routes`.

O check deve carregar as rotas antes de validar o conjunto de ViewSets, para
que o catálogo seja completo e independente da ordem incidental de imports.

## Matriz inicial dos recursos existentes

| Recurso | Sessão humana | API key |
| --- | --- | --- |
| organizações | permission CRUD; actions tenant-free sem papel quando declarado | leitura da própria organização |
| times | permission CRUD + papel por action | CRUD disponível conforme policy |
| vínculos | permission CRUD + papel; activate/deactivate customizados | read, update, activate e deactivate; delete proibido |
| convites | permission CRUD + papel; accept customizado | actions liberadas pela policy |
| logs de auditoria | `view_logalteracao`; somente sessão | desabilitada |
| gerenciamento de API keys | permissions `*_apikey`; somente sessão | desabilitada |
| perfil, senha e MFA | policies próprias fora do mixin | desabilitada |
| lookup | autenticação e registry de lookup, fora do mixin | sem mudança neste projeto |

Os papéis mínimos atuais serão preservados na migração inicial. Mudanças de
produto na matriz de papéis exigirão decisão separada.

## Testes

### Resolução de policy

- mapeamento CRUD por `view.action`;
- mapeamento de actions customizadas;
- derivação do model pelo queryset;
- validação de permissions e papéis;
- ausência de dependência da estrutura da URL.

### Composição do mixin

- permissions obrigatórias são sempre aplicadas;
- `permission_classes` da classe e da action são aditivas;
- duplicatas executam uma única vez;
- `IsAuthenticated` não desativa a policy;
- `AllowAny` não é aceito sem declaração pública consistente;
- ViewSet concreto sem policy falha no system check.

### Sessões humanas

Cada action terá casos para:

- permission e papel suficientes;
- permission ausente;
- papel insuficiente;
- tenant ausente ou vínculo inativo;
- acesso a objeto de outro tenant;
- permission customizada.

### API keys

- scope concreto suficiente e insuficiente;
- action proibida mesmo com `resource:*` ou `*`;
- custom action disponível e indisponível;
- recurso com `api_key_enabled=False`;
- delegação comum, ampla e irrestrita;
- catálogo disponível, indisponível e não delegável;
- API key impedida de administrar credenciais.

### Rotas públicas

- `@public` dispensa o middleware e aplica `AllowAny`;
- prefixos de `public_routes` continuam funcionando;
- combinações inconsistentes falham no system check.

## Documentação

A implementação atualizará:

- guia de criação de ViewSets e o contrato aditivo de
  `ModelPermissionMixin`;
- explicação de autenticação, permissions, papéis, RLS e scopes;
- referência da API de gerenciamento de keys e do catálogo de scopes;
- OpenAPI, distinguindo operações de sessão, scopes delegáveis e ações
  exclusivas de sessão;
- documentação de `@public` e `public_routes`.

Nenhuma lista manual de scopes será mantida fora da policy e do
`ScopeRegistry`.

## Migração

A mudança será feita em etapas para evitar liberar endpoints durante a
transição:

1. introduzir `ResourcePolicy`, `ActionPolicy`, `ModelPermissionMixin` e checks;
2. adaptar o `ScopeRegistry` para policies mantendo compatibilidade temporária;
3. migrar os ViewSets de negócio e preservar papéis atuais;
4. implementar disponibilidade, delegação ampla e catálogo de scopes;
5. integrar `@public` com `AllowAny` e manter `public_routes` para exceções;
6. remover `CustomDjangoModelPermissions` e metadados de scope substituídos;
7. atualizar documentação e executar a matriz completa de autorização.

Durante a migração, uma rota sem policy válida deve falhar fechada. Não haverá
fallback silencioso para a autorização anterior.
