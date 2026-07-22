# Primeiro ambiente

1. Instale as dependências com `uv sync`.
2. Copie `.env.example` para `.env` e preencha os valores necessários.
3. Suba PostgreSQL e Redis com `docker compose up -d db redis`.
4. Execute `uv run python manage.py migrate`.
5. Inicie a API com `uv run python manage.py runserver`.

Consulte a referência interativa em `/api/docs/` quando a aplicação estiver em
execução.
