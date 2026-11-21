# Arquitetura

A aplicação usa Django REST Framework com PostgreSQL como banco principal, Redis
para cache e broker, e Celery para trabalho assíncrono. A especificação é gerada
por drf-spectacular e apresentada pelo Scalar.

A documentação segue Diátaxis para separar aprendizado, procedimentos, referência
e decisões arquiteturais.

## Tenancy e subtenancy

`apps.organizacoes` define a Organização, os Vínculos e o tenant. O app
`apps.workspaces` adiciona a subtenancy: um Workspace separa registros dentro
de uma Organização sem duplicar pessoas ou contrato comercial. A policy RLS
combina `tenant_id` com o modo de Workspace publicado no contexto da requisição.

O `current_workspace` fica no Vínculo, por isso a escolha é compartilhada entre
sessões e dispositivos. A seleção de visualização também é persistida em
`VinculoWorkspace`; o middleware publica ambas e as consultas de models que
herdam `Base` passam pelo RLS automaticamente. `NULL` em `Base.workspace`
significa que o registro é compartilhado pela Organização, e não que está fora
do isolamento.

Há quatro modos de contexto. `membership` limita uma pessoa aos Workspaces
selecionados e ao Workspace atual; `api_key` permite consultar todos os
Workspaces ativos do tenant, mas não inventa um Workspace atual; `control`
permite somente linhas compartilhadas; `system` atravessa Workspaces para uma
Organização explícita, usado em operações internas. A anotação `@no_workspace`
identifica as rotas de control plane e impede que elas dependam de uma seleção
humana.

O RLS é a fronteira de segurança e os serializers são a camada de contrato. Em
models opcionais, omissão preserva `NULL`; em models obrigatórios, omissão
humana usa o Workspace atual e a API key deve informar um Workspace ativo. Essa
regra é duplicada por constraint/policy no banco para proteger escritas fora do
serializer.

## Erros

Toda falha HTTP — vinda do DRF, de middleware ou dos handlers de status do
Django — converge para o mesmo envelope de erro, com código estável tipado,
mensagem traduzível e `request_id` de correlação. Ver `docs/reference/api.md`
para o contrato e `.ai/brainstorming/spec/2026-07-28-api-errors-design.md`
para a spec completa.

## Organizações

`apps.organizacoes` é dono do tenant, dos vínculos e do contexto imutável
anexado à requisição.

