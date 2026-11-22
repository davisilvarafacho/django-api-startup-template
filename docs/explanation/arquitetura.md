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

## Cobrança

`apps.billing` é um app pequeno sobre o `django-checkouts`. O gateway é a fonte
da verdade: o app guarda um espelho da assinatura e decide apenas se a
organização tem acesso ao plano pago e quantos membros cabem nele.

- `Plan` (`BaseTenantless`) é o catálogo. Exatamente um plano é o gratuito,
  garantido pelo `post_migrate`; depois de salvo, o admin não altera `is_free`,
  `slug`, `rank` nem `gateway_price_id`, e o `gateway_price_id` de um plano pago
  não muda: preço novo vira plano novo.
- `Subscription` (`Base`, sob RLS) é o espelho da assinatura da organização. Não
  há campo "tem acesso" gravado: o acesso é derivado do status.
- `BillingEvent` (`BaseTenantless`) é a caixa de entrada dos eventos do gateway.
  Guarda ids e metadados, sem payload bruto.

O evento só dispara a sincronização. O webhook verifica a assinatura, grava o
`BillingEvent` com chave única por variante e enfileira `process_billing_event`
depois do commit. A tarefa busca a assinatura no gateway, descobre a
organização pelo `reference_id` da própria assinatura remota e chama `sync`.
O `sync` trava a linha local antes de ler o gateway, então duas sincronizações
da mesma organização (webhook e reconciliação) se serializam, e a segunda grava
o estado mais novo. Ordem e duplicidade de eventos deixam de importar.

A reconciliação roda a cada 15 minutos: busca na API do gateway os eventos da
janela de 72 horas, insere os que faltam, reenfileira os pendentes e apaga os
aplicados há mais de 90 dias.

Os limites de membros ficam em `apps.billing.members`. Quando o plano efetivo
não comporta todos os vínculos ativos, a cobrança suspende os mais recentes,
nunca o proprietário, e marca `plan_limit_suspended_at` no vínculo; quando cabe
de novo, reativa só esses. `apps.organizacoes` depende do billing em três pontos
apenas: a checagem de capacidade em convites, aceites e reativações de vínculo; a
criação da assinatura gratuita (`Subscriptions.ensure_free`) no onboarding da
organização (`apps/organizacoes/onboarding.py`); e o disparo do cancelamento
quando a organização é encerrada.

As rotas de escrita chamam o gateway fora da transação da request e exigem o
header `Idempotency-Key`, repassado à biblioteca. Uma falha que deixa o resultado
incerto vira `503 billing.gateway_uncertain`: o cliente repete com a mesma chave,
e a reconciliação corrige o espelho.

Especificação completa: `.ai/brainstorming/spec/2026-11-21-billing-enxuto-design.md`.
Rotas em `docs/reference/api.md`, operação em `docs/how-to/operar-cobranca.md`.
