# Cache de permissões — Design

## Objetivo

Adicionar um cache compartilhado de fatos de autorização sem alterar a semântica
das APIs existentes. A solução deve reduzir consultas repetidas, preservar
`user.has_perm()` e funcionar na API, no Django admin, em tasks Celery,
management commands e chamadas diretas de domínio.

O cache cobre:

- permissões globais do Django, diretas e herdadas de grupos;
- vínculo e papel por organização;
- permissões por objeto do `django-guardian`, diretas e por grupo;
- fatos estáveis usados pelos predicates do `django-rules`.

O Redis nunca será fonte de verdade. Em falha do cache, a autorização será
recalculada no banco.

## Contexto e escolha

O projeto já usa Django Cache Framework, `django-redis`, `django-cachalot`,
`django-guardian` e `django-rules`.

Foram comparadas três estratégias:

1. camada semântica própria sobre Django Cache Framework e `django-redis`;
2. `django-cacheops`, especialmente `cached_as(...)`;
3. somente `django-cachalot`.

A decisão é a primeira. “Própria” significa implementar apenas os conceitos que
as bibliotecas genéricas não conhecem: fatos de autorização, chaves, epochs,
invalidação e métricas. Redis, conexão, serialização, TTL e primitivas de cache
continuam delegados ao Django e ao `django-redis`.

O Cacheops não será adicionado nesta entrega. Ele reduziria parte do plumbing,
mas ainda exigiria declarar todas as dependências de grupos, relações M2M,
Guardian, tenant e rules. Também criaria outro protocolo de cache ORM ao lado do
Cachalot. Ele poderá ser reavaliado se o projeto decidir substituir o Cachalot e
padronizar Cacheops como estratégia geral de ORM.

O Cachalot continuará como cache de queries da aplicação e poderá acelerar
consultas comuns. Ele não será a autoridade sobre a validade dos snapshots de
autorização.

A pesquisa completa está em
[`docs/research/2026-07-29-django-permission-cache-libraries.md`](../../research/2026-07-29-django-permission-cache-libraries.md).

## Escopo

### Incluído

- cache positivo e negativo;
- invalidação por epochs após commit;
- TTL de segurança centralizado em 1.800 segundos;
- alias de cache dedicado;
- backends compatíveis com Django e Guardian;
- snapshot imutável de acesso ao tenant;
- integração dos predicates de papel com o resolvedor de tenant;
- fallback ao banco;
- métricas Prometheus e logs estruturados;
- comando operacional de invalidação global;
- contrato executável para mutações em massa;
- testes com PostgreSQL e Redis reais.

### Fora do escopo

- cachear automaticamente o booleano final de predicates arbitrários;
- cache dos scopes de API token, já avaliados em memória a partir do
  `TokenMetaData`;
- cache de entitlements de checkout/planos;
- field-level permissions;
- outbox persistente para invalidações;
- migração do Cachalot para Cacheops;
- cancelamento de requests que já estavam em andamento durante uma revogação.

O cache de entitlements será desenhado após o modelo de checkout/planos e foi
registrado separadamente no roadmap.

## Organização do código

O núcleo ficará em um pacote Python comum na raiz, sem ser um Django app:

```text
common/
└── permission_cache/
    ├── __init__.py
    ├── config.py
    ├── store.py
    ├── keys.py
    ├── epochs.py
    ├── types.py
    ├── backends.py
    ├── metrics.py
    ├── resolvers/
    │   ├── __init__.py
    │   ├── django.py
    │   ├── guardian.py
    │   └── tenant.py
    ├── signals/
    │   ├── __init__.py
    │   ├── django.py
    │   ├── guardian.py
    │   └── tenant.py
    └── tests/
```

O pacote não terá `AppConfig`, models nem migrations.

- O `AppConfig.ready()` de autenticação carregará os signals de Django e
  Guardian.
- O `AppConfig.ready()` de organizações carregará os signals de tenant.
- O management command ficará em
  `apps/api/core/management/commands/invalidate_permission_cache.py`, pois
  commands só são descobertos dentro de apps instalados.
- Autenticação e organizações consumirão as interfaces de
  `common.permission_cache`; não haverá um novo item em `INSTALLED_APPS`.

## Configuração

A configuração será centralizada:

```python
AUTHORIZATION_CACHE = {
    "ENABLED": True,
    "ALIAS": "permissions",
    "TIMEOUT": 1800,
    "KEY_PREFIX": "authz:v1",
}
```

