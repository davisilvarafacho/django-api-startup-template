# Operar a cobrança

Este guia cobre a configuração e a operação do app `apps.billing` com o Stripe,
pelo `django-checkouts`. O desenho está em `docs/explanation/arquitetura.md`, as
rotas em `docs/reference/api.md`.

## Variáveis de ambiente

Todas são lidas por `utils/env.py` e estão no `.env.example`.

| Variável | Padrão | Para que serve |
| --- | --- | --- |
| `STRIPE_SECRET_KEY` | vazio | Chave secreta da Stripe. Sem ela, fora de produção, a variante não é registrada e as operações que chamam o gateway não funcionam. Em produção, a ausência falha no `check --deploy`. |
| `STRIPE_WEBHOOK_SECRET` | vazio | Segredo de assinatura do endpoint de webhook. Sem ele, todo evento é recusado com `400 billing.webhook_invalid`. |
| `STRIPE_SANDBOX` | `true` fora de produção, `false` em produção | Opera o gateway em modo de teste. Com chave `sk_live_` e sandbox ligado, o check emite aviso. |
| `BILLING_GATEWAY_VARIANT` | `stripe` | Variante do `CHECKOUT_VARIANTS` usada pela cobrança. A rota de webhook só aceita esse nome. |
| `FRONTEND_BILLING_CHECKOUT_SUCCESS_URL` | `http://localhost:3000/cobranca/checkout/sucesso` fora de produção; vazio em produção, sem padrão | Para onde o checkout volta depois do pagamento. Em produção, defina a variável. |
| `FRONTEND_BILLING_CHECKOUT_CANCEL_URL` | `http://localhost:3000/cobranca/checkout/cancelado` fora de produção; vazio em produção, sem padrão | Para onde o checkout volta se o cliente desistir. Em produção, defina a variável. |
| `BILLING_FREE_PLAN_MEMBER_LIMIT` | `3` | Limite de membros do plano gratuito, gravado quando o plano é criado. |
| `BILLING_RECONCILE_WINDOW_HOURS` | `72` | Janela, em horas, em que a reconciliação busca eventos no gateway. |
| `BILLING_EVENT_MAX_ATTEMPTS` | `10` | Tentativas de processamento de um evento antes de a reconciliação desistir dele. |

Depois de alterar as chaves, valide com `make check`.

## Criar os planos no gateway

Cada plano pago corresponde a **um preço recorrente** no Stripe. Um preço não
muda depois de criado: para mudar o valor, crie outro preço e outro plano.

1. No Stripe, crie um produto e um preço recorrente em BRL, com a periodicidade
   desejada.
2. Copie o ID do preço, no formato `price_...`.
3. Anote a periodicidade. Ela precisa corresponder ao `billing_cycle` do plano
   no sistema.

Um preço anual, por exemplo, é outro plano, com o mesmo `rank` do mensal. Mesmo
`rank` conta como upgrade: a troca é imediata, com fatura proporcional.

## Criar os planos no admin

No admin Django, em **Planos**, cadastre cada plano pago com:

- **Nome** e **slug**: o slug é o identificador que o frontend envia em
  `{"plan": "<slug>"}`. Ele é único e, depois de salvo, não muda.
- **Ordem** (`rank`): posição na escala. O maior rank é o plano superior, e é
  ele que decide entre upgrade e downgrade. Depois de salvo, não muda.
- **Limite de membros**: máximo de vínculos ativos neste plano. Continua
  editável depois de salvo.
- **Gratuito**: só o plano gratuito é marcado com este campo. Depois de salvo,
  não muda, para que sempre exista exatamente um plano gratuito.
- **Valor** (em centavos) e **ciclo de cobrança**: só para exibição e para o
  catálogo.
- **ID do preço no gateway**: o `price_...` copiado do Stripe. Este campo só é
  editável na criação. Depois de salvo, fica somente leitura, e isso é de
  propósito: o espelho mapeia cada preço a um único plano.
- **Ativo**: plano inativo sai do catálogo e não pode ser assinado nem usado
  como destino de troca, mas continua reconhecido nos eventos que já referenciam
  o preço.

O plano **gratuito não se cadastra**: o sistema o cria ao final de cada
`migrate`, com o limite de `BILLING_FREE_PLAN_MEMBER_LIMIT`. Só pode existir um, e ele não
tem preço no gateway.

Para desativar um plano, marque **Ativo** como falso ou use a ação
**Inativar registros selecionados**.

## Configurar o webhook no Stripe

1. No Stripe, crie um endpoint apontando para
   `https://<seu-host>/cobranca/webhooks/stripe/`. O último segmento é a
   variante: use o valor de `BILLING_GATEWAY_VARIANT`.
2. Copie o segredo de assinatura do endpoint para `STRIPE_WEBHOOK_SECRET`.
3. Assine os tipos de evento que o `django-checkouts` mapeia para a cobrança:
   - `checkout.session.created`, `checkout.session.completed`,
     `checkout.session.async_payment_succeeded`,
     `checkout.session.async_payment_failed`, `checkout.session.expired` e
     `checkout.session.canceled`;
   - `customer.subscription.created`, `customer.subscription.updated` e
     `customer.subscription.deleted`;
   - `invoice.created`, `invoice.finalized`, `invoice.paid`,
     `invoice.payment_failed`, `invoice.voided` e `invoice.marked_uncollectible`.

   A tabela completa está em `django_checkouts/gateways/stripe/mapping.py`, na
   versão instalada.

O webhook é público e não usa token: a autenticidade vem só da assinatura do
gateway. Ele apenas grava o evento e enfileira o processamento; a aplicação ao
espelho acontece no worker.

