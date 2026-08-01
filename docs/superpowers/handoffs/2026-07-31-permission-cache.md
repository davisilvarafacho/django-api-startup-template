# Handoff — cache de permissões

- Branch: `feature/permission-cache`
- Worktree: `.worktrees/permission-cache`
- Data: 2026-07-31
- Plano: [`2026-07-30-permission-cache.md`](../plans/2026-07-30-permission-cache.md)

## Estado

As tarefas 1 a 3 estão implementadas e aprovadas em revisão independente.

| Tarefa | Estado | Commits |
| --- | --- | --- |
| 1 — configuração, chaves e contratos imutáveis | concluída | `ac07d15` |
| 2 — epochs, store, fallback, métricas e invalidação genérica | concluída após um fix | `c7d7696`, `98de4f9`, `c85b373` |
| 3 — resolver e backend global do Django | concluída após um fix | `ac685be`, `b5db4f3` |
| 4 — signals Django | implementação concluída, revisão reprovada | `1f4f192` |
| 5 a 10 | não iniciadas | — |

O commit `1f4f192` passou 23 testes focados e 175 testes da suíte completa,
mas não deve ser considerado aprovado ainda.

## Ponto de parada

A revisão da tarefa 4 identificou uma condição de corrida na captura de IDs
durante `reverse clear()` de relações M2M. O código usa um dicionário global
por processo, indexado por `(sender, instance.pk, using)`. Duas transações
concorrentes sobre o mesmo grupo podem sobrescrever essa captura; se uma delas
fizer rollback e a outra confirmar, a confirmação pode deixar de invalidar o
cache de permissões revogadas.

O plano aprovado descreve explicitamente esse dicionário global, portanto a
correção exige escolher entre:

1. revisar o plano e isolar a captura por execução/transação (recomendado);
2. manter o mecanismo atual e aceitar essa janela de inconsistência concorrente.

Nenhuma tarefa posterior deve começar antes dessa decisão e da correção/revisão
da tarefa 4, pois elas dependem da semântica de invalidação após commit.

## Próxima ação recomendada

Após a decisão, retomar o agente implementador da tarefa 4 para corrigir o
finding, executar a re-revisão escopada e então continuar da tarefa 5 em
ordem sequencial, usando o ledger local em
`.superpowers/sdd/2026-07-30-permission-cache/progress.md`.

## Validações já executadas

- Task 1: 6 testes focados aprovados.
- Task 2: 28 testes focados aprovados, incluindo overflow em Redis real;
  cobertura do pacote `common/permission_cache` em 90%.
- Task 3: 165 testes da suíte completa aprovados.
- Task 4: 175 testes da suíte completa aprovados, pendente apenas da correção
  de concorrência identificada em revisão.
- Ruff, formatação, `manage.py check`, checagem de migrations e documentação
  passaram nas tarefas em que foram executados.
