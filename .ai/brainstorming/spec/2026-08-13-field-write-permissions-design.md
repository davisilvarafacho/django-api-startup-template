# Permissões dinâmicas de escrita por campo

## Objetivo

Permitir que cada organização proteja dinamicamente campos graváveis de seus
recursos, sem declarar as regras no código e sem criar `auth.Permission` em
tempo de execução.

Uma política define o papel mínimo necessário para escrever em um campo. Um
usuário abaixo desse papel pode receber uma concessão individual. Sem política,
o campo preserva o comportamento atual do endpoint.

Exemplo conceitual: `sales.discount` exige o papel `ADMINISTRADOR`, mas dois
gestores de confiança recebem concessões individuais para editar o desconto.

## Escopo

Esta primeira versão inclui:

- configuração em tempo de execução e isolada por organização;
- identidade estável por `recurso + campo` em todos os serializers e endpoints;
- um papel mínimo por campo e concessões para usuários individuais;
- a mesma regra para `POST`, `PUT` e `PATCH`;
- campos concretos e graváveis ligados diretamente a models registrados no
  catálogo `resource:action`;
- aliases simples de serializer, como `source="discount"`;
- foreign keys e many-to-many diretos quando forem graváveis pelo serializer;
- endpoints REST para catálogo, políticas, concessões e gestores;
- cache distribuído, auditoria, observabilidade e tratamento seguro de
  renomeações.

Ficam fora desta versão:

- negação individual para quem já possui o papel mínimo;
- concessões para grupos, times ou API keys;
- regras diferentes por serializer, endpoint ou operação HTTP;
- campos calculados, propriedades, relações reversas, escritas aninhadas ou
  `source` complexo;
- proteção de mutações realizadas diretamente por ORM ou serviços fora da
  camada DRF;
- automação de renomeações no comando `makemigrations`.

## Linguagem do domínio

- **Política de escrita de campo**: regra de uma organização que protege um
  campo e define seu papel mínimo.
- **Concessão de campo**: exceção individual que libera um usuário abaixo do
  papel mínimo.
- **Gestão de acesso a campos**: autoridade administrativa plena para gerir
  políticas, concessões e outros gestores. Não concede escrita nos campos.
- **Campo aberto**: campo sem política na organização atual; segue a
  autorização normal do endpoint.

O glossário resumido também vive em `CONTEXT.md`.

## Decisões de autorização

### Regra de escrita

Para um campo com política, a escrita é permitida quando pelo menos uma das
condições abaixo for verdadeira:

1. o papel do vínculo ativo alcança o papel mínimo da política; ou
2. o vínculo ativo possui uma concessão individual para a política.

A regra é, portanto, **papel mínimo OU concessão individual**. Não existe
negação individual. Um campo sem política permanece aberto para quem já pode
executar a operação do endpoint.

### API keys

API keys continuam sujeitas aos scopes normais do endpoint, mas não possuem
papel nem concessão de campo. Elas:

- podem escrever em campos abertos;
- recebem negação ao tentar escrever em qualquer campo protegido;
- não podem utilizar os endpoints administrativos desta funcionalidade.

O acesso do responsável humano pela API key não é herdado.

### Gestão administrativa

O proprietário da organização possui gestão de acesso a campos de forma
implícita. Um proprietário ou gestor pode delegar essa autoridade a qualquer
outro vínculo ativo da organização.

Quem possui gestão pode:

- criar, alterar e remover qualquer política;
- criar e revogar qualquer concessão;
- conceder e revogar gestão, inclusive revogar a própria gestão delegada.

Essa autoridade independe das políticas e concessões que o próprio gestor
possui. A autoridade implícita do proprietário não pode ser removida.

## Arquitetura

A funcionalidade será um app compartilhado da API. Os nomes das classes e dos
models serão em inglês; as rotas públicas permanecerão em português, no plural
e em `snake_case`.

### Catálogo de campos

Um `FieldCatalog` reutiliza o `scope_registry` existente. Cada model concreto
com `api_scope_resource` produz candidatos identificados por
`<resource>.<model_field>`.

O catálogo inclui somente campos concretos, diretos, editáveis e potencialmente
graváveis. Exclui:

- primary keys automáticas;
- campos `editable=False`;
- campos internos ou somente leitura definidos pela base;
- relações reversas, propriedades e campos calculados;
- campos sem correspondência direta com um model.

O catálogo declara elegibilidade; ele não torna um campo disponível em um
serializer que não o exponha.

### Models

Os registros são próprios do domínio, sem gerar `auth.Permission` dinâmica.

#### `FieldWritePolicy`

- `organization`: organização proprietária da regra;
- `resource`: nome público registrado em `api_scope_resource`;
- `field`: nome concreto do campo no model;
- `minimum_role`: valor válido de `Papel`;
- campos comuns de autoria, timestamps e exclusão lógica.

Existe no máximo uma política não excluída para
`organization + resource + field`.

#### `FieldGrant`

- `organization`;
- `policy`: política protegida;
- `membership`: vínculo beneficiado;
- campos comuns de autoria, timestamps e exclusão lógica.

