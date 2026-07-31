# Primeiro ambiente

1. Instale as dependências com `uv sync`.
2. Copie `.env.example` para `.env` e preencha os valores necessários.
3. Suba PostgreSQL e Redis com `docker compose up -d db redis`.
4. Execute `uv run python manage.py migrate`.
5. Crie os dados genéricos com `uv run python manage.py seed_demo`.
6. Inicie a API com `uv run python manage.py runserver`.

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
