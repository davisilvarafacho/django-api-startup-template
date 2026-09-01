# Operar assinaturas e o rollout comercial

Este procedimento inicializa contratos para organizações criadas antes da
política comercial e verifica o estado necessário para ativar o middleware.
Execute os comandos com a credencial normal da aplicação. A leitura global usa
um selector `SECURITY INVOKER`, com `search_path` fixo e privilégios mínimos;
ele consulta cada tenant sob sua própria policy `FORCE RLS` e restaura o
contexto anterior. O comando entra no contexto RLS de cada organização antes
de criar o contrato.

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
aplicação; a membership acima acrescenta apenas a capacidade limitada de
transferir ownership para a role NOLOGIN.

Não conceda `billing_functions_owner` a web ou worker, nem
`billing_ingress_runtime` a web ou migrator. A role owner recebe apenas os
privilégios internos necessários às duas funções e às policies dedicadas. A
role runtime recebe `USAGE` no schema e `EXECUTE` nas interfaces estreitas
`faturamento_receber_evento` e `faturamento_rotear_evento`, sem grants em
tabelas, colunas ou sequências. O worker faz `SET ROLE
billing_ingress_runtime`; a função executa como `billing_functions_owner`.

Configure `BILLING_DATABASE_MODE=web`, `ingress` ou `migration` em cada
processo. O check de deploy prova atributos, memberships transitivas, owner das
funções e roles das policies: web não pode assumir nenhuma role, ingress pode
assumir somente runtime, e migration pode assumir somente owner. Use o alias
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

Os dois primeiros comandos comerciais são dry-runs. Revise suas saídas antes
dos respectivos `--apply`. `initialize_subscriptions` considera somente
organizações ativas sem contrato corrente, não altera contratos existentes e
cria o plano gratuito pelo caso de uso nominal. O processamento é paginado e
idempotente: se houver interrupção, corrija a causa e repita o mesmo comando.

O check de deploy `assinaturas.E003` usa uma única consulta e precisa retornar
zero organizações ativas sem assinatura. Não habilite tráfego na nova versão
enquanto ele falhar. O middleware responde
`503 billing.subscription_required` se encontrar essa lacuna em runtime.

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