O TTL de 1.800 segundos é o padrão de todas as camadas. A estrutura permite
overrides futuros por camada, mas esta entrega não introduzirá valores
diferentes.

Com `ENABLED=False`, os mesmos backends e resolvedores consultarão o banco sem
ler ou escrever cache. Esse kill switch permite comparar comportamento, operar
incidentes e testar equivalência sem trocar `AUTHENTICATION_BACKENDS`.

Em produção, `CACHES["permissions"]` usará `django-redis` e um URL configurável,
com fallback para o database lógico `/4`. O projeto já usa:

- `/0` para o broker Celery;
- `/1` para o cache padrão;
- `/2` para resultados Celery;
- `/3` para o Cachalot;
- `/4` para o cache de autorização.

O alias `permissions` não habilitará `IGNORE_EXCEPTIONS`. O store capturará
falhas de leitura e escrita deliberadamente, enquanto falhas de invalidação
precisam permanecer observáveis.

`KEY_PREFIX`, ou o `KEY_PREFIX` do próprio alias Django, identificará também o
ambiente/deployment. Duas instalações que compartilham Redis não poderão usar o
mesmo prefixo.

Em testes unitários, o alias poderá usar `LocMemCache`. Testes de atomicidade,
visibilidade entre processos e queda/retorno usarão Redis real.

Todos os processos usarão o Redis primário para epochs e snapshots. Réplicas de
leitura não são permitidas nesse alias porque atraso de replicação poderia
devolver uma versão anterior.

Databases lógicos evitam colisão de chaves, mas continuam compartilhando
memória, rede e disponibilidade do mesmo servidor. Uma futura migração para
Redis Cluster, que normalmente usa apenas DB 0, deverá preservar o isolamento
com prefixes ou instâncias separadas.

## Contratos de dados

Valores armazenados serão pequenos, imutáveis e compostos somente de tipos
primitivos versionados. Models Django nunca serão serializados no Redis.

### Envelope

Todo valor terá versão de schema e distinção explícita entre miss e resultado
negativo. `None` não representará simultaneamente os dois estados.

Exemplos conceituais:

```python
{"schema": 1, "found": False}
{"schema": 1, "found": True, "permissions": ["app.change_item"]}
```

Um payload ausente é miss. Um envelope com `found=False` é um hit negativo.
Payload inválido ou de versão desconhecida é tratado como miss e contabilizado
como erro de decode.

### Permissões globais

O snapshot separará listas ordenadas de strings `app_label.codename` em:

- permissions diretas do usuário;
- permissions herdadas de grupos.

O conjunto total será a união das duas listas. Essa separação preserva
`get_user_permissions()`, `get_group_permissions()` e `get_all_permissions()`
sem consultas adicionais. As listas serão convertidas para `frozenset` em
memória.

### Guardian

O snapshot será o conjunto resolvido de codenames para a combinação:

- deployment/key prefix;
- database alias;
- usuário;
- content type/modelo;
- PK canônica do objeto.

Objetos sem PK não serão cacheados.

### Tenant

O resolvedor devolverá uma dataclass imutável:

```python
TenantAccess(
    organization_id=10,
    organization_slug="acme",
    membership_id=27,
    role=30,
)
```

O Redis armazenará um dicionário primitivo equivalente, nunca a dataclass
picklada nem instâncias de `Organizacao` ou `Vinculo`.

Haverá duas formas de acesso ao mesmo fato:

- por `(user_id, organization_slug)`, usada na resolução da request;
- por `(user_id, organization_id)`, usada pelos predicates de rules.

Um carregamento bem-sucedido poderá preencher os dois índices.

## Epochs e composição de chaves

Epoch é um contador inteiro compartilhado no Redis. Uma chave de snapshot inclui
os epochs relevantes. Invalidar significa incrementar um contador
atomicamente; valores da versão anterior permanecem fisicamente no Redis, mas
ficam inalcançáveis e expiram pelo TTL.

Exemplo conceitual, incluindo database alias:

```text
authz:v1:epoch:django:user:42 = 7
authz:v1:snapshot:django:default:g0:l3:u42:e7 = {...}
```

Quando o epoch do usuário muda para 8, novos leitores usam outra chave. Nenhum
processo escolhe versões manualmente.

Um epoch ausente será inicializado com um seed inteiro aleatório de alta
entropia usando `add`/`SET NX`; increments posteriores serão atômicos. Não se
reinicia em zero: se o Redis expulsar somente a chave do epoch e preservar um
snapshot antigo, um novo seed não poderá voltar a alcançar aquela versão.

