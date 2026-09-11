# Operar assinaturas e o rollout comercial

Este procedimento inicializa contratos para organizações criadas antes da
política comercial e verifica o estado necessário para ativar o middleware.
Execute os comandos com a credencial normal da aplicação. A leitura global usa
um selector `SECURITY INVOKER`, com `search_path` fixo e privilégios mínimos;
ele consulta cada tenant sob sua própria policy `FORCE RLS` e restaura o
contexto anterior. O comando entra no contexto RLS de cada organização antes
de criar o contrato.

O projeto instala a release publicada
`django-checkouts[stripe]==1.0.1`. O lockfile de entrega deve apontar para a
PyPI, nunca para um path local ou revisão VCS.

## Fazer o rollout

Antes da migration de faturamento, o administrador do banco deve
pré-provisionar duas roles `NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE
NOCREATEDB NOINHERIT NOREPLICATION`, ambas sem roles concedidas a elas:
`billing_functions_owner`, dona exclusiva das interfaces `SECURITY DEFINER`, e
`billing_ingress_runtime`, interface operacional do worker. A migration não
executa `CREATE ROLE`, portanto o contrato funciona em PostgreSQL gerenciado.

Use uma terceira credencial, exclusiva para DDL, em
`BILLING_MIGRATION_DATABASE_USER`. Ela deve ser diferente dos usuários web e
worker e receber `billing_functions_owner` com `SET OPTION`; em PostgreSQL 16+
o provisionamento equivalente é:

```sql
GRANT billing_functions_owner TO app_migrator WITH SET TRUE;
GRANT billing_ingress_runtime TO app_billing_worker WITH SET TRUE;
```

`app_migrator` continua precisando das permissões DDL usuais para o schema da
aplicação. Ela é um principal administrativo confiável: já pode alterar o
schema e substituir funções, portanto não há isolamento útil contra DML a ser
obtido com grants adicionais. Mantenha sua credencial fora dos processos da
aplicação, injete-a somente durante a janela de migration e revogue ou rotacione
o segredo ao final.

Não conceda `billing_functions_owner` a web ou worker, nem
`billing_ingress_runtime` ao web. A role owner recebe apenas os
privilégios internos necessários às duas funções e às policies dedicadas. A
role runtime recebe `USAGE` no schema e `EXECUTE` nas interfaces estreitas
`faturamento_receber_evento` e `faturamento_rotear_evento`, sem grants em
tabelas, colunas ou sequências. O worker faz `SET ROLE
billing_ingress_runtime`; a função executa como `billing_functions_owner`.

Configure `BILLING_DATABASE_MODE=web`, `ingress` ou `migration` em cada
processo. O check de deploy prova atributos, memberships transitivas, owner das
funções e roles das policies: web não pode assumir nenhuma role e ingress pode
assumir runtime, mas nunca owner. No modo migration o check exige acesso ao
owner e trata a credencial como a trust boundary administrativa. Use o alias
explícito e nunca substitua automaticamente `DATABASE_USER`:

```bash
make billing-migrate
BILLING_DATABASE_MODE=web uv run python manage.py check --deploy --tag database
BILLING_DATABASE_MODE=ingress uv run python manage.py check --deploy --tag database
```

Não coloque senhas na linha de comando; injete
`BILLING_MIGRATION_DATABASE_PASSWORD` pelo gerenciador de segredos. O tipo
normalizado de evento segue a gramática
`^[a-z][a-z0-9]*([._][a-z0-9]+)*$`, com no máximo 100 caracteres. E-mail,
uppercase, whitespace, controles, hífen e segmentos vazios são rejeitados no
Python, na constraint SQL e na interface de ingresso.

Faça backup do banco e aplique primeiro as migrations. Antes de colocar
instâncias da nova aplicação em tráfego, execute na mesma versão de código:

```bash
make billing-migrate
uv run python manage.py sync_plans
uv run python manage.py sync_plans --apply
uv run python manage.py initialize_subscriptions --batch-size 100
uv run python manage.py initialize_subscriptions --apply --batch-size 100
uv run python manage.py check --deploy --tag database
```

