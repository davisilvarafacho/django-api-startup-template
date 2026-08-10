# Camada de API do metadata — design

- Data: 2026-08-09
- Status: aprovado, pronto para plano de implementação

## Problema

O app `apps/api/metadata` tem model, admin e testes de contrato, mas nenhuma
superfície HTTP. O item "Metadata framework (JSON key-value por modelo)" do
Batch 8 do roadmap segue aberto porque falta exatamente essa camada.

Além disso, o `MetadataMixin` já presente em `apps/api/base/models.py` grava no
banco a partir de uma property de leitura, o que precisa ser corrigido antes de
qualquer endpoint depender dele.

## Objetivo

Metadata é ponto de extensão para **quem consome a API**: uma integração anexa
chaves próprias a registros existentes sem precisar de migration no projeto
derivado. Chaves são arbitrárias e definidas pelo cliente.

Fora de escopo: `private_metadata` (separação público/privado no estilo Saleor),
busca ou filtro por conteúdo de metadata, e job de expurgo de órfãos.

## Decisões

### Exposição: action do `BaseModelViewSet`, ligada por padrão

O metadata é sub-recurso do próprio objeto, e não um recurso independente:

```
GET   /pedidos/42/metadata/
PATCH /pedidos/42/metadata/
```

Amarrar a rota ao viewset do alvo faz a autorização e o isolamento por tenant
virem de graça: quem chega ao `get_object()` já passou por `IsAuthenticated`,
`TenantPermission` (que aplicou o `SET LOCAL` do RLS), `TokenScopePermission` e
`CustomDjangoModelPermissions`.

A action é ligada por padrão em `BaseModelViewSet`, com opt-out por atributo:

```python
class VinculoViewSet(BaseModelViewSet):
    metadata_habilitado = False
```

O opt-out é implementado em `get_extra_actions()`, removendo a action da coleta
do router. A rota deixa de existir — 404 real, sem `OPTIONS` revelando o
endpoint. Nenhum viewset atual do repositório herda `BaseModelViewSet`
(`organizacoes` e `autenticacao` usam `viewsets.ModelViewSet` diretamente),
então o default ligado não expõe nada no estado atual; o atributo existe para o
projeto derivado.

### Contrato: merge de chaves, valores string, `null` remove

```
GET   /pedidos/42/metadata/
      -> 200 {"dados": {"erp_id": "X-1", "nota": "urgente"}}

PATCH /pedidos/42/metadata/
      {"dados": {"nota": null, "canal": "web"}}
      -> 200 {"dados": {"erp_id": "X-1", "canal": "web"}}
```

`PATCH` funde as chaves enviadas com as existentes. Valor `null` remove a chave.
Valores são string — o cliente serializa estruturas por conta própria, o que
mantém validação, limites e futura indexação triviais. Corpo e resposta usam a
mesma envoltória `{"dados": {...}}`, para o payload poder ganhar campos depois
sem quebrar cliente.

Não há `DELETE`. O motivo é concreto: hoje
`CustomDjangoModelPermissions.is_action()` só reconhece uma action quando o path
começa com `v1/`, prefixo que nenhuma URL do projeto usa, então o `perms_map`
por action está inerte e vale o mapa por método HTTP. Nesse mapa `DELETE` exige
`delete_<model>` — permissão errada para remover uma chave de anotação. Com
apenas `GET` e `PATCH`, as permissões caem certas sem depender do conserto
daquele bug, que está sendo tratado em outro trabalho.

### Permissões

| Método | Permissão exigida |
|--------|-------------------|
| `GET` | `<app_label>.view_<model>` |
| `PATCH` | `<app_label>.change_<model>` |

Quem pode editar o registro pode anotá-lo. Nenhum codename novo, nenhuma
declaração em `Meta.permissions`, nenhum cadastro por model — o que é coerente
com a action estar ligada por padrão.

### Limites, vindos de settings

| Setting | Default |
|---------|---------|
| `METADATA_MAX_KEYS` | 50 |
| `METADATA_MAX_KEY_LENGTH` | 256 |
| `METADATA_MAX_VALUE_LENGTH` | 1024 |

O limite de valor foi fixado em 1024 (o número do Saleor), assumindo que o
`1204` dito na conversa foi digitação.

Lidos do ambiente com esses defaults. Isso exige duas mudanças em
`utils/env.py`: as três chaves entram na tupla `ENVS` (que é um `Literal`
fechado) e o módulo ganha `get_int_from_env`, hoje inexistente — só há
`get_bool_from_env` e `get_list_from_env`. As variáveis também entram no
`.env.example`.

Violação de limite, chave vazia, corpo que não seja objeto plano ou valor que
não seja string nem `null` produzem `ValidationError` do DRF, que o exception
handler em `apps/api/core/errors.py` já converte em **422** dentro do envelope
`{"errors": [...], "request_id": ...}`.