## Subir os processos

A cobrança depende de três tarefas Celery:

- `billing.process_event`: aplica um evento ao espelho. Disparada pelo webhook
  e pela reconciliação.
- `billing.reconcile`: a cada 15 minutos, pelo beat.
- `billing.cancel_closed_organization`: disparada quando uma organização é
  encerrada.

Em desenvolvimento, use `make worker` e `make beat`. O beat usa o agendador do
banco (`DatabaseScheduler`), e a agenda de `billing.reconcile` está em
`CELERY_BEAT_SCHEDULE`, em `api/settings.py`.

## O que a reconciliação faz

A cada 15 minutos, `billing.reconcile` executa quatro passos:

1. **Eventos perdidos.** Pede ao gateway os eventos dos últimos
   `BILLING_RECONCILE_WINDOW_HOURS` (72 horas por padrão), página a página. Os
   que ainda não estão na caixa de entrada são gravados e enfileirados. Eventos
   mais antigos que a janela não são recuperados por este passo.
2. **Pendentes.** Reenfileira os eventos não aplicados que estão na caixa de
   entrada há mais de 5 minutos e têm menos tentativas que
   `BILLING_EVENT_MAX_ATTEMPTS`.
3. **Organizações encerradas.** Reenfileira o cancelamento imediato das
   organizações encerradas que ainda têm assinatura vinculada ao gateway (veja
   [Organização encerrada](#organizacao-encerrada)). Uma falha neste passo é
   registrada no log e não impede os demais.
4. **Limpeza.** Apaga os eventos aplicados há mais de 90 dias.

A reconciliação não reprocessa eventos já aplicados. O espelho se corrige no
próximo evento da assinatura, porque a sincronização relê o estado atual no
gateway. Se uma divergência persistir, use a sincronização manual descrita
abaixo.

## Diagnosticar um evento

No admin, em **Eventos de cobrança**, a caixa de entrada é só leitura. Os campos
que importam:

- **Processado em** vazio: o evento ainda não foi aplicado.
- **Tentativas** e **Último erro**: por que o processamento falhou. Erro
  temporário do gateway é repetido com espera crescente; os demais ficam
  pendentes até a reconciliação.
- Evento sem assinatura (por exemplo, um checkout expirado) é marcado como
  processado sem alterar nada.

Quando um evento atinge `BILLING_EVENT_MAX_ATTEMPTS`, a reconciliação para de
reenfileirá-lo e registra um erro no log do app, uma vez por execução. Em
produção, o Sentry captura esse erro. Esse caso pede investigação manual.

## Verificar a assinatura de uma organização

No admin, em **Assinaturas**, o staff vê uma organização por vez. Escolha a
organização no filtro **Organização** da listagem; a escolha fica na sessão para
as telas de detalhe. Sem filtro nem escolha anterior, é usada a primeira
organização cadastrada. O espelho é só leitura: status, plano, plano agendado,
período e data de sincronização. Nenhum registro é criado, alterado ou apagado
por ali; a escrita vem só da sincronização.

Se uma assinatura parecer desatualizada, confirme primeiro que os eventos
correspondentes estão aplicados em **Eventos de cobrança**. Para forçar a
sincronização de uma organização, use `make shell`:

```python
from apps.billing.subscriptions import Subscriptions

Subscriptions.sync(ID_DA_ORGANIZACAO)
```

A sincronização lê o estado atual no gateway e sobrescreve o espelho. Ela não
reprocessa eventos.

## Organização encerrada

Ao encerrar uma organização, a cobrança cancela a assinatura paga **imediatamente**
no gateway, depois do commit do encerramento, pela tarefa
`billing.cancel_closed_organization`. A chave de idempotência é fixa por
organização, então repetir a tarefa não cancela duas vezes. Enquanto a
organização estiver encerrada, a sincronização não reativa vínculos suspensos
pelo limite do plano.

Essa tarefa só repete automaticamente uma falha **temporária** do gateway, até 5
vezes, com espera exponencial de 30 segundos, limitada a 15 minutos. Se o
`.delay` nunca chegou ao broker (broker fora no commit), a tarefa nem chega a
existir, e portanto não há log de worker para esse caso. Duas situações ficam
sem retentativa da própria tarefa:

- uma recusa permanente do gateway (`CheckoutError` não temporário): a tarefa
  falha, sem nova tentativa;
- o esgotamento das 5 tentativas.

Em todos esses casos a assinatura paga continua ativa no gateway e o espelho não
muda. A reconciliação (`reconcile_billing`, a cada 15 minutos) cobre isso: ela
procura as organizações encerradas que ainda têm assinatura vinculada ao gateway
e reenfileira o cancelamento, registrando no log a quantidade reenfileirada.
Assim, uma falha ou um enfileiramento perdido é recuperado em até 15 minutos,
desde que o gateway volte a aceitar o cancelamento. Uma recusa permanente
persistente reaparece a cada ciclo e pede investigação.

Um checkout concluído **depois** do encerramento (o dono iniciou o pagamento
antes) também não gera cobrança: ao processar o evento, a assinatura remota que
dá acesso é cancelada já, com a mesma chave `billing:close:<id>`, e o espelho
continua no plano gratuito.

O que fazer se o cancelamento continuar falhando:

1. Veja nos logs do worker a falha de `billing.cancel_closed_organization` da
   organização, e confira no admin, em **Assinaturas**, que o espelho ainda está
   com o plano pago.
2. Cancele a assinatura imediatamente no painel do Stripe.
3. O evento de cancelamento chega pelo webhook e devolve o espelho ao gratuito.
   Se o webhook não estiver recebendo eventos, rode a sincronização manual
   descrita acima.
