# Relatório — Tarefa 2: aplicar a baseline com rollback de arquivos

## Implementação

- Adicionada `apply_migration_reset(plan, *, confirmed_database)` em `apps/api/core/migration_reset.py`.
- A aplicação bloqueia produção e exige confirmação exata do banco antes de qualquer mutação.
- Migrations removíveis são copiadas para um diretório temporário; falhas em `makemigrations` restauram o snapshot e removem arquivos gerados.
- A execução segue a ordem: `makemigrations`, validação, reset do schema público, `migrate`, validação final e `showmigrations --plan`.
- O reset do schema usa a conexão padrão e recria `public` com autorização e grant públicos.

## Arquivos alterados

- `apps/api/core/migration_reset.py`
- `apps/api/core/tests/management_commands/test_reset_migrations.py`

## TDD e testes

RED: após adicionar os quatro testes de aplicação/proteção/rollback/ordem, a suíte reportou 4 falhas por ausência de `_run_manage_py` e `apply_migration_reset`; os 5 testes de planejamento passaram.

GREEN: implementação mínima adicionada; `uv run --group test pytest --nomigrations apps/api/core/tests/management_commands/test_reset_migrations.py -q --no-cov` passou com 9 testes.

Ruff: `uv run ruff check apps/api/core/migration_reset.py apps/api/core/tests/management_commands/test_reset_migrations.py` passou.

## Self-review

- Diff restrito aos dois arquivos da tarefa; `git diff --check` passou.
- Testes patcham apenas seams internos e não executam reset real, banco ou migrations reais.
- A ordem e as mensagens exigidas pelo brief foram preservadas.

## Preocupações

- A suíte completa permanece não executada conforme bloqueio documentado no brief (banco legado `test_base_permission_cache` com sessão ativa).
- Falha durante a reconstrução do banco após geração da baseline não restaura arquivos, conforme comportamento especificado no brief.

## Correção pós-review: limite explícito do banco

- Adicionada guarda antes do snapshot para rejeitar qualquer `plan.database_name` diferente de `base`.
- A confirmação também exige exatamente `base`, independentemente do valor configurado no plano.
- Decisão registrada: `--nomigrations` permanece permitido nos testes internos das Tarefas 1–3 porque a baseline nova ainda não existe; será removido definitivamente na Tarefa 5.
- O ajuste de `close` em `finally` ficou fora do escopo desta tarefa.

### TDD da correção

RED: o teste `test_apply_recusa_plano_de_banco_diferente_de_base_antes_de_mutar` falhou ao alcançar `_run_manage_py`, provando que o plano `outro` não era rejeitado antes da mutação.

GREEN: após a guarda explícita, a suíte focada passou com 10 testes; Ruff também passou.

Comandos executados:

```text
uv run --group test pytest --nomigrations apps/api/core/tests/management_commands/test_reset_migrations.py -q --no-cov
uv run ruff check apps/api/core/migration_reset.py apps/api/core/tests/management_commands/test_reset_migrations.py
```

Self-review: `git diff --check` será executado antes do commit; alteração restrita à guarda, ao teste focado e a este relatório.

## Correção pós-review: conexão efetiva

- Adicionada validação, antes do `TemporaryDirectory`/snapshot, de `connections["default"].settings_dict`: `NAME` deve ser literalmente `base` e `ENGINE` deve terminar em `.postgresql`.
- O helper de teste sincroniza `settings.DATABASES` e o `settings_dict` da conexão para manter os seams determinísticos; o novo teste força conexão SQLite/`outro` com plano `base` e confirma ausência de mutação.
- O escopo Minor de `connection.close()` em `finally` continua fora desta rodada.

### TDD da correção de conexão

RED: `test_apply_recusa_conexao_efetiva_diferente_do_plano_antes_de_mutar` falhou ao alcançar `_run_manage_py` quando a conexão efetiva era SQLite/`outro`.

GREEN: após a validação, `uv run --group test pytest --nomigrations apps/api/core/tests/management_commands/test_reset_migrations.py -q --no-cov` passou com 11 testes e `uv run ruff check apps/api/core/migration_reset.py apps/api/core/tests/management_commands/test_reset_migrations.py` passou.