### Correções no `MetadataMixin`

O mixin em `apps/api/base/models.py` passa a ser somente leitura:

- `raw_metadata` devolve o dict existente ou `{}`, **sem** `get_or_create`. Hoje
  uma leitura grava: numa listagem de 30 itens são até 30 `INSERT`, cada um
  registrado pelo `AuditlogHistoryField`, cada um herdando a organização do
  contexto RLS vigente — e, fora do ciclo de request, a mesma leitura levanta
  `RLSContextRequiredError` em vez de devolver um dict.
- Ganha uma `GenericRelation` para o `Metadata`, o que torna
  `prefetch_related` possível e elimina o N+1 acima.
- A property `content_type` vira o método `get_content_type()`. Como property
  ela colidia com o campo FK homônimo de `Metadata` (que herda de `Base` e
  portanto do mixin), funcionando apenas por qual descriptor vence no MRO; como
  método, o nome não colide e fica explícito que há resolução de `ContentType`
  ali, no idioma de `get_absolute_url`.

### Service layer

Toda escrita vive em `apps/api/metadata/handlers.py`:

- `mesclar_dados(atuais, alteracoes)`, função pura que devolve o dict resultante
  ou levanta erro de validação ao violar limite — testável sem banco;
- `aplicar_metadata(objeto, alteracoes)`, que resolve o `ContentType`, obtém ou
  cria a linha de `Metadata` dentro da organização corrente, aplica o merge e
  persiste.

`aplicar_metadata` é o **único** ponto do sistema que cria linha de `Metadata`.

### Constraint por organização

A constraint atual é `UniqueConstraint("content_type", "object_id")`, sem
organização. Como o `MetadataMixin` vive em `BaseTenantless`, `Usuario`,
`Organizacao`, `Vinculo` e `Convite` também aceitam metadata — e aí duas
organizações não conseguem anotar o mesmo usuário. Pior: o RLS esconde a linha
da outra organização, então a segunda escrita recebe `IntegrityError` sobre uma
linha invisível, virando 500 e vazando existência entre inquilinos.

A constraint passa a seguir os [ADR 0007](../../adr/0007-indices-e-constraints-por-organizacao.md)
e [ADR 0008](../../adr/0008-unicidade-por-organizacao.md):

```python
models.UniqueConstraint(
    fields=["organizacao", "content_type", "object_id"],
    condition=models.Q(is_deleted=False),
    name="metadata_organizacao_content_type_object_id_unique",
)
```

Migration `0002` no app `metadata`. Cada organização passa a ter seu próprio
documento de metadata para o mesmo objeto, que é a semântica correta quando o
metadata é extensão do consumidor da API.

### Ciclo de vida

Exclusão de objeto é soft delete, então a linha de metadata permanece e a action
devolve 404 — `get_object()` usa o manager `objects`, que já filtra
`is_deleted`. Em hard delete a `GenericForeignKey` deixa órfão; o comportamento
fica documentado e nenhum job de expurgo será construído agora.

## Arquivos

| Arquivo | Mudança |
|---------|---------|
| `apps/api/metadata/handlers.py` | novo — merge puro e `aplicar_metadata` |
| `apps/api/metadata/serializers.py` | novo — `MetadataAlteracaoSerializer` |
| `apps/api/metadata/models.py` | constraint com organização e `condition` |
| `apps/api/metadata/migrations/0002_*.py` | nova constraint |
| `apps/api/base/views.py` | mixin com a action e o opt-out; herdado por `BaseModelViewSet` |
| `apps/api/base/models.py` | `MetadataMixin`: leitura pura, `GenericRelation`, `get_content_type()` |
| `utils/env.py` | `get_int_from_env` e as três chaves em `ENVS` |
| `api/settings.py` | os três settings de limite |
| `.env.example` | as três variáveis |
| `docs/reference/metadata.md` | novo, com entrada no `mkdocs.yml` |
| `docs/ROADMAP.md` | Batch 8: metadata framework concluído |

A cadeia de imports fica `base/views → metadata/{serializers,handlers} →
metadata/models → base/models`, sem ciclo (ADR 0006).

## Testes

Sem banco, no handler: merge preservando chaves ausentes do payload, `null`
removendo chave, cada um dos três limites estourando, corpo que não é objeto
plano, valor não-string, chave vazia.

Com banco, na action: `GET` exigindo `view_<model>`, `PATCH` exigindo
`change_<model>`, `PATCH` de organização diferente não enxergando nem
sobrescrevendo o documento alheio, opt-out resultando em 404 sem rota
registrada, e `django_assert_num_queries` provando que `prefetch_related`
mantém constante a contagem de queries de uma listagem.