O primeiro comando de cada par é um dry-run. Revise sua saída antes do
respectivo `--apply`. `initialize_subscriptions` considera somente
organizações ativas sem contrato corrente, não altera contratos existentes e
cria o plano gratuito pelo caso de uso nominal. O processamento é paginado e
idempotente: se houver interrupção, corrija a causa e repita o mesmo comando.

O check de deploy `assinaturas.E003` usa uma única consulta e precisa retornar
zero organizações ativas sem assinatura. Não habilite tráfego na nova versão
enquanto ele falhar. O middleware responde
`503 billing.subscription_required` se encontrar essa lacuna em runtime.

## Configurar o Stripe e o webhook

Injete `STRIPE_API_KEY` e `STRIPE_WEBHOOK_SECRET` pelo gerenciador de segredos.
Use `STRIPE_SANDBOX=True` somente com chave de teste/sandbox; em produção use
`False`. `BILLING_CHECKOUT_SUCCESS_URL` e `BILLING_CHECKOUT_CANCEL_URL` precisam
ser HTTPS em produção. Não registre os valores dessas variáveis em arquivos,
logs, tickets ou relatórios.

Cadastre no Stripe o endpoint público:

```text
POST https://api.example.com/faturamento/webhooks/stripe/
```

O proxy deve encaminhar o corpo sem decodificar, reconstruir ou normalizar JSON:
a verificação usa os bytes originais e o header `Stripe-Signature`. O endpoint
não usa sessão, mas falha fechado para assinatura inválida. Nunca habilite log
de corpo ou headers nessa rota. O template persiste somente fatos normalizados
em allowlist e um hash canônico; o payload bruto e o `raw` da biblioteca são
descartados.

Antes de liberar checkout pago, rode `python manage.py check --deploy` em cada
modo de processo. O modo web não pode assumir nenhuma role financeira; o worker
de ingresso pode assumir apenas `billing_ingress_runtime`; migrations usam a
credencial DDL efêmera. Uma configuração sem as duas credenciais Stripe falha
com `django_checkouts.E001`/`faturamento.E001` e não deve receber tráfego.

## Verificar jobs e acesso

O Celery Beat agenda dois jobs locais:

- `assinaturas.encerrar_trials_vencidos`, a cada hora no minuto 5;
- `assinaturas.reconciliar_carencias_seats`, a cada 15 minutos.

`SUBSCRIPTION_TASK_BATCH_SIZE` define o lote de leitura, com padrão `100`.
O selector devolve no máximo esse número de candidatos elegíveis por execução;
os jobs então entram em RLS por organização, usam locks na ordem organização →
assinatura, revalidam o estado sob lock e só depois calculam a ocupação. Eles
registram somente duração e contagem. É seguro reexecutá-los: um trial já
convertido e uma carência já coerente não ganham outra revisão.

O faturamento acrescenta estes jobs:

- `faturamento.recuperar_eventos_cobranca`, a cada 5 minutos, reivindica no
  máximo 100 eventos abandonados ou cujo retry venceu;
- `faturamento.reconciliar_eventos_stripe`, a cada 15 minutos, percorre uma
  janela de 20 minutos com 5 minutos de sobreposição e deduplica pelo ID remoto.

O retry automático usa backoff exponencial, começa em 30 segundos, respeita o
`retry_after` normalizado quando presente e para depois de oito tentativas no
ciclo. Não altere status financeiro manualmente. Falha inconclusiva fica em
`billing.checkout_uncertain` ou `RECONCILE_FIRST` até uma consulta remota
conclusiva.

Depois do rollout, valide com uma sessão de administrador:

```text
GET /assinatura/                 # inclui situacao_acesso, motivos e regularizar_ate
GET /assinatura/utilizacao-seats/
```

Durante uma carência, a API continua acessível e informa a causa e o menor
prazo. Após o prazo, rotas comuns respondem
`403 billing.organization_restricted`; somente proprietário ou administrador
alcançam ações marcadas de consulta ou regularização. O marcador é declarativo
na view, não uma lista textual de URLs.

## Investigar e recuperar faturamento

Use o Django Admin sempre com uma organização selecionada. Checkouts, faturas,
mapeamentos de assinatura e eventos são somente leitura; URLs, payload
normalizado, hash e erro interno ficam ocultos. `ReferenciaPrecoGateway` é o
cadastro separado para IDs de preço quando uma variante não aceitar preço
inline. Operadores recebem permissões separadas:

