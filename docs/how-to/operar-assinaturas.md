# Operar assinaturas e o rollout comercial

Este procedimento inicializa contratos para organizações criadas antes da
política comercial e verifica o estado necessário para ativar o middleware.
Execute `sync_plans` e `initialize_subscriptions` com a credencial normal da
aplicação. `make billing-migrate` é a exceção: usa somente o alias
`billing_migration` e a credencial DDL dedicada descrita abaixo. A leitura
global usa um selector `SECURITY INVOKER`, com `search_path` fixo e privilégios
mínimos; ele consulta cada tenant sob sua própria policy `FORCE RLS` e restaura
o contexto anterior. O comando entra no contexto RLS de cada organização antes
de criar o contrato.

O projeto instala a release publicada
`django-checkouts[stripe]==1.0.1`. O lockfile de entrega deve apontar para a
PyPI, nunca para um path local ou revisão VCS.

## Fazer o rollout

Antes da migration de faturamento, o administrador do banco deve
pré-provisionar duas roles `NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE
NOCREATEDB NOINHERIT NOREPLICATION`, ambas sem roles concedidas a elas:
`billing_functions_owner`, dona exclusiva das interfaces `SECURITY DEFINER`, e
`billing_ingress_runtime`, interface operacional do ingresso. A migration não
executa `CREATE ROLE`, portanto o contrato funciona em PostgreSQL gerenciado.

Separe quatro logins, todos distintos entre si e das duas roles `NOLOGIN`:

- `DATABASE_USER` para web, MCP, Beat e o worker geral;
- `BILLING_INGRESS_DATABASE_USER` somente para o processo HTTP de webhook;
- `BILLING_INGRESS_WORKER_DATABASE_USER` para o worker da fila global;
- `BILLING_MIGRATION_DATABASE_USER` somente para DDL.

O login de migration recebe `billing_functions_owner` com `SET OPTION`; os
dois logins de ingresso recebem apenas `billing_ingress_runtime`. Em PostgreSQL
16+, o provisionamento equivalente é:

```sql
GRANT billing_functions_owner TO app_migrator WITH SET TRUE;
GRANT billing_ingress_runtime TO app_billing_ingress_http WITH SET TRUE;
GRANT billing_ingress_runtime TO app_billing_ingress_worker WITH SET TRUE;
```

`app_migrator` continua precisando das permissões DDL usuais para o schema da
aplicação. Ela é um principal administrativo confiável: já pode alterar o
schema e substituir funções, portanto não há isolamento útil contra DML a ser
obtido com grants adicionais. Mantenha sua credencial fora dos processos da
aplicação, injete-a somente durante a janela de migration e revogue ou rotacione
o segredo ao final.

Não conceda `billing_functions_owner` ao web ou a workers, nem
`billing_ingress_runtime` ao web. Trocar somente
`BILLING_DATABASE_MODE=ingress` em um processo que ainda usa `DATABASE_USER`
não cria isolamento e não é um procedimento de deploy válido. A role owner
recebe apenas os privilégios internos necessários às quatro funções e às
policies dedicadas. A role runtime recebe `USAGE` no schema, `EXECUTE` nas
interfaces abaixo e `SELECT` somente nas colunas necessárias para resolver
destino em `assinatura_gateway` e `checkout_cobranca`; ela não lê URL, valores
ou outros campos financeiros e não recebe escrita direta em tabela ou
sequência:

- `faturamento_ingress_evento(text,text,text,text,text,text,boolean,jsonb,text,timestamptz)`;
- `faturamento_rotear_evento_destino(bigint,bigint)`;
- `faturamento_claim_recovery(integer,timestamptz)`;
- `faturamento_destino_reconcile(bigint)`.

Todas pertencem a `billing_functions_owner`, fixam
`search_path=pg_catalog,pg_temp` e revogam `EXECUTE` de `PUBLIC`. O worker faz
`SET ROLE billing_ingress_runtime`; a função executa como
`billing_functions_owner`. Os nomes antigos `faturamento_receber_evento` e
`faturamento_rotear_evento` não pertencem ao catálogo atual.

