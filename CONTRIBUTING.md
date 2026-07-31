# Contribuindo

## Ambiente

```bash
uv sync                       # instala dependências (inclui grupo dev)
make docs                     # valida a documentação
uv run pre-commit install     # ativa os hooks de pre-commit
cp .env.example .env          # preencha DATABASE_* / REDIS_*
docker compose up -d db redis # sobe Postgres + Redis
uv run python manage.py migrate
```

Atalhos no `Makefile` — rode `make help` para ver todos.

## Padrões

- **Commits:** seguem [Conventional Commits](https://www.conventionalcommits.org)
  (`feat:`, `fix:`, `docs:`, `refactor:`, `chore:`, `test:`, `perf:`, `build:`, `ci:`).
  O hook de `commit-msg` e a CI validam esse formato.
- **Versionamento:** [SemVer](https://semver.org), casado com a versão do schema OpenAPI.
- **Changelog:** formato [Keep a Changelog](https://keepachangelog.com).
- **Lint/format:** `ruff` (via pre-commit). Rode `make lint` e `make format`.
- **Docstrings:** convenção Google (`ruff pydocstyle`).

## Testes

```bash
make test          # pytest com cobertura, sem migrations até o reset pré-lançamento
```

Os testes rodam contra PostgreSQL e Redis (via `docker compose up -d db redis`).
Cobertura de código **novo** é exigida em 80% no PR (Codecov, gate não-retroativo).

## Antes de abrir o PR

- `make lint` e `make test` verdes.
- `make docs` verde.
- Respeite a política temporária de não alterar migrations; o gate
  `makemigrations --check --dry-run` volta após o reset pré-lançamento.
- Preencha o template de PR.
