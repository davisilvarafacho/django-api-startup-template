# Primeiro ambiente

1. Instale as dependências com `uv sync`.
2. Copie `.env.example` para `.env` e preencha os valores necessários.
3. Suba PostgreSQL e Redis com `make up`.
4. Provisione os logins e roles locais com `make billing-bootstrap`.
5. Aplique as migrations com `make migrate`.
6. Crie os dados genéricos com `uv run python manage.py seed_demo`.
7. Inicie a API com `make run`.

`billing-bootstrap` pode ser repetido e também atualiza um volume criado por
uma versão antiga do template. Se o volume já tiver dados, faça e teste um
backup antes. Configure `POSTGRES_ADMIN_USER` com o administrador que criou
esse volume e mantenha distintos os logins `DATABASE_USER`,
`BILLING_INGRESS_DATABASE_USER` e
`BILLING_INGRESS_WORKER_DATABASE_USER`; mudar apenas
`BILLING_DATABASE_MODE` não troca a credencial do processo.

Use `demo@example.com` / `demo123456` no login e envie
`X-Organization: demo` nas rotas isoladas por tenant.

Consulte a referência interativa em `/api/docs/` quando a aplicação estiver em
execução.

## Alternativa: Dev Containers

Com Docker e uma ferramenta compatível com a especificação Dev Containers:

1. No host, execute
   `make billing-bootstrap BILLING_COMPOSE_FILE=.devcontainer/docker-compose.yml`.
2. Abra o repositório no container.
3. Aguarde `uv sync --frozen --group dev && npm ci`.
4. Execute `make migrate`.
5. Execute `uv run python manage.py seed_demo`.
6. Inicie a API com `make run`.

PostgreSQL, Redis e o worker Celery já acompanham o ambiente. Migrações, seed e
servidor permanecem explícitos; abrir o container não grava dados da aplicação.