Configure `BILLING_DATABASE_MODE=web`, `ingress` ou `migration` em cada
processo. O check de deploy prova atributos, memberships transitivas, owner das
quatro funções e o conjunto exato de policies por tabela: web não pode assumir
nenhuma role e ingresso pode assumir runtime, mas nunca owner. No modo
migration o check exige acesso ao owner e trata a credencial como a trust
boundary administrativa. Use o alias explícito e execute o check dentro de
cada processo real, para que cada comando use o respectivo login dedicado:

```bash
make billing-migrate
docker compose exec -T web python manage.py check --deploy --tag database
docker compose exec -T billing_ingress python manage.py check --deploy --tag database
docker compose exec -T billing_ingress_worker python manage.py check --deploy --tag database
```

Na stack Compose, `web`, MCP, Beat e o worker da fila `celery` usam
`DATABASE_USER`; o HTTP `billing_ingress` usa
`BILLING_INGRESS_DATABASE_USER`, enquanto o worker da
fila `billing_ingress` usa `BILLING_INGRESS_WORKER_DATABASE_USER`. O Nginx é o único
serviço publicado e envia exclusivamente o prefixo
`/faturamento/webhooks/` ao HTTP ingress. As variáveis da credencial ingress e
da credencial DDL, assim como as credenciais administrativas locais, são
sobrescritas com vazio nos processos da aplicação. O login HTTP recebe somente
`CONNECT`, `USAGE` e membership na role runtime; ele não tem grants diretos de
tabela. O login do worker recebe os grants ORM usuais limitados por RLS,
necessários às tasks tenantizadas de reconciliação, mas sua única membership
também é `billing_ingress_runtime`. O script em
`docker/postgres/init-billing-roles.sh` roda automaticamente pelo entrypoint
somente quando o PostgreSQL inicializa um volume vazio. Para reaplicá-lo com
segurança em um volume local existente:

1. faça um backup e teste sua restauração;
2. em `.env`, configure `POSTGRES_ADMIN_USER` com o login administrativo que
   criou o volume e mantenha os três logins permanentes da aplicação distintos
   dele e entre si;
3. execute `make billing-bootstrap` antes de `make billing-migrate`.

Mudar `POSTGRES_ADMIN_USER` no Compose não renomeia o principal já gravado no
volume. O alvo reconcilia o container `db` com a configuração atual, aguarda o
healthcheck e executa o script montado dentro dele; as senhas vêm do ambiente do
container e não da linha de comando. Criações condicionais e grants repetíveis
tornam segura a reexecução do bootstrap. Localmente, o administrador também é
a credencial efêmera de migration e nunca é injetado nos processos web ou
workers. O Compose carrega `.env`, mas o Make não o importa: se o administrador
do volume não for o default local, exporte `POSTGRES_ADMIN_USER` e
`POSTGRES_ADMIN_PASSWORD` no ambiente do `make` a partir do gerenciador de
segredos antes de executar a migration. Para o volume do Dev Container, rode no
host:

```bash
make billing-bootstrap BILLING_COMPOSE_FILE=.devcontainer/docker-compose.yml
```

O alvo é exclusivo das stacks locais. Em banco gerenciado, faça backup e
provisione as duas roles e os quatro logins pelo fluxo administrativo da
plataforma; não copie credenciais locais nem execute o container de bootstrap
contra produção.

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

Antes de liberar checkout pago, rode o check de deploy nos containers web, HTTP
ingress e ingress worker, como mostrado acima. O web não pode assumir nenhuma
role financeira; os dois processos de ingresso podem assumir apenas
`billing_ingress_runtime`, cada um com seu próprio login; migrations usam a
credencial DDL efêmera. Uma configuração sem as duas credenciais Stripe falha
fechado em produção com `django_checkouts.E001`/`faturamento.E001` e não deve
receber tráfego. Em desenvolvimento, as credenciais podem ficar vazias para o
quickstart e as rotas que instanciam o gateway continuam indisponíveis; formato
inválido ainda falha. O ambiente de teste silencia também o check local para
manter migrations e testes determinísticos sem segredos.

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

