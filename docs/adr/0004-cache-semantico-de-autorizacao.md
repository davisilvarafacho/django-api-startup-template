# 0004 — Cache semântico de autorização

- Status: Aceito
- Data: 2026-07-29

## Contexto

A autorização combina quatro mecanismos: permissions globais do Django,
vínculo/papel por organização, permissões por objeto do `django-guardian` e
predicates do `django-rules`.

O Django e o Guardian possuem caches locais à instância, enquanto o Cachalot
cacheia resultados de queries e invalida por tabela. Nenhum deles representa,
entre requests e workers, o fato composto “este sujeito possui este acesso
neste tenant ou objeto”.

Autorização exige também cache negativo, revogação previsível, fallback seguro
e observabilidade de domínio. Cachear somente SQL ou o booleano final de um
predicate arbitrário não oferece essas garantias.

## Decisão

Adotar uma camada semântica em `common/permission_cache`, como pacote Python
comum e não como Django app.

O armazenamento usará Django Cache Framework com `django-redis`, no alias
dedicado `permissions`. O TTL padrão será centralizado em
`AUTHORIZATION_CACHE` e terá 1.800 segundos.

Snapshots serão pequenos, imutáveis e compostos por tipos primitivos. Models
Django não serão serializados. Resultados negativos terão representação
explícita.

A invalidação usará epochs atômicos no Redis. Chaves de snapshot incluem os
epochs global, da camada e do sujeito/objeto. Signals agendam increments em
`transaction.on_commit()`. Um comando operacional poderá incrementar o epoch
global em O(1).

Falhas de leitura do Redis causam fallback ao banco, nunca concessão por erro.
Falhas de invalidação são observadas por métrica e log. Aceita-se consistência
eventual limitada ao TTL restante, no máximo 30 minutos, caso uma invalidação
seja perdida durante indisponibilidade do Redis. Não será criada outbox nesta
fase.

O Cachalot continuará habilitado para queries normais da aplicação. Consultas
que recompõem snapshots de autorização serão executadas dentro de
`cachalot_disabled(all_queries=True)`, sem desabilitá-lo globalmente.

Backends cacheados substituirão, e não duplicarão, os backends padrão Django e
Guardian. Predicates de rules continuarão sendo avaliados; somente seus fatos
estáveis usarão os resolvedores.

## Contrato de mutação

Mudanças de autorização devem passar por operações ORM que emitam os signals
previstos ou por serviços de mutação que invalidem explicitamente.

`QuerySet.update()`, `bulk_create()` e `bulk_update()` não podem ser usados
diretamente em modelos de autorização. O mesmo vale para operações em lote do
Guardian que não emitem `post_save`. O projeto fornecerá wrappers oficiais que
executam a mutação e agendam os increments corretos após commit.

SQL cru ou alterações por serviços externos exigirão invalidação explícita e
não fazem parte do contrato inicial.

## Alternativas rejeitadas

**Somente `django-cachalot`.** Reduz queries, mas invalida amplamente por tabela
e não oferece snapshots, métricas ou invalidação por sujeito.

**Adicionar `django-cacheops`.** `cached_as(...)` reduz plumbing, porém ainda
exige enumerar todas as dependências semânticas e tratar bulk, Guardian e rules.
Também adicionaria outro protocolo Redis ao lado do Cachalot. Poderá ser
reavaliado se substituir o Cachalot como estratégia geral de ORM.

**Outbox de invalidação.** Eliminaria parte da janela após falha do Redis, mas
adicionaria persistência, worker, retries e estados operacionais. O TTL aceito
é suficiente nesta fase.

## Consequências

- O cache expressa diretamente os fatos de autorização e preserva
  `user.has_perm()`.
- Redis indisponível aumenta consultas ao banco, mas não derruba nem libera a
  autorização por falta de cache.
- Revogações são imediatas no caminho normal e eventualmente consistentes, por
  até 30 minutos, quando a invalidação falha.
- A implementação precisa manter uma matriz explícita de eventos, epochs e
  sujeitos afetados.
- Mutações em massa ganham caminhos oficiais e não podem contornar a
  invalidação silenciosamente.
- Cache semântico e Cachalot operam em databases lógicos separados e podem
  coexistir.
- Testes com Redis real são obrigatórios para declarar as garantias
  distribuídas.
