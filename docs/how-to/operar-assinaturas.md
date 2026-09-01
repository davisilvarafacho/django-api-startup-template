# Operar assinaturas e o rollout comercial

Este procedimento inicializa contratos para organizações criadas antes da
política comercial e verifica o estado necessário para ativar o middleware.
Execute os comandos com a credencial normal da aplicação. A leitura global usa
um selector `SECURITY INVOKER`, com `search_path` fixo e privilégios mínimos;
ele consulta cada tenant sob sua própria policy `FORCE RLS` e restaura o
contexto anterior. O comando entra no contexto RLS de cada organização antes
de criar o contrato.

## Fazer o rollout

Antes da migration de faturamento, o administrador do cluster deve
pré-provisionar `billing_ingress_runtime` como `NOLOGIN NOSUPERUSER
NOBYPASSRLS`. A migration não cria roles nem exige `CREATEROLE`, o que a torna
compatível com PostgreSQL gerenciado. Conceda membership dessa role somente ao
`DATABASE_USER` usado pelo worker que recebe webhooks. Esse worker deve executar
`SET ROLE billing_ingress_runtime` antes do ingresso; processos web comuns não
devem receber a membership. A migration concede à role apenas os privilégios
nos objetos de faturamento necessários e o check `faturamento.E003` valida os
atributos e a membership. `BILLING_INGRESS_DATABASE_ROLE` documenta o nome
contratual e deve permanecer `billing_ingress_runtime`.

Faça backup do banco e aplique primeiro as migrations. Antes de colocar
instâncias da nova aplicação em tráfego, execute na mesma versão de código:

```bash
uv run python manage.py migrate
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
