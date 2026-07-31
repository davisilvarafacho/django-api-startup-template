# Bibliotecas Django para cache de permissões

Data da pesquisa: 2026-07-29  
Fontes consultadas: documentação, PyPI, repositórios e código dos próprios
projetos, acessados em 2026-07-29.

## Conclusão

Nenhuma das bibliotecas avaliadas substitui de forma segura a camada semântica
de cache de autorização proposta para o projeto.

A combinação recomendada continua sendo:

1. **Django Cache Framework + `django-redis`** como infraestrutura do cache
   compartilhado, em um alias `permissions`;
2. **resolvedores próprios e pequenos** para permissões globais do Django,
   vínculo/papel no tenant e permissões por objeto do Guardian;
3. **`django-rules` continuando a avaliar predicates**, mas consumindo fatos
   estáveis já cacheados; resultados de predicates arbitrários não devem ser
   cacheados automaticamente;
4. **signals e hooks explícitos de domínio** para invalidação após commit, com
   TTL de 1.800 segundos como rede de segurança;
5. **`django-cachalot` mantido como cache de queries**, capaz de acelerar a
   recomposição após um miss, mas não como cache de autorização.

`django-cacheops`, especialmente `cached_as(...)`, é a única alternativa que
merece consideração técnica mais próxima. Ele pode cachear uma função e ligar
seu resultado a querysets/modelos para invalidação automática. Entretanto, é a
aplicação que ainda precisa declarar corretamente todas as dependências,
construir a chave contextual e tratar operações em massa. Em autorização, isso
apenas desloca — não elimina — a responsabilidade semântica. Adicioná-lo também
criaria uma segunda camada de cache de ORM ao lado do Cachalot e um segundo
cliente/configuração de Redis ao lado do `django-redis`.

## Contexto deste projeto

O projeto usa Django 5.2, Python 3.12, `django-cachalot`,
`django-redis`, `django-guardian` e `rules`. O cache de queries já possui um
alias dedicado, e Redis também é instrumentado pelo OpenTelemetry. A solução
nova precisa cobrir:

- permissões globais diretas e herdadas de grupos;
- vínculo e papel por organização;
- permissões por objeto, diretas e herdadas de grupos;
- fatos consumidos por predicates do `django-rules`;
- resultados positivos e negativos;
- API, admin, Celery, management commands e chamadas diretas a
  `user.has_perm()`;
- fallback ao banco se o Redis falhar;
- invalidação imediata no caminho normal e TTL configurável como proteção.

Esse escopo é mais amplo que “cachear uma query”: ele materializa um fato de
autorização e precisa conhecer quem será afetado por cada mutação.

## Comparação resumida

| Opção | Manutenção e compatibilidade | Cache compartilhado | Invalidação | Permissão/tenant | Observabilidade | Papel recomendado |
| --- | --- | --- | --- | --- | --- | --- |
| Django Cache + `django-redis` | `django-redis` 7.0.0, junho/2026; Django 5.2+ e Python 3.10+ | Sim, Redis | Manual, por chave/versão/TTL | Agnóstico; permite modelar exatamente | Logs de falha; instrumentação Redis; métricas de domínio ficam na aplicação | **Base recomendada** |
| `django-cachalot` | 2.9.0, janeiro/2026; Django 5.2 e Python 3.12 declarados | Sim, via backend Django | Automática por tabela, inclusive bulk no ORM | Não conhece usuário, tenant, objeto ou decisão | signal de invalidação, API e painel de debug | Complementar em misses |
| `django-cacheops` | 7.2, abril/2025; classifica Django 5.2 e Python 3.12 | Sim, Redis próprio | Por query/modelo; signals; `cached_as`; bulk requer API especial | Pode receber dependências, mas não as descobre | signals de hit/miss e invalidação | Alternativa possível, não recomendada aqui |
| `django-cache-machine` | 1.2.0, julho/2022; só declara Django até 4.0 e Python 3.10 | Parcial: objetos no cache Django; Redis só para flush lists | Por objeto/flush list, com limitações de criação e queries vazias | Não conhece autorização | Sem métricas de domínio documentadas | Descartar |
| Cache interno do `ModelBackend` | Django 5.2 | Não; vive na instância de usuário | Não é invalidado automaticamente na instância | Só permissões globais do Django | Nenhuma específica | Manter apenas como L1 por execução |
| `django-guardian` | 3.3.2, junho/2026; Django 5.2 e Python 3.12 | Não; checker/usuário em memória | Não observa mudança enquanto o checker vive | Sim, permissões por objeto e grupos | Nenhuma de cache compartilhado | Resolver/fonte de verdade |
| `django-rules` | 3.5 no projeto; predicates são callables arbitrários | Não | Não há invalidação distribuída | Sim, regras contextuais | log detalhado de predicates | Avaliador, não cache |
| `django-permission-engine` | 0.1.7, março/2026, status Alpha; não declara Django 5.2/Python 3.12 nos classifiers | Cache opcional anunciado | Não há garantias suficientes publicadas para este caso | Substitui o modelo por um registry próprio | Não documentada para este requisito | Não adotar para cache |
| `rbac-infra` | 0.1.8, abril/2026; pacote novo, Python 3.9+, sem matriz Django 5.2 | Redis próprio opcional | Contrato de invalidação não está documentado | Substitui os modelos por RBAC multi-tenant próprio | Não documentada para este requisito | Não adotar para cache |