Existe no máximo uma concessão não excluída por `policy + membership`. A
organização do vínculo, da concessão e da política deve ser a mesma.

#### `FieldAccessManager`

- `organization`;
- `membership`: vínculo com autoridade delegada;
- campos comuns de autoria, timestamps e exclusão lógica.

Existe no máximo um registro não excluído por
`organization + membership`. Proprietários não precisam desse registro.

As três entidades pertencem ao tenant e devem respeitar o isolamento por
organização. Uma concessão ou gestão só funciona enquanto o vínculo estiver
ativo e não excluído. Se a pessoa retornar com um novo vínculo, acessos antigos
não são restaurados automaticamente.

### Serviços

`FieldAuthorizationService` recebe organização, credencial/ator, model e os
campos efetivamente enviados. Ele resolve as políticas em lote e devolve todas
as negações, preservando o nome público usado no payload.

`FieldAccessManagementService` concentra as mutações administrativas, valida o
tenant, o estado dos vínculos, duplicidades e autoridade do ator, e agenda a
invalidação do cache somente após commit.

Consumers não devem criar, atualizar ou excluir policies, grants ou managers
diretamente com operações ORM em lote.

### Integração com serializers

`FieldWriteAuthorizationMixin` será integrado a `BaseModelSerializer`. Antes da
validação comum, ele:

1. considera somente chaves presentes no payload;
2. seleciona somente campos graváveis do serializer;
3. resolve o model field direto, aceitando um alias simples em `source`;
4. consulta `FieldAuthorizationService` uma vez para todo o conjunto;
5. interrompe a requisição se houver qualquer negação.

Todo serializer gravável de model no projeto deve herdar de
`BaseModelSerializer`. Um guardrail de projeto impedirá novas subclasses
diretas de `serializers.ModelSerializer` na camada de apps, pois elas
contornariam a autorização. Serializers somente de leitura podem declarar uma
isenção explícita.

Escritas diretas por ORM ou serviços internos permanecem fora deste contrato e
devem ter sua própria autorização no ponto de entrada.

## Fluxo de uma escrita

Em `POST`, `PUT` ou `PATCH`:

1. `TenantPermission` resolve a organização e o vínculo, ou a organização
   fixa da API key.
2. O serializer mapeia as chaves graváveis enviadas para model fields.
3. O serviço carrega todas as políticas do recurso na organização.
4. Campos sem política são permitidos.
5. Para cada campo protegido, o serviço avalia papel ou concessão; API keys são
   negadas.
6. Todas as negações são agregadas.
7. Se houver negação, a validação normal não prossegue e nada é persistido.
8. Sem negações, o fluxo normal do DRF continua.

`PATCH` normalmente envia poucos campos. `PUT` normalmente envia uma
representação mais completa. Nos dois casos, somente as chaves explicitamente
enviadas são autorizadas; campos omitidos, automáticos ou preservados pelo
serializer não exigem acesso.

## Contrato HTTP administrativo

Todas as rotas exigem organização atual, usuário humano autenticado e gestão de
acesso a campos.

- `GET /acessos_campos/campos_disponiveis/`
  lista recursos e campos elegíveis;
- `GET/POST /acessos_campos/politicas/`
  lista ou cria políticas;
- `GET/PATCH/DELETE /acessos_campos/politicas/{id}/`
  consulta, altera o papel mínimo ou remove uma política;
- `GET/POST /acessos_campos/politicas/{id}/concessoes/`
  lista ou cria concessões individuais;
- `DELETE /acessos_campos/politicas/{id}/concessoes/{usuario_id}/`
  revoga uma concessão;
- `GET/POST /acessos_campos/gestores/`
  lista ou delega gestores;
- `DELETE /acessos_campos/gestores/{usuario_id}/`
  revoga uma gestão delegada.

Os payloads usam nomes em português e `snake_case`, incluindo `recurso`,
`campo`, `papel_minimo` e `usuario_id`. Os identificadores de recurso continuam
seguindo o contrato público existente do `scope_registry`.

Criar uma política protege o campo imediatamente. Excluir a política também
exclui logicamente suas concessões e torna o campo aberto. Recriar a política
não restaura concessões anteriores.

## Erros

Uma escrita com campos negados retorna `403` e um item para cada campo. A
operação inteira é atômica.

```json
{
  "errors": [
    {
      "field": "discount",
      "code": "field_permissions.write_denied",
      "message": "Você não possui permissão para editar este campo."
    },
    {
      "field": "commission",
      "code": "field_permissions.write_denied",
      "message": "Você não possui permissão para editar este campo."
    }
  ],
  "request_id": "..."
}
```

Uma exceção agregadora tipada integrará a lista de `APIErrorItem` ao handler
existente, sem strings de código soltas.

Demais respostas:

- `400`: recurso ou campo fora do catálogo, ou papel mínimo inválido;
- `403`: ator sem gestão administrativa;
- `409`: política, concessão ou gestor duplicado;
- `409`: concessão ou gestão destinada a vínculo inativo ou de outro tenant;
- `409`: tentativa de revogar a gestão implícita do proprietário;
- `503`: banco indisponível quando o cache também não consegue responder.

