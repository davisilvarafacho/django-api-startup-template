# Relatório — Tarefa 1: planejar o reset sem efeitos colaterais

## Implementação

Criado o módulo puro `apps/api/core/migration_reset.py`, com:

- `MigrationResetError` para recusar configurações inseguras;
- `MigrationResetPlan` imutável, contendo base, banco, apps, diretórios, remoções e preservações;
- `build_migration_reset_plan()` descobrindo somente apps listados em `BUSINESS_APPS` e migrations próprias;
- validação de backend PostgreSQL, nome do banco, localização sob `apps/`, diretório `migrations/` não simbólico para fora do app, `__init__.py`, nomes de arquivos e migration manual preservada do core.

Criados cinco testes focados em `apps/api/core/tests/management_commands/test_reset_migrations.py`, usando apps falsos em `tmp_path` e sem tocar em migrations reais ou banco.

## Arquivos

- `apps/api/core/migration_reset.py`
- `apps/api/core/tests/management_commands/test_reset_migrations.py`
- `.superpowers/sdd/task-1-report.md`

## TDD — RED

Comando:

```text
uv run --group test pytest --nomigrations apps/api/core/tests/management_commands/test_reset_migrations.py -q --no-cov
```

Resultado relevante:

```text
ImportError: cannot import name 'migration_reset' from 'apps.api.core'
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
```

Falha esperada porque os testes foram escritos antes do módulo de produção existir.

## TDD — GREEN

Após a implementação, o mesmo comando resultou em:

```text
..... [100%]
5 passed, 1 warning in 0.08s
```

O warning de `SECRET_KEY` temporária é do ambiente de testes.

## Self-review

- A implementação apenas constrói e retorna dados; não remove arquivos, executa SQL nem acessa/destrói banco.
- `__init__.py` e migrations preservadas não entram em `remove`.
- Caminhos são resolvidos antes das validações para impedir escapes por symlink.
- O diff foi verificado com `git diff --check`.

## Ruff

```text
uv run ruff check apps/api/core/migration_reset.py apps/api/core/tests/management_commands/test_reset_migrations.py
All checks passed!
```

## Preocupações

- A suíte completa não foi executada; a baseline do brief informa bloqueio pelo banco legado `test_base_permission_cache` com sessão ativa. Nenhum comando tentou modificar ou remover esse banco.
- O warning de `SECRET_KEY` temporária não afeta os testes focados.
