# Resetar migrations locais

Este procedimento descarta o schema `public` do banco configurado e substitui
as migrations dos apps em `BUSINESS_APPS`. Use apenas quando o histórico de
upgrade também for descartável. A aplicação só é aceita com
`DJANGO_ENVIRONMENT=development`.

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

O comando só opera quando `DATABASE_NAME=base` e a confirmação literal é
`--confirm-database base`; qualquer outro nome é recusado. A operação não cria
backup dos dados.

## Verificar

```bash
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py showmigrations --plan
make test
```

Se a geração falhar antes do reset do schema, os arquivos antigos são
restaurados. Depois que o schema for removido, corrija a causa indicada e rode
`make migrate`; as migrations novas permanecem no working tree.
