# Rastreamento automático de campos alterados

## Objetivo

Evitar que `Model.save()` atualize todas as colunas de instâncias grandes. Para
instâncias já persistidas que herdam de `BaseGlobal` ou `Base`, a camada base
deve detectar automaticamente quais valores finais mudaram e enviar somente
esses campos ao Django, sem código adicional nos consumidores.

## Escopo

- Abrange `instance.save()` sem `update_fields` explícito.
- Aplica-se centralmente em `BaseGlobal`, portanto também a `Base`.
- Inclui `last_modified_at` quando houver ao menos uma mudança real.
- Não executa `UPDATE` nem altera o timestamp quando não houver diferença.
- Preserva exatamente o comportamento de chamadas com `update_fields`.

## Desenho

Depois da hidratação de uma instância existente, a base mantém um snapshot dos
valores dos campos concretos efetivamente carregados. O snapshot deve usar
cópia profunda para detectar mutações in-place, como alterações em listas ou
dicionários de um `JSONField`.

No `save()` de uma instância existente, sem `update_fields`, a base compara os
valores atuais aos valores do snapshot. Ela fornece ao Django um
`update_fields` com os nomes dos campos cujo valor final diverge e com os campos
`auto_now` aplicáveis. Se a lista for vazia, retorna sem gravar.

Campos deferred não entram no snapshot nem são acessados durante a comparação.
Isso evita consultas adicionais e preserva o uso de `defer()`.

Após um save bem-sucedido, o snapshot é reconstruído. Objetos novos preservam o
fluxo normal de inserção, pois todos os campos precisam ser incluídos no
`INSERT`.

## Limites intencionais

`bulk_create`, `bulk_update` e `QuerySet.update()` não chamam `save()` e ficam
fora deste escopo. Esses fluxos já devem receber explicitamente os valores e
campos necessários por quem os invoca.

## Testes

- Alterar um campo em instância persistida produz `UPDATE` apenas para ele e
  `last_modified_at`.
- Chamar `save()` sem alterar valores não executa `UPDATE`.
- Reverter um campo ao valor original antes do save não executa `UPDATE`.
- Mutação in-place de valor mutável é detectada.
- Campos deferred não geram consulta extra nem entram em `update_fields`.
- `update_fields` fornecido pelo consumidor é preservado.