Epochs não terão TTL. Snapshots terão. Uma política de eviction `volatile-*` é
preferível, mas a correção não dependerá dela. Inicialização concorrente,
perda isolada do epoch e overflow serão cobertos pelos testes com Redis real.

### Escopos

- `global`: invalidação operacional de emergência para todas as camadas;
- `django:global`: alterações que afetam muitos usuários, como permissions de
  grupo;
- `django:user:<id>`: permissions diretas, grupos e estado do usuário;
- `tenant:global`: alterações estruturais em organizações;
- `tenant:user:<id>`: vínculos e papéis de um usuário;
- `guardian:global`: alterações estruturais ou de fan-out desconhecido;
- `guardian:user:<id>`: pertencimento a grupos;
- `guardian:object:<db>:<content-type>:<pk>`: concessões diretas ou por grupo
  naquele objeto.

As chaves de snapshot incluem o epoch global, o epoch global da camada e os
epochs específicos do sujeito/objeto necessários. Todas incluem também o
database alias; nenhuma combinação entre bancos compartilhará snapshot.

Mudanças de grupo com fan-out amplo usam epochs globais em vez de enumerar
milhões de usuários ou chaves. A granularidade poderá ser refinada futuramente
com evidência de carga.

## Fluxo de leitura

Cada resolvedor executará cache-aside:

1. normaliza os identificadores;
2. lê em uma única operação os epochs relevantes no Redis primário;
3. monta a chave versionada;
4. procura o envelope;
5. em hit, devolve o fato imutável e registra métrica;
6. em miss, consulta o banco com o Cachalot desabilitado;
7. relê os epochs antes de armazenar;
8. se os epochs não mudaram, grava o envelope com TTL;
9. se mudaram, descarta o resultado para cache e tenta novamente;
10. após um limite pequeno de retries, devolve o resultado atual do banco sem
    cachear, evitando livelock.

Se uma invalidação ocorrer depois da última leitura de epoch, o resultado será
gravado sob a versão antiga e ficará inalcançável para novos leitores. Uma
request que já estava concorrendo com a mutação pode terminar com a decisão
anterior; requests iniciadas depois da invalidação observam o novo epoch.

Cache local por execução só poderá existir associado ao conjunto de epochs que
produziu o valor. Os atributos `_perm_cache`, `_user_perm_cache` e
`_group_perm_cache` do `ModelBackend` não serão reutilizados sem validação de
versão.

Não haverá lock distribuído ou single-flight nesta primeira versão. Duas
requests poderão recompor o mesmo snapshot simultaneamente; isso é seguro e
preferível a bloquear o caminho de autorização. As métricas indicarão se
stampede se tornar um problema real.

## Integração com Django, Guardian e Rules

### Backends

Os backends padrão não ficarão em paralelo com versões cacheadas. Como Django
concede acesso quando qualquer backend retorna verdadeiro, um backend antigo
com valor obsoleto quebraria a revogação.

`AUTHENTICATION_BACKENDS` manterá o backend de rules e substituirá:

- `django.contrib.auth.backends.ModelBackend` por uma extensão compatível que
  usa o resolvedor global;
- `guardian.backends.ObjectPermissionBackend` por uma extensão compatível que
  usa o resolvedor Guardian.

Serão preservados e testados:

- `has_perm()` e `has_perms()`;
- `get_user_permissions()`, `get_group_permissions()` e
  `get_all_permissions()`;
- `has_module_perms()`;
- variantes assíncronas oferecidas pelo Django;
- superusuário, usuário inativo e anônimo;
- semântica OR entre rules e os demais backends;
- validação de app label e comportamento do Guardian.

### Rules

Predicates arbitrários continuarão sendo avaliados. Somente fatos estáveis
internos, como papel na organização, serão obtidos pelos resolvedores.

O predicate `papel_minimo` passará a consultar `TenantAccessResolver` por
`(user_id, organization_id)`. Um predicate futuro só poderá cachear seu
resultado final por opt-in e com dependências explícitas; isso não faz parte
desta entrega.

## Interface de tenant na request

`TenantPermission` deixará de anexar models serializáveis à request como
contrato principal. A interface canônica será:

```python
request.tenant
request.tenant.organization_id
request.tenant.organization_slug
request.tenant.membership_id
request.tenant.role
request.tenant.has_minimum_role(...)
```

Os consumidores atuais serão migrados:

- views filtrarão e salvarão por `organization_id`;
- serializers compararão `request.tenant.role`;
- `PapelMinimoPermission` usará `has_minimum_role`;
- o contexto RLS receberá `organization_id`;
- middleware inicializará `request.tenant = None`.

Essa mudança é interna ao template. Ela evita modelos obsoletos no Redis e deixa
explícito o mínimo necessário para autorização.

## Coexistência com o Cachalot

O cache semântico e o Cachalot são independentes:

- `permissions` usa o database lógico `/4`;
- `cachalot` usa `/3`;
- invalidação de autorização não executa `FLUSHDB` nem apaga chaves do
  Cachalot;
- queries normais de modelos da aplicação continuam elegíveis ao Cachalot.

Em um miss semântico, o banco precisa ser a fonte consultada. Os loaders dos
resolvedores executarão suas queries dentro de:

```python
with cachalot_disabled(all_queries=True):
    ...
```

O contexto é local ao bloco. Ele não desabilita o Cachalot globalmente nem
impede que os mesmos modelos sejam cacheados em outros fluxos.

## Contrato de invalidação

| Evento | Epoch incrementado |
| --- | --- |
| Permission direta adicionada, removida ou limpa do usuário | Django do usuário |
| Usuário adicionado, removido ou limpo de grupos | Django do usuário e Guardian do usuário |
| Permissions de um grupo alteradas ou limpas | Django global |
| Grupo excluído ou alterado de forma estrutural | Django global e Guardian global |
| `is_active` ou `is_superuser` alterado | Epochs daquele usuário em todas as camadas |
| Usuário excluído | Epochs daquele usuário em todas as camadas |
| Vínculo criado ou removido | Tenant dos usuários afetados |
| Papel, estado, usuário ou organização do vínculo alterados | Tenant dos lados anterior e novo |
| Slug, estado ou exclusão da organização | Tenant global |
| Permission Guardian direta criada, alterada ou removida | Guardian do objeto anterior e novo |
| Permission Guardian de grupo criada, alterada ou removida | Guardian do objeto anterior e novo |
| Permission ou ContentType alterado estruturalmente | Epoch global das camadas afetadas |
| `post_migrate` criar ou sincronizar permissions | Django global e Guardian global |

Signals de `pre_save`/`pre_delete` preservarão IDs anteriores quando necessários.
Signals `m2m_changed` tratarão `post_add`, `post_remove` e `post_clear`; no caso
de `clear()`, os sujeitos afetados serão capturados em `pre_clear`.

Incrementos serão agendados com `transaction.on_commit()`. O callback receberá
somente IDs primitivos e tratará sua própria falha, pois a transação do banco já
terá sido confirmada.

## Operações em massa

`QuerySet.update()`, `bulk_create()` e `bulk_update()` não emitem os signals
necessários. Alterações de autorização não poderão usar essas APIs diretamente.

O pacote fornecerá serviços/wrappers oficiais que:

1. executam a mutação;
2. determinam os sujeitos anterior e novo;
3. agendam os increments após commit;
4. registram métricas;
5. suportam as operações em lote do Guardian, que também não emitem
   `post_save`.

SQL cru e serviços externos ficam fora do contrato inicial. Se forem
introduzidos, deverão chamar uma invalidação explícita ou incrementar o epoch
global.

Essa convenção será registrada em ADR. Documentação isolada não será considerada
proteção suficiente: os caminhos oficiais de escrita e os testes tornarão o
contrato executável.

## Consistência e falhas

### Garantia normal

Com banco e Redis disponíveis, mudanças confirmadas incrementam os epochs
imediatamente após commit. Novas verificações deixam de alcançar snapshots
anteriores.

### Janela aceita

Se uma invalidação falhar durante indisponibilidade do Redis, uma entrada antiga
pode voltar a ser observada quando o Redis se recuperar. A consistência é
eventual e limitada ao TTL restante, no máximo 30 minutos.

Essa janela foi aceita explicitamente. Uma outbox durável não será implementada
agora porque adicionaria tabela, worker, retries e estados operacionais para um
caso excepcional já limitado pelo TTL.

### Comportamento por falha

- erro de leitura: consulta o banco sem Cachalot e não usa dado antigo;
- erro de escrita: devolve o resultado correto do banco e registra falha;
- erro de decode/schema: trata como miss;
- erro do banco: propaga; nunca concede acesso usando stale-on-error;
- erro de invalidação: registra métrica e log, sem desfazer a transação já
  confirmada;