## Django Cache Framework e `django-redis`

O framework nativo oferece aliases, TTL, prefixos, transformação de chave e
versionamento. O Django 5.2 também possui backend Redis nativo e permite
incrementar a versão de uma chave sem limpar todo o cache
([cache framework](https://docs.djangoproject.com/en/5.2/topics/cache/#cache-versioning)).
Isso é suficiente para armazenar snapshots imutáveis como:

- conjunto de permissões globais por usuário;
- vínculo/papel por `(usuário, organização)`;
- conjunto de codenames por `(usuário, content type, objeto)`.

O `django-redis` 7.0.0 foi publicado em 2 de junho de 2026, exige Django 5.2+
e inclui Python 3.12 entre as versões suportadas
([PyPI](https://pypi.org/project/django-redis/)). Ele acrescenta recursos úteis
como TTL consultável, locks, operações atômicas, remoção por padrão e
`IGNORE_EXCEPTIONS`. Esse último transforma falhas de conexão em comportamento
de miss e pode registrar as exceções
([documentação de falhas](https://github.com/jazzband/django-redis#memcached-exceptions-behavior)).

Para autorização, o valor precisa usar um envelope ou sentinel explícito:
`None` não pode significar ao mesmo tempo “miss” e “negação”. Assim, `False`,
conjunto vazio e “sem vínculo” podem ser cacheados sem ambiguidade.

O framework não sabe quando uma permissão mudou. Isso é uma vantagem neste caso:
a aplicação mantém controle explícito sobre o alcance da invalidação, em vez de
inferi-lo de uma query. O custo é implementar as poucas peças semânticas que
nenhuma biblioteca genérica oferece.

## `django-cachalot`

O Cachalot 2.9.0 foi publicado em 28 de janeiro de 2026 e declara suporte a
Django 4.2, 5.2 e 6.0 e Python 3.8–3.14
([PyPI](https://pypi.org/project/django-cachalot/)). O projeto está atualmente
fixado em 2.8.0, versão que já adicionou Django 5.2 à matriz de testes
([changelog](https://django-cachalot.readthedocs.io/en/latest/changelog.html)).

Ele intercepta leituras do ORM, armazena o resultado SQL e invalida por tabela.
Sua documentação declara suporte a `QuerySet.update()` e a mudanças em massa,
mas uma alteração em qualquer registro invalida todas as queries que tocam a
tabela correspondente
([introdução](https://django-cachalot.readthedocs.io/en/latest/introduction.html)).

Isso o torna robusto para coerência de queries, porém inadequado como cache
principal de autorização:

- não existe uma chave conceitual por usuário/tenant/objeto;
- uma mudança de permissão de grupo não materializa diretamente quais usuários
  perderam acesso;
- o Cachalot não cacheia uma decisão composta, apenas as queries usadas para
  produzi-la;
- observabilidade é de invalidação de tabela/query, não de
  `hit`, `miss`, `fallback` e `erro` por mecanismo de autorização.

Sua API expõe invalidação por tabela/modelo e o timestamp da última invalidação
([API](https://django-cachalot.readthedocs.io/en/latest/api.html)). Seria
tecnicamente possível incorporar timestamps de tabelas em chaves próprias, mas
isso voltaria a invalidar de maneira ampla, acoplaria o design ao Cachalot e
continuaria sem expressar dependências de predicates arbitrários.

Portanto, o Cachalot deve permanecer transparente abaixo dos resolvedores:
quando o cache semântico expirar ou for invalidado, as consultas de recomposição
podem se beneficiar do cache de query. Ele não deve controlar a validade do
snapshot.

## `django-cacheops` e `cached_as(...)`

O Cacheops 7.2 foi publicado em 20 de abril de 2025. O PyPI declara suporte a
Django 5.2 e Python 3.12
([PyPI](https://pypi.org/project/django-cacheops/)). Ele usa Redis diretamente,
possui invalidação orientada a eventos, suporte a transações, fallback em falha
do Redis e signals de leitura/invalidação
([documentação oficial](https://github.com/Suor/django-cacheops)).

`cached_as(...)` permite cachear o retorno de uma função e associá-lo a um ou
mais modelos ou querysets. Também aceita um `extra` para variar a chave. Por
exemplo, seria possível ligar um snapshot a querysets filtrados pelo usuário,
organização e objeto. O retorno negativo também pode ser armazenado porque a
função cacheada pode retornar um booleano ou coleção vazia.

Entretanto, para este projeto ainda seria necessário declarar:

- todas as tabelas e relações M2M que influenciam permissões globais;
- pertencimento atual a grupos e permissões de cada grupo;
- vínculo e papel na organização;
- permissões Guardian diretas e por grupo, inclusive tabelas genéricas;
- estado adicional lido por cada predicate cacheável;
- usuário, organização, content type e PK no `extra`/argumentos;
- invalidação em mutações em massa que não emitem signals.

Associar `cached_as(Model)` a cada tabela seria seguro, mas invalidaria snapshots
de todos os usuários em qualquer mudança. Usar querysets filtrados melhora a
granularidade, porém aumenta o risco de omitir uma dependência indireta. Mudar
as permissões de um grupo é o exemplo clássico: o conjunto afetado é o dos
usuários que pertencem ao grupo, não apenas a linha alterada.

Há ainda limitações documentadas relevantes: `QuerySet.update()` não dispara os
eventos esperados; deve ser substituído por `invalidated_update()`, que busca os
objetos antes e depois. SQL raw não é suportado, alterações via
`select_related` têm ressalvas e multi-table inheritance não funciona
corretamente
([caveats e mass updates](https://github.com/Suor/django-cacheops#mass-updates)).

O Guardian agrava esse ponto: suas atribuições em lote usam `bulk_create()` e
explicitamente não enviam `post_save`
([documentação do Guardian](https://django-guardian.readthedocs.io/en/stable/userguide/assign/#limitations)).
Logo, mesmo com `cached_as`, continuaríamos obrigados a envolver APIs de mutação
e invalidar explicitamente.

### Sobreposição com Cachalot e `django-redis`

Cacheops não é um adaptador do Django Cache Framework: mantém configuração,
cliente Redis, formato de chaves, serialização e estruturas de invalidação
próprias. No projeto atual teríamos:

1. Cachalot cacheando queries no alias `cachalot`;
2. Cacheops mantendo dependências e resultados de função no Redis próprio;
3. `django-redis` atendendo os demais aliases;
4. três caminhos distintos de falha, métricas e operação.

Em um miss de `cached_as`, as queries ainda passariam pelo Cachalot. Isso pode
funcionar, mas duplica camadas sem eliminar a necessidade do contrato de
autorização. A economia seria principalmente não escrever o armazenamento e
parte dos signals; em troca, dependeríamos do modelo de invalidação do Cacheops
e de suas ressalvas.

Recomendação: **não adicionar Cacheops apenas para este recurso**. Se ele for
adotado futuramente como estratégia geral de ORM no lugar do Cachalot, então
`cached_as` pode ser reavaliado, com uma prova de conceito específica para
grupos, Guardian, transações e revogações.

## `django-cache-machine`

A versão mais recente, 1.2.0, é de julho de 2022 e declara somente Django
2.2–4.0 e Python 3.6–3.10
([PyPI](https://pypi.org/project/django-cache-machine/)). Não há compatibilidade
declarada com Django 5.2 ou Python 3.12.

Sua invalidação usa “flush lists” por objeto. A própria documentação informa
que querysets vazios não são cacheados por padrão, criação de objetos não
invalida queries por padrão e Redis armazena apenas as flush lists; os objetos
continuam no backend Django, historicamente Memcached
([documentação](https://cache-machine.readthedocs.io/en/latest/)).

Além da incompatibilidade, essas escolhas são perigosas para cache negativo de
autorização: uma concessão nova precisa invalidar imediatamente um “não possui
permissão”. A biblioteca deve ser descartada.

## Cache interno do `ModelBackend`

O `ModelBackend` do Django guarda o conjunto de permissões na própria instância
do usuário após a primeira consulta. A documentação o descreve como adequado ao
ciclo request/response e alerta que nem `refresh_from_db()` limpa o cache; é
necessário buscar outra instância
([Django 5.2](https://docs.djangoproject.com/en/5.2/topics/auth/default/#permission-caching)).

Esse cache:

- não é Redis nem compartilhado entre requests, workers ou Celery;
- não possui TTL;
- pode ficar obsoleto dentro da própria execução;
- cobre permissões globais, mas o backend padrão retorna conjunto vazio quando
  recebe `obj`
  ([referência do backend](https://docs.djangoproject.com/en/5.2/ref/contrib/auth/#django.contrib.auth.backends.ModelBackend)).

Ele deve continuar existindo como otimização L1 da instância, mas o backend
customizado precisará consultar o resolvedor compartilhado e garantir que
invalidações locais não mantenham atributos `_perm_cache`,
`_user_perm_cache` ou `_group_perm_cache` obsoletos.

## `django-guardian`

O Guardian 3.3.2 foi publicado em 8 de junho de 2026 e declara Django 5.2 e
Python 3.12
([PyPI](https://pypi.org/project/django-guardian/)). Ele é a implementação
correta de permissões persistidas por objeto, não uma solução de cache
distribuído.

`ObjectPermissionChecker` guarda uma lista de permissões por content type/PK em
um dicionário local. A documentação alerta que mudanças posteriores não são
observadas pela mesma instância do checker
([API do checker](https://django-guardian.readthedocs.io/en/stable/api/core/)).
`prefetch_perms()` reduz queries para vários objetos, mas o resultado ainda vive
no checker/usuário da execução atual.

O resolvedor compartilhado pode reutilizar as queries e modelos do Guardian,
mas precisa adicionar:

- chave com usuário, grupos relevantes, content type e PK;
- cache negativo;
- TTL;
- invalidadores para atribuição/remoção direta e por grupo;
- hook explícito para as operações em lote do Guardian.

## `django-rules`

`rules` executa callables arbitrários, combináveis em predicates. Ele oferece um
contexto apenas durante uma invocação, que pode guardar valores computados entre
predicates, e logging de avaliação
([documentação oficial](https://github.com/dfunckt/django-rules)).

Como um predicate pode depender de qualquer atributo do usuário, objeto,
horário, request ou serviço externo, não há como uma biblioteca genérica inferir
uma chave e todos os eventos de invalidação. Cachear automaticamente seu
booleano final aumentaria o risco de autorização obsoleta.

A estratégia segura é manter a avaliação e trocar consultas estáveis internas,
como “qual é o papel deste usuário nesta organização?”, por chamadas ao
resolvedor de tenant. Um predicate só deve ter seu resultado final cacheado por
opt-in e com dependências declaradas.

## Bibliotecas que substituem o modelo de autorização

### `django-permission-engine`

O `django-permission-engine` 0.1.7 foi publicado em março de 2026, anuncia
checks O(1) com cache opcional, grupos e integração DRF. Porém está classificado
como **Alpha**, possui poucos commits e seu PyPI declara classifiers somente até
Django 4.2 e Python 3.11
([PyPI](https://pypi.org/project/django-permission-engine/),
[repositório](https://github.com/sarthaksnh5/django_permission_engine)).

Adotá-lo implicaria migrar para seu Unified Permission Registry, modelos,
endpoints de atribuição e permission class. Ele não complementa de forma
transparente Django + Guardian + Rules, nem há evidência publicada suficiente
de invalidação imediata distribuída para os quatro mecanismos atuais.

### `rbac-infra`

O `rbac-infra` 0.1.8 foi publicado em abril de 2026. Possui conceitos atraentes
para o domínio — RBAC multi-tenant, policies e cache Redis — mas é um pacote
0.1.x recente, declara apenas Python 3.9+ e não publica uma matriz explícita de
Django 5.2/Python 3.12. A documentação não especifica TTL, cache negativo,
invalidação nas mutações dos modelos ou observabilidade
([PyPI](https://pypi.org/project/rbac-infra/)).

Ele também introduz seus próprios `Tenant`, `Role`, `Permission` e `UserRole`.
Logo, seria uma substituição do modelo de autorização do template, não uma
biblioteca de cache encaixável. Não é recomendável assumir essa migração para
resolver apenas desempenho.

## Contrato recomendado

### Armazenamento

- alias `CACHES["permissions"]` com `django-redis`;
- Redis dedicado logicamente ao domínio, separado do cache geral e do Cachalot;
- TTL padrão centralizado em `AUTHORIZATION_CACHE`, igual a 1.800 segundos;
- valores imutáveis e pequenos: sets/listas de strings e snapshots de vínculo;
- envelope explícito para diferenciar miss de resultado negativo.

### Invalidação

- `m2m_changed` para grupos, permissões de usuário e permissões de grupo;
- `post_save`/`post_delete` para vínculo/papel e modelos de permissão Guardian;
- execução via `transaction.on_commit()` para não publicar estado que ainda
  possa sofrer rollback;
- versionamento/epochs quando uma alteração afeta muitos sujeitos, como uma
  mudança de permissão de grupo;
- operações `update()`, `bulk_create()` e `bulk_update()` proibidas no domínio
  de autorização sem chamada explícita de invalidação;
- wrappers para atribuições em lote do Guardian;
- TTL como recuperação para signal/hook perdido, não como mecanismo principal.

O próprio Django confirma que `bulk_create()` e `bulk_update()` não chamam
`save()` nem emitem `pre_save`/`post_save`
([QuerySet API](https://docs.djangoproject.com/en/5.2/ref/models/querysets/#bulk-create)).

### Falhas e observabilidade

- falha do cache equivale a miss e consulta ao banco, nunca a concessão;
- se a leitura do banco falhar, manter o comportamento normal de erro da
  aplicação, sem reutilizar dado potencialmente revogado;
- métricas Prometheus de `hit`, `miss`, `db_fallback`, `cache_error` e
  `invalidation`, com label apenas para `django`, `tenant`, `guardian` e
  `rules`;
- nenhuma label com usuário, organização, permissão ou PK;
- manter a instrumentação OpenTelemetry de Redis para latência/erros de baixo
  nível e estender a observabilidade existente com métricas de domínio;
- logs somente para erro/fallback anormal, com rate limiting se necessário.

## Decisão sugerida para o design

Manter a arquitetura híbrida já aprovada:

- **Django Cache Framework + `django-redis`: adotado** para o cache semântico;
- **Cachalot: mantido** como cache de queries complementar;
- **Cacheops: não adicionado** nesta implementação;
- **Cache Machine: descartado** por manutenção/compatibilidade e riscos de
  invalidação negativa;
- **ModelBackend e Guardian: estendidos**, não substituídos;
- **Rules: continua avaliando**, consumindo resolvedores estáveis;
- **Permission Engine e RBAC Infra: não adotados**, pois mudariam o modelo de
  autorização e ainda não demonstram maturidade/compatibilidade suficiente para
  este template.

Essa escolha escreve somente o que é específico do domínio — composição de
chaves, resolvedores, invalidação e métricas — e delega Redis, TTL,
serialização, conexão e primitivas de cache a bibliotecas maduras já presentes
no projeto.