Falhas de cache nunca concedem acesso.

## Cache, consistência e concorrência

A funcionalidade estende o cache semântico de autorização:

- políticas são resolvidas por `organização + recurso`;
- concessões e gestão são resolvidas por `organização + vínculo`;
- snapshots contêm apenas tipos primitivos e resultados negativos explícitos;
- mutações incrementam epochs em `transaction.on_commit()`;
- falha de leitura do Redis usa o banco;
- queries de recomposição não dependem do Cachalot;
- a resolução é em lote, sem uma consulta por campo.

Um estado versionado por organização coordena autorizações e mudanças de
política. Escritas usam bloqueio compartilhado e mutações administrativas usam
bloqueio exclusivo sobre esse estado durante a transação. Assim, escritas
normais podem prosseguir em paralelo, enquanto uma mudança de política possui
um ponto de ordenação inequívoco:

- se o endurecimento da política for confirmado primeiro, a escrita posterior
  usa a nova versão e é negada;
- se uma escrita já tiver adquirido o estado anterior primeiro, ela termina
  antes de a nova política entrar em vigor;
- flexibilizações concorrentes podem exigir que o cliente repita uma tentativa
  previamente negada.

O PostgreSQL é requisito do projeto; a implementação pode usar locks
transacionais compartilhados/exclusivos sem serializar escritas comuns entre
si.

## Renomeação e remoção de campos

Renomear somente o model field poderia deixar a política no nome antigo e abrir
o novo campo silenciosamente. O tratamento será fail-closed.

Cada `RenameField` autorizável deve ser acompanhado explicitamente por uma
operação reversível, por exemplo:

```python
RenameFieldWritePolicies(
    resource="sales",
    old_field="discount",
    new_field="discount_percentage",
)
```

A operação atualiza `FieldWritePolicy.field` e preserva todas as concessões.
Ao reverter a migration, restaura o nome anterior. A operação deve usar models
históricos e o alias do `schema_editor`.

Quando um campo for removido definitivamente, uma operação explícita deve
excluir logicamente sua política e concessões.

Se existir qualquer política cujo recurso ou campo não esteja no catálogo:

- `manage.py check` relata erro;
- métrica e log operacional são emitidos;
- todas as escritas naquele recurso e organização são bloqueadas até a
  correção.

A extensão automática do `makemigrations` foi deliberadamente adiada e está
registrada no roadmap.

## Auditoria e observabilidade

Criação, alteração e exclusão de políticas, concessões e gestores registram:

- organização;
- ator responsável;
- valores anteriores e novos;
- timestamp;
- `request_id` quando a mutação vier da API.

Serão expostas métricas para:

- decisões de campo permitidas e negadas;
- fallback do cache para o banco;
- latência da autorização;
- políticas órfãs;
- falhas de invalidação.

Métricas e logs não incluem valores dos campos, e labels não incluem IDs de
usuários ou organizações. Nomes de recursos e campos só podem aparecer em logs
estruturados de diagnóstico, nunca como labels de alta cardinalidade.

## Testes e critérios de aceitação

### Autorização

- campo sem política permanece editável;
- papel mínimo ou superior permite a escrita;
- papel inferior sem concessão é negado;
- concessão individual libera o vínculo correto;
- gestão administrativa não concede escrita implicitamente;
- API key é negada em campos protegidos e permitida em campos abertos;
- múltiplas negações retornam juntas em `errors`;
- qualquer negação impede toda a persistência;
- `POST`, `PUT` e `PATCH` verificam somente chaves enviadas;
- aliases simples obedecem ao model field e reportam a chave pública;
- configurações de uma organização não afetam outra.

### Configuração e ciclo de vida

- somente proprietários e gestores ativos acessam os endpoints;
- gestores podem administrar qualquer regra e delegar gestão;
- vínculo inativo ou excluído invalida concessão e gestão;
- duplicidades e cruzamentos de tenant são rejeitados;
- excluir e recriar uma política não restaura concessões antigas;
- o catálogo exclui campos não elegíveis;
- serializers graváveis que contornem a classe base falham no guardrail.

### Consistência operacional

- alterações invalidam o cache somente após commit;
- rollback preserva snapshots anteriores;
- Redis indisponível produz fallback ao banco, sem concessão por falha;
- mudanças concorrentes respeitam o ponto de ordenação transacional;
- política órfã bloqueia o recurso e aparece no system check;
- renomear e reverter preserva políticas e concessões;
- remoção explícita não deixa políticas órfãs;
- auditoria e métricas não expõem valores sensíveis.

## Evoluções futuras

- integrar `RenameFieldWritePolicies` automaticamente ao `makemigrations`;
- conceder acesso a times ou grupos;
- criar grants próprios para API keys;
- suportar campos aninhados e identificadores manuais de serializers;
- permitir granularidade por operação ou endpoint, se surgir um caso real.
