# Arquitetura

A aplicação usa Django REST Framework com PostgreSQL como banco principal, Redis
para cache e broker, e Celery para trabalho assíncrono. A especificação é gerada
por drf-spectacular e apresentada pelo Scalar.

A documentação segue Diátaxis para separar aprendizado, procedimentos, referência
e decisões arquiteturais.

## Erros

Toda falha HTTP — vinda do DRF, de middleware ou dos handlers de status do
Django — converge para o mesmo envelope de erro, com código estável tipado,
mensagem traduzível e `request_id` de correlação. Ver `docs/reference/api.md`
para o contrato e `.ai/brainstorming/spec/2026-07-28-api-errors-design.md`
para a spec completa.

## Organizações, assinaturas e faturamento

`apps.organizacoes` é dono do tenant, dos vínculos e do contexto imutável
anexado à requisição. `apps.assinaturas` é o núcleo comercial: catálogo
versionado, contratos, propostas, seats e política de acesso. O subapp
`apps.assinaturas.subapps.faturamento` depende desse núcleo e é o único código
da aplicação autorizado a importar `django_checkouts`; o núcleo comercial não
depende do subapp nem de um gateway. Até a rota de aceite pago é registrada
pelo subapp, de modo que importar ou instalar apenas o núcleo continua válido.

Essa direção mantém plano gratuito, trial e contratos enterprise locais
operacionais sem Stripe. Operações pagas usam fases explícitas de preparação,
I/O e confirmação. Locks e transações protegem somente o estado local; a chamada
externa ocorre fora de `atomic`, com chave idempotente derivada da organização e
do pedido. Depois do I/O, a confirmação relê revisão e snapshot. Resultado
remoto divergente ou incerto não é transformado em sucesso ou falha presumida:
fica para reconciliação.

O template fixa `django-checkouts[stripe]==1.0.1` da PyPI. A biblioteca é
stateless e normaliza checkouts, setups, assinaturas, faturas, webhooks e
eventos; os models e as decisões contratuais continuam locais. Nenhum módulo do
template importa o SDK `stripe` ou persiste o campo diagnóstico `raw` da
biblioteca.

### Entrada assíncrona e RLS

O webhook autentica o corpo bruto antes de normalizar uma allowlist de fatos
financeiros. Corpo, headers, e-mail, dados de cartão e segredo não são
persistidos. A unicidade global `(variante, identificador_evento)` permite
deduplicar antes de conhecer o tenant; uma referência assinada ou o mapeamento
de assinatura resolve a organização. Só então o evento entra no contexto RLS e
é entregue ao processador.

A fronteira anterior ao tenant usa funções PostgreSQL `SECURITY DEFINER` com
`search_path` fixo. A role `billing_ingress_runtime` executa essas interfaces
estreitas e lê apenas as colunas de mapeamento necessárias para resolver uma
assinatura ou referência; URL e fatos financeiros não entram nesse grant. As
funções pertencem a `billing_functions_owner`, role `NOLOGIN` que nunca é
concedida aos processos da aplicação. Checkouts, faturas, eventos roteados e
registros de operação usam `FORCE ROW LEVEL SECURITY`.

Há duas filas e três principals de aplicação. O worker geral, sem roles
financeiras, consome `celery` e processa eventos já tenantizados. O worker
ingress consome `billing_ingress`, assume apenas `billing_ingress_runtime` nas
operações globais e executa recovery/reconciliação. Um processo HTTP ingress,
com login próprio sem grants diretos de tabela e URLConf mínimo, aceita apenas
health checks e webhooks; o Nginx encaminha somente
`/faturamento/webhooks/` para ele. O web comum não recebe credenciais ingress
nem a credencial de migration.

O processador recupera o recurso atual pela interface normalizada antes de
aplicar efeitos monotônicos. Falhas temporárias usam backoff exponencial e no
máximo oito tentativas automáticas por ciclo. Falhas `RECONCILE_FIRST`, eventos
abandonados e a varredura periódica convergem no mesmo ingresso e processador;
pedidos manuais de retry ou reconciliação são explícitos, idempotentes e
auditados.
