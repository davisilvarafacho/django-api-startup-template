# 0007 — Índices e constraints começam por organização

- Status: Aceito
- Data: 2026-08-09

## Contexto

Todo model de negócio herda de `Base` e é isolado por Row Level Security. A
policy do `django-rls` adiciona `organizacao_id = current_setting(...)` ao
predicado de **toda** query desses models — não existe leitura de model de
negócio sem esse filtro.

Um índice B-tree só é acessado com eficiência pela sua coluna inicial. Um índice
declarado como `("codigo", "organizacao")` não serve ao predicado do RLS: para
achar as linhas de uma organização o Postgres precisa varrer o índice inteiro ou
descartá-lo e ir à tabela. O índice existe, ocupa espaço, encarece toda escrita,
e não é usado para o filtro que aparece em 100% das consultas.

O mesmo raciocínio vale para `UniqueConstraint`, que no Postgres é implementada
como índice único e é usada tanto para validar a escrita quanto para consultar.

## Decisão

Em models que herdam de `Base`, `organizacao` é o **primeiro** campo de toda
entrada de `Meta.indexes` e de toda `UniqueConstraint` em `Meta.constraints`.

```python
class Meta:
    indexes = [
        models.Index(fields=["organizacao", "status", "-created_at"]),
    ]
    constraints = [
        models.UniqueConstraint(
            fields=["organizacao", "codigo"],
            condition=models.Q(is_deleted=False),
            name="pedido_organizacao_codigo_unique",
        ),
    ]
```

A regra não se aplica a models `BaseTenantless` (`Usuario`, `Organizacao`,
`Time`, `Vinculo`, `Convite`), que não têm o campo `organizacao` e são lidos
antes de existir contexto de tenant.

O cumprimento é verificado por system check do Django, que reporta o model e a
declaração em desacordo.

## Alternativas rejeitadas

**Confiar no planner.** O Postgres às vezes recorre a *index skip scan* ou
combina bitmaps de dois índices, mas essa escolha depende de estatísticas e de
cardinalidade. Uma convenção de modelagem não deve depender de o planner acertar.

**Índice adicional só em `organizacao`.** Serviria ao predicado do RLS isolado,
mas não a nenhuma consulta real, que sempre combina organização com outra coluna.
Resultaria em dois índices onde um composto bem ordenado resolve.

**Deixar a ordem a critério de quem escreve o model.** É exatamente o estado que
produziu a `UniqueConstraint` de `Metadata` sem organização. A ordem correta não
é evidente ao escrever o model, só ao depurar a query meses depois.

## Consequências

- Índices compostos passam a servir simultaneamente ao filtro do RLS e ao filtro
  de negócio.
- Ordenar por `organizacao` primeiro não prejudica consultas privilegiadas que
  atravessam organizações: elas são raras e operacionais.
- Models existentes fora do padrão precisam de migration corretiva; o system
  check indica quais são.
- A regra de unicidade decorrente está no [ADR 0008](0008-unicidade-por-organizacao.md).
