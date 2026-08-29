# Catálogo de planos

O app `apps.assinaturas` mantém o catálogo comercial global usado para novas
contratações. O catálogo tem três níveis:

- `Plano` é a identidade estável do produto;
- `VersaoPlano` guarda os termos comerciais de uma versão;
- `PrecoPlano` associa a versão a uma periodicidade e moeda.

Código de plano é único entre registros vivos. Número de versão é único por
plano, só uma versão viva pode ser atual e só existe um preço por versão,
periodicidade e moeda. Moedas usam três letras maiúsculas e valores monetários
são inteiros não negativos na menor unidade da moeda.

## Imutabilidade

Uma versão com `publicada_em` preenchido não aceita alteração de termos. Seus
preços também não podem ser alterados. A proteção vale para `save()`,
`QuerySet.update()` e `bulk_update()`; o Admin expõe esses registros somente
para consulta. Os únicos campos operacionais mutáveis são `atual` e
`is_active`, conforme o model.

Qualquer mudança de seats, trial, carências, recursos ou preços exige um novo
`numero` em `DefinicaoVersaoPlano`. Assinaturas existentes continuam apontando
para a versão histórica.

## Recursos tipados

Cada chave em `apps/assinaturas/features.py` é um `RecursoPlano[T]`. Um
`ValoresRecursos` valida o JSON persistido e oferece lookup pelo objeto tipado:

```python
from apps.assinaturas.features import PAPEIS_ISENTOS_SEAT, QUANTIDADE_PROJETOS

limite: int = recursos.obter(QUANTIDADE_PROJETOS)
papeis_isentos: frozenset = recursos.obter(PAPEIS_ISENTOS_SEAT)
```

Snapshots novos são materializados com todas as chaves conhecidas. Quando um
snapshot antigo não contém uma chave adicionada posteriormente, `obter()`
devolve o default tipado da declaração. `CATALOGO_RECURSOS.json_schema()` e
`CATALOGO_RECURSOS.exemplos()` produzem documentos determinísticos para
consumidores de OpenAPI. System checks recusam declarações inconsistentes.

## Bootstrap e sincronização

As definições congeladas `DefinicaoPlano`, `DefinicaoVersaoPlano` e
`DefinicaoPrecoPlano` vivem em `apps/assinaturas/catalogs.py`. O bootstrap
inicial contém o plano gratuito v1 e um plano profissional pago de exemplo. Ele
não contém IDs de produto, preço ou assinatura do Stripe: referências externas
pertencem ao futuro subapp de faturamento e são provisionadas separadamente.

Inspecione mudanças sem escrever:

```bash
uv run python manage.py sync_plans
```

Depois de revisar a saída, aplique-as explicitamente:

```bash
uv run python manage.py sync_plans --apply
```

O comando é transacional e idempotente. Ele cria planos, versões e preços
ausentes e troca a versão atual retirando primeiro a flag anterior. Se o mesmo
`(plano, numero)` ou preço publicado tiver conteúdo diferente, o comando falha
e orienta declarar o próximo número; nenhuma escrita parcial permanece. Ele
não cria assinaturas, produtos remotos nem chama gateway.