- contexto incompleto, usuário inativo ou objeto sem PK: fail-closed conforme a
  semântica dos backends.

## Observabilidade

O registry Prometheus existente será estendido, sem substituir o endpoint
`/metrics` nem seus controles:

- `authorization_cache_operations_total{layer,outcome}`;
- `authorization_cache_invalidations_total{layer,status}`;
- `authorization_cache_fallback_total{layer,reason}`;
- `authorization_cache_resolve_seconds{layer,source}`.

Os valores de `layer`, `outcome`, `status`, `reason` e `source` serão conjuntos
fechados. Usuário, organização, codename, content type e PK nunca serão labels.

As camadas observáveis serão `django`, `tenant`, `guardian` e `rules`. Rules
reutiliza fatos de outros resolvedores, mas informa seu consumidor para permitir
medir o caminho sem criar outro snapshot.

OpenTelemetry continuará instrumentando Redis e banco em baixo nível. Logs
estruturados serão usados apenas para falhas e fallbacks anormais, sem tokens,
conteúdo das permissions ou PII.

## Operação de emergência

O command:

```text
python manage.py invalidate_permission_cache
```

incrementará o epoch global. Isso invalida logicamente todos os snapshots em
O(1), sem `FLUSHDB`, `delete_pattern` ou interferência no Cachalot.

O comando retornará código diferente de zero se o incremento falhar e imprimirá
somente o novo epoch ou o erro operacional seguro.

Casos de uso:

- recuperação após falha de invalidação;
- deploy que altere o schema lógico dos fatos;
- resposta operacional a suspeita de autorização obsoleta.

## Estratégia de testes

### Unitários sem banco

- configuração e defaults;
- composição e normalização de chaves;
- inicialização/incremento de epochs;
- reinicialização segura após perda isolada de uma chave de epoch;
- envelope positivo, negativo e inválido;
- hit, miss, retry e limite de churn;
- falhas de leitura, escrita e invalidação;
- métricas com labels permitidos;
- nenhuma serialização de models.

### Integração Django/PostgreSQL

- permissions globais diretas e por grupo;
- usuário adicionado, removido e limpo de grupos;
- vínculo, papel, organização e estado;
- Guardian direto e por grupo;
- predicates de papel via rules;
- superusuário, inativo e anônimo;
- API sync e async dos backends;
- alterações anterior/nova de FK e slug;
- wrappers de bulk e operações bulk do Guardian;
- RLS configurado pelo snapshot de tenant.

### Integração Redis real

- atomicidade de increments concorrentes;
- visibilidade entre processos;
- TTL de 1.800 segundos;
- cache positivo e negativo;
- isolamento entre databases `/3` e `/4`;
- queda e retorno do Redis com entrada antiga;
- comando de epoch global;
- ausência de leitura em réplica.

### Concorrência e transações

- reader e writer simultâneos;
- epoch alterado durante recomposição;
- gravação tardia sob epoch antigo;
- commit, rollback e nested atomic;
- M2M `clear()` com captura em `pre_clear`;
- invalidação somente após commit;
- interação entre resolvedores e `cachalot_disabled`.

LocMem será suficiente apenas para testes unitários. As garantias distribuídas
não serão declaradas cobertas sem Redis real.

## Critérios de aceite

- O comportamento de autorização é idêntico com cache ligado ou desligado.
- Um hit não executa queries de autorização no banco.
- Uma mutação confirmada muda o epoch correto.
- Snapshots anteriores deixam de ser alcançados por novas verificações.
- Cache positivo e negativo são invalidados nos eventos documentados.
- Falha do Redis consulta o banco e nunca concede acesso por erro.
- Django, Guardian, Rules, DRF, admin, Celery e management commands preservam
  suas interfaces.
- Queries de recomposição ignoram o Cachalot; demais queries da aplicação
  continuam podendo usá-lo.
- Métricas não possuem labels de alta cardinalidade.
- Lint, checks e suíte completa passam com PostgreSQL e Redis.

## Decisões duráveis

O ADR 0004 registra:

- cache semântico sobre Django Cache Framework e `django-redis`;
- Cachalot apenas como cache de query complementar;
- epochs e TTL como modelo de consistência;
- janela máxima aceita de 30 minutos após falha de invalidação;
- ausência de outbox nesta fase;
- proibição de mutações bulk sem invalidação explícita;
- pacote compartilhado em `common/permission_cache`, sem ser Django app.
