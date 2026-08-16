# Resetar migrations locais

Este procedimento descarta o schema `public` do banco configurado e substitui
as migrations dos apps em `BUSINESS_APPS`. Use apenas quando o histórico de
upgrade também for descartável. A aplicação só é aceita com
`DJANGO_ENVIRONMENT=development`.

## Inspecionar sem alterar

```bash
make reset-migrations
```

A saída lista seis etapas, incluindo as duas verificações de
`makemigrations --check --dry-run`.
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
restaurados. Nas etapas seguintes, as migrations novas permanecem no working
tree. Recupere conforme a etapa indicada no erro:

- `recriar schema public`: corrija a causa e repita o reset protegido completo
  com
  `make reset-migrations RESET_MIGRATIONS_ARGS='--apply --confirm-database base'`;
- `aplicar migrations`: corrija a causa e execute `make migrate`;
- `validar models e migrations`: execute
  `uv run python manage.py makemigrations --check --dry-run`;
- `exibir plano aplicado`: execute
  `uv run python manage.py showmigrations --plan`.

A recriação do schema ocorre em uma transação. Se ela falhar, o schema e o
histórico anteriores podem ser restaurados, mesmo que as migrations novas
continuem no working tree.
