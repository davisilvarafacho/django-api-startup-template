# 0008 — Unicidade sempre por organização, nunca global

- Status: Aceito
- Data: 2026-08-09

## Contexto

`unique=True` em um field, assim como `Meta.unique_together`, cria um índice
único **global**: o valor passa a ser único em toda a tabela, atravessando
organizações. Em uma base multi-tenant por Row Level Security isso produz três
problemas distintos.

O primeiro é semântico. Duas organizações são inquilinos independentes; que a
Acme tenha um pedido de código `ABC` não deve impedir a Globex de ter o seu.
Unicidade global transforma um valor de negócio local em um recurso disputado
entre clientes.

O segundo é operacional e mais grave. O RLS esconde da organização atual as
linhas das demais. Ao inserir um valor que já existe em outra organização, a
escrita falha com `IntegrityError` sobre uma linha que aquele tenant não pode
enxergar nem listar: a resposta é 500 em vez de erro de validação, e o próprio
erro confirma que o valor existe em algum lugar — um oráculo de existência entre
inquilinos.

O terceiro aparece com soft delete. Toda a base marca `is_deleted=True` em vez
de apagar. Um índice único que ignore essa coluna faz um registro excluído
continuar bloqueando a recriação do mesmo valor.

## Decisão

Em models que herdam de `Base`, `unique=True` e `Meta.unique_together` são
proibidos. Unicidade é declarada exclusivamente como `UniqueConstraint`, com
`organizacao` como primeiro campo ([ADR 0007](0007-indices-e-constraints-por-organizacao.md))
e com a exclusão lógica expressa em `condition`:

```python
class Meta:
    constraints = [
        models.UniqueConstraint(
            fields=["organizacao", "codigo"],
            condition=models.Q(is_deleted=False),
            name="pedido_organizacao_codigo_unique",
        ),
    ]
```

O nome segue `<tabela>_<campos>_unique`.

Models `BaseTenantless` continuam podendo usar `unique=True`: `Usuario.email` e
o identificador de `Organizacao` são globais por natureza, existem antes de haver
tenant e não estão sob RLS.

O cumprimento é verificado por system check do Django.

## Alternativas rejeitadas

**`is_deleted` como último campo da lista de `fields`.** Foi a primeira proposta
e resolve o caso simples, mas quebra na repetição: com `("organizacao",
"codigo", "is_deleted")` a tupla `(org, "ABC", True)` também é única, então só
pode existir **um** registro excluído com aquele código. A sequência criar →
excluir → recriar → excluir de novo estoura `IntegrityError` na segunda
exclusão. O índice parcial não tem esse teto: um registro vivo, quantos
excluídos forem necessários.

**Unicidade global mantida, com validação amigável no serializer.** Continuaria
vazando existência entre organizações — a validação teria de consultar linhas de
outro tenant para dar a mensagem — e continuaria sujeita a corrida entre a
checagem e o insert.

**Unicidade só na aplicação, sem constraint.** Duas requisições concorrentes
gravam o mesmo valor. O banco é o único lugar onde a garantia é real.

## Consequências

- Cada organização passa a ter seu próprio espaço de valores únicos.
- Conflitos passam a ocorrer apenas dentro da organização atual, onde a linha
  conflitante é visível e pode virar erro de validação legível.
- Índice parcial não é alvo automático de `ON CONFLICT`: `get_or_create` sob
  concorrência e `bulk_create(update_conflicts=True)` precisam levar em conta a
  condição `is_deleted=False`.
- `UniqueConstraint` com `condition` não é usada pelo DRF para gerar validação
  automática de unicidade; a mensagem amigável, quando necessária, é
  responsabilidade do serializer.
- Models existentes fora do padrão precisam de migration corretiva; o system
  check indica quais são.