Outras três tasks financeiras não pertencem ao Beat:
`faturamento.processar_evento_cobranca`,
`faturamento.reconciliar_evento_cobranca` e
`faturamento.executar_reconciliacao_operacional`. Elas são disparadas pela
ingestão, pelo recovery ou por uma solicitação operacional auditada. O recovery
agendado usa lote padrão de 100; a função SQL aceita explicitamente limites de
1 a 1.000 para invocações operacionais controladas.

O retry automático usa backoff exponencial, começa em 30 segundos, respeita o
`retry_after` normalizado quando presente e para depois de oito tentativas no
ciclo. Não altere status financeiro manualmente. Falha inconclusiva fica em
`billing.checkout_uncertain` ou `RECONCILE_FIRST` até uma consulta remota
conclusiva.

As tasks globais `recuperar_eventos_cobranca`,
`reconciliar_eventos_stripe`, `reconciliar_evento_cobranca` e
`executar_reconciliacao_operacional` são roteadas para `billing_ingress` e só o
worker com o login ingress consome essa fila. `processar_evento_cobranca` já
recebe um tenant autenticado, usa o ORM financeiro tenantizado e permanece na
fila `celery`; o worker geral consome explicitamente apenas essa fila. Não
inicie workers sem `-Q`, pois isso desfaz a separação operacional.

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

O rollback preferido é retirar a versão nova de tráfego e voltar o código
mantendo o schema aplicado, mas somente quando a versão anterior tiver sido
validada contra esse schema. Pause os quatro schedules comerciais —
`assinaturas.encerrar_trials_vencidos`,
`assinaturas.reconciliar_carencias_seats`,
`faturamento.recuperar_eventos_cobranca` e
`faturamento.reconciliar_eventos_stripe` — se a versão anterior não registrar
suas tasks. Antes de qualquer rollback de schema, retire do Nginx o HTTP
`billing_ingress`, drene e pare o worker da fila `billing_ingress`, e drene na
fila `celery` os `faturamento.processar_evento_cobranca` já publicados. Só
então pare Beat e os demais processos que poderiam escrever.

Não use uma sequência de números de migration sem o app e não remova
manualmente funções, triggers ou policies. Primeiro faça backup, teste a
restauração e inspecione o grafo efetivamente aplicado com
`uv run python manage.py showmigrations assinaturas faturamento --plan`,
e registre cada alvo como `app_label.nome_completo`, obtido do artefato exato
que será restaurado. Não copie um número isolado nem presuma que a migration
folha é igual entre releases. Execute qualquer reversão somente com
`BILLING_DATABASE_MODE=migration`, `--database=billing_migration`, o app label
e o nome completo aprovados no plano.

Rollback de banco é uma manutenção separada e potencialmente destrutiva.
Reverter a migration que instala a leitura mínima de resolução revoga as
colunas necessárias ao HTTP ingress e torna webhook conhecido indisponível;
por isso o processo deve estar fora de tráfego primeiro. A reversão de
`faturamento.0011_fatos_invoice_e_lease_recovery` converte fatos
financeiros indisponíveis (`NULL`) em zero e remove o lease de recovery;
`faturamento.0009_reconcile_first_conclusivo` remove auditorias automáticas; e
`faturamento.0008_fronteira_recovery_e_rls_auditoria` desativa o RLS da tabela
de reaberturas. Antes de aprovar qualquer alvo, confira esses impactos nos
dados, gere o plano da versão a restaurar em homologação e valide depois as
quatro funções, ACLs, policies `FORCE RLS` e o `check --deploy --tag database`.
