# Primeiro ambiente

1. Instale as dependências com `uv sync`.
2. Copie `.env.example` para `.env` e preencha os valores necessários.
3. Suba PostgreSQL e Redis com `make up`.
4. Provisione o login web local com `make db-roles`.
5. Aplique as migrations com `make migrate`.
6. Crie os dados genéricos com `uv run python manage.py seed_demo`.
7. Inicie a API com `make run`.

`db-roles` pode ser repetido. Configure `DATABASE_ADMIN_USER` com o
administrador que criou o volume e mantenha `DATABASE_USER` distinto dele.

Use `demo@example.com` / `demo123456` no login e envie
`X-Organization: demo` nas rotas isoladas por tenant.

Consulte a referência interativa em `/api/docs/` quando a aplicação estiver em
execução.

## Alternativa: Dev Containers

Com Docker e uma ferramenta compatível com a especificação Dev Containers:

1. Abra o repositório no container.
2. Aguarde `uv sync --frozen --group dev && npm ci`.
3. Execute `make migrate`.
4. Execute `uv run python manage.py seed_demo`.
5. Inicie a API com `make run`.

PostgreSQL, Redis e o worker Celery já acompanham o ambiente. Migrações, seed e
servidor permanecem explícitos; abrir o container não grava dados da aplicação.
