# Resetar migrations locais

Este procedimento descarta o schema `public` do banco configurado e substitui
as migrations dos apps em `BUSINESS_APPS`. Use apenas quando o histórico de
upgrade também for descartável. O comando recusa produção.

## Inspecionar sem alterar

```bash
make reset-migrations
```

Confira o banco, os arquivos marcados como `REMOVER` e a migration manual
marcada como `PRESERVAR`.

## Executar

```bash
make reset-migrations RESET_MIGRATIONS_ARGS='--apply --confirm-database base'
```

Troque `base` somente quando `DATABASE_NAME` tiver outro valor e confirme o
nome exatamente. A operação não cria backup dos dados.

## Verificar

```bash
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py showmigrations --plan
make test
```

Se a geração falhar antes do reset do schema, os arquivos antigos são
restaurados. Depois que o schema for removido, corrija a causa indicada e rode
`make migrate`; as migrations novas permanecem no working tree.
