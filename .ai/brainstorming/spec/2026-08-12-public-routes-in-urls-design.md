# Rotas públicas declaradas em `urls.py`

## Objetivo

Manter a descoberta automática de rotas públicas, deslocando somente o local da
declaração: de `<app>.public_routes.PUBLIC_ROUTES` para
`<app>.urls.PUBLIC_ROUTES`.

## Escopo desta etapa

- Configurar a instância `routes_registry` para descobrir `PUBLIC_ROUTES` em
  cada módulo `urls.py` dos `BUSINESS_APPS`.
- Mover a declaração atual de rotas públicas do app de autenticação para
  `apps/api/autenticacao/urls.py`.
- Remover `apps/api/autenticacao/public_routes.py`.
- Atualizar testes e documentação para a nova convenção.

`tenant_free_registry` e `tenant_free_routes.py` permanecem inalterados nesta
etapa; a mesma migração poderá ser feita posteriormente de forma independente.

## Arquitetura e fluxo

`CoreConfig.ready()` continuará chamando `routes_registry.discover()` durante o
boot. O registry continuará percorrendo `settings.BUSINESS_APPS`, importando um
módulo convencional por app, obtendo uma constante e acumulando prefixos junto
aos defaults (`/admin/`, `/health/` e `/metrics`).

A única alteração será a convenção de importação: o módulo passa a ser `urls` e
a constante continua sendo `PUBLIC_ROUTES`. A API de `RouteRegistry`, sua
idempotência, a validação de listas/tuplas de strings e a comparação por
`startswith` não mudam.

## Comportamento e erros

- Um app sem `urls.py` continua sendo ignorado pela descoberta.
- Um `urls.py` sem `PUBLIC_ROUTES` continua sendo ignorado, com log de debug.
- Uma constante com tipo inválido ou entradas não textuais continua levantando
  `TypeError` no boot.
- Prefixos continuam exigindo granularidade; `/auth/` ainda liberaria todas as
  rotas iniciadas por esse caminho.

## Testes

Os testes unitários de `RouteRegistry` passarão a construir módulos
`<app>.urls` e validarão que a descoberta, os defaults, a idempotência e as
falhas de validação preservam o comportamento atual. Não há mudança prevista
no fluxo do middleware de autenticação.

