# Repository Guidelines

## Project Structure & Module Organization

This is a Django REST Framework API template. Django project configuration lives
in `api/` (settings, routing, ASGI/WSGI, Celery). Reusable application code lives
under `apps/`: `apps/api/` contains shared API modules, while `apps/usuarios/`
and `apps/organizacoes/` are domain apps.
Keep each app's tests in its `tests/` package and migrations in `migrations/`.
Shared utilities are in `utils/`; operational configuration is in
`observability/`; documentation is built from `docs/` with MkDocs. Do not treat
`examples/` as part of the primary test suite.

## Build, Test, and Development Commands

Use `uv` for dependencies and execution:

```bash
uv sync                         # install locked development dependencies
cp .env.example .env            # create local configuration (never commit it)
make up                         # start PostgreSQL and Redis
make migrate && make run        # apply migrations and start Django
make test                        # run pytest with coverage
make lint && make format         # check and apply Ruff formatting
make docs                        # strictly validate the MkDocs site
```

Run `make help` for all shortcuts. For dependency changes, use `uv add` or
`uv remove` and commit both `pyproject.toml` and `uv.lock`.

## Coding Style & Naming Conventions

Target Python 3.12 style and use four-space indentation. Ruff is authoritative:
it formats code, sorts imports (Django, DRF, third-party, then `apps`), and
enforces the configured lint rules; its maximum line length is 150. Use
`snake_case` for functions, modules, variables, and test names; use `PascalCase`
for classes. Name tests `test_*.py`, with focused functions such as
`test_returns_404_for_unknown_tenant`. Use Google-style docstrings when useful.

## Testing Guidelines

Pytest, pytest-django, and pytest-cov power the suite. Tests require local
PostgreSQL and Redis (`make up`); Celery tasks run eagerly under the test
settings. Add coverage for new behavior—PRs require 80% coverage for new code
through Codecov. Also run `uv run python manage.py makemigrations --check --dry-run`
when models change.

## Commits & Pull Requests

Use Conventional Commits, as enforced by hooks and CI: `feat:`, `fix:`, `docs:`,
`test:`, `refactor:`, `chore:`, `build:`, `ci:`, or `perf:`. Keep commits narrow
and imperative (for example, `fix: validate tenant header`). PRs must explain
what and why, select the change type, pass lint/tests/docs, have no pending
migrations, and update documentation or ADRs when applicable. Include API
examples or screenshots only for externally visible changes.

## Security & Configuration

Start from `.env.example`; keep credentials, API keys, and production settings
out of Git. Treat storage migrations and key rotation commands as operational
changes: use their dry-run modes and follow the relevant documentation first.
