# 0009 — Workspaces como subtenancy

- Status: Aceito
- Data: 2026-09-24

## Contexto

`Organização` continua sendo o tenant e a fronteira comercial do sistema, mas
alguns domínios precisam separar dados dentro da mesma Organização. Exemplos
incluem unidades operacionais, ambientes ou filiais que compartilham pessoas e
contrato, mas não devem aparecer em toda consulta humana.

## Decisão

`Workspace` é a fronteira de subtenancy dentro de uma Organização. O registro
de negócio herda `Base` e recebe `workspace` opcional: `NULL` significa um
registro compartilhado por toda a Organização. A policy RLS combina o tenant
com o contexto de Workspace publicado pela aplicação:

- uma sessão humana usa o Vínculo, o Workspace atual e a seleção persistida;
- uma API key vê todos os Workspaces ativos da Organização;
- `control` vê somente registros compartilhados;
- `system` atravessa Workspaces, mas continua limitado a uma Organização
  explícita.

O `current_workspace` pertence ao Vínculo, não à sessão. Assim, a seleção é
compartilhada entre dispositivos e sessões do mesmo vínculo. A seleção de
visualização também é persistida no vínculo e é aplicada automaticamente pela
policy RLS. Rotas de control plane recebem `@no_workspace` e não exigem
Workspace atual; elas só podem enxergar registros compartilhados.

Serializers de models opcionais preservam `NULL` quando o campo é omitido.
Serializers de models obrigatórios usam o Workspace atual quando omitidos e
exigem um Workspace explícito para API keys. O banco também exige a presença
do campo em models obrigatórios, protegendo `bulk_create`, `update` e escritas
fora do serializer.

## Alternativas rejeitadas

**Reutilizar `Time`.** Time representa agrupamento colaborativo e não uma
fronteira de isolamento; transformá-lo em Workspace misturaria autorização de
grupo com tenancy e quebraria sua semântica existente.

**Transformar cada filial em uma Organização.** Isso duplicaria vínculos,
contratos e administração para uma subdivisão que deve compartilhar o tenant.
Também perderia a distinção entre o contrato comercial e o recorte operacional.

**Confiar somente em filtros de queryset.** Um filtro de aplicação pode ser
esquecido e não protege SQL direto, `bulk_create` ou outra conexão. RLS é a
garantia final; os querysets e serializers continuam necessários para UX,
validação e autorização da API.

## Consequências

- Models novos devem declarar explicitamente se são opcionais ou obrigatórios.
- Toda escrita precisa publicar contexto de Organização e Workspace adequado.
- Roles que consultam models com a policy devem possuir leitura nas tabelas de
  contexto usadas pela policy.
- Inativar um Workspace ou remover seu acesso limpa seleções atuais inválidas
  e impede novas leituras e escritas naquele recorte.
- API keys não mantêm Workspace atual: elas trabalham no conjunto ativo do
  tenant e informam o Workspace em escritas obrigatórias.