- `faturamento.retry_failed_eventocobranca` reabre um evento terminal falho e
  zera somente o contador do novo ciclo automático;
- `faturamento.reconcile_eventocobranca` agenda uma janela de reconciliação
  ligada ao evento selecionado.

As duas actions exigem motivo, ator e chave idempotente e criam registros de
auditoria tenantizados. Repetir uma action não duplica o pedido. Prefira a
reconciliação quando houve timeout ou outra falha externa inconclusiva; retry
cego pode duplicar uma mutação remota.

Monitore as métricas de checkout, webhook, retry, processamento, recuperação e
reconciliação por variante e resultado. Os labels são conjuntos finitos e não
incluem IDs, e-mails, URLs ou mensagens externas. Um aumento de eventos não
roteados indica referência assinada/assinatura remota ainda desconhecida; a
recuperação periódica tenta novamente depois que o mapeamento existir.

## Validar sem credenciais e executar o smoke sandbox

O gate determinístico deste template usa `FakeCheckoutGateway` pela interface
pública de `django-checkouts` e não toca a rede:

```bash
uv run --group test pytest apps/assinaturas/subapps/faturamento \
  apps/assinaturas/tests/test_tenant_access_middleware.py
```

O smoke ao vivo pertence à fronteira da biblioteca e é deliberadamente
separado do gate de cobertura. Com uma credencial de sandbox disponível no
gerenciador de segredos, execute a release publicada em um ambiente isolado; o
roteiro cria, recupera e expira um checkout de R$ 1,00 sem imprimir IDs ou a
chave:

```bash
DJC_STRIPE_API_KEY="$(secret-manager read stripe-sandbox-api-key)" \
uv run --no-project --with 'django-checkouts[stripe]==1.0.1' python - <<'PY'
import os
from uuid import uuid4

from django_checkouts.client import CheckoutClient
from django_checkouts.gateways.stripe import StripeGateway
from django_checkouts.types import CheckoutCreate, CheckoutItem, InlinePrice

key = f"smoke:{uuid4()}"
client = CheckoutClient(
    StripeGateway(api_key=os.environ["DJC_STRIPE_API_KEY"], sandbox=True)
)
created = client.checkouts.create(
    CheckoutCreate(
        items=(CheckoutItem(price=InlinePrice(name="Smoke", unit_amount=100)),),
        success_url="https://example.com/smoke/success",
        cancel_url="https://example.com/smoke/cancel",
        reference_id=key,
    ),
    idempotency_key=f"{key}:create",
)
retrieved = client.checkouts.retrieve(created.external_id)
assert retrieved.external_id == created.external_id
client.checkouts.cancel(created.external_id, idempotency_key=f"{key}:cancel")
print("stripe sandbox smoke: ok")
PY
```

`secret-manager read ...` é um placeholder para o CLI aprovado pela sua
organização; não cole a chave no histórico do shell. Sem
`DJC_STRIPE_API_KEY`, registre o smoke como ignorado por ausência de credencial,
nunca como aprovado e nunca substitua a chave por um valor inventado.

## Reverter com segurança

Se a falha ocorrer antes do `--apply`, não há escrita para desfazer. Se ocorrer
durante a inicialização, preserve os contratos já criados e repita o comando;
apagá-los criaria uma janela de indisponibilidade e não é necessário.

Para rollback da aplicação, retire a versão nova de tráfego e volte o código
mantendo as migrations aplicadas. O schema e os contratos gratuitos são
compatíveis como dados históricos. Pause os dois schedules comerciais se o
código anterior não registrar essas tasks.

Só reverta as migrations `0012`, `0011` e `0010`, nessa ordem, em uma
manutenção separada, depois de
confirmar que não existem alterações `FALLBACK_TRIAL` nem transições que
dependam das novas proteções. A reversão de `0012` remove o selector e restaura
a guarda da `0011`; a reversão de `0011` remove os triggers novos e restaura o
trigger contratual anterior. Fazê-las enquanto workers ou tráfego continuam
ativos reabre uma janela de bypass e não é um rollback seguro.
