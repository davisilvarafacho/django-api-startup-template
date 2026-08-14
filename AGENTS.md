# Repository Guidelines

## Project Structure & Module Organization

This is a Django REST Framework API template. The authoritative map of the
directory tree is `docs/reference/estrutura-de-diretorios.md`; read it before
creating a directory or deciding where new code belongs. The short version:

- `api/` — Django project configuration only (settings, routing, ASGI/WSGI,
  Celery, per-environment app lists). No business logic.
- `apps/` — every installed app. `apps/api/` is a *grouper*, not an app: it has
  no `apps.py` and is absent from `BUSINESS_APPS`, so its children sit directly
  inside it. A real parent app nests its children under `subapps/` instead.
- `internal_frameworks/` — infrastructure written in-house (guardrails,
  permission cache, sensitive fields). No domain models here.
- `utils/` — generic, stateless helpers.
- `tests/` — only what spans more than one app: `architecture/` and `support/`.
- `observability/`, `docker/`, `.devcontainer/`, `.ci/` — operational config.
- `docs/` — documentation, built with MkDocs.

Keep each app's tests in its `tests/` package and migrations in `migrations/`.
Do not treat `.examples/` as part of the primary test suite.

## Where to Write Documentation

Split by genre of text, not by audience. Content serving both developers and
agents has a single copy in `docs/`, pointed to from here.

- `docs/` — project documentation, Diátaxis-organized, validated by
  `mkdocs build --strict`.
- `AGENTS.md`, `CLAUDE.md` — routers: auto-loaded, they point at everything else.
- `.ai/` — instructions meant only for agents.
- `.claude/skills/` — specialized instructions, loaded on demand by topic.

**Brainstorming output goes to `.ai/brainstorming/`**: design documents in
`spec/`, implementation plans in `plan/`, named `YYYY-MM-DD-topic.md`. Never
write a spec or a plan under `docs/` — they are work records, not product
documentation. `docs/superpowers/` holds the specs and plans that predate this
convention; it is history, not a destination for new files.

## Import Architecture

Import objects directly from the module that declares them; do not create a
`shared.py` solely to re-export symbols. Imports between apps and module types
are otherwise unrestricted. When a concrete circular import occurs, defer only
one edge with a local import in the smallest runtime scope. Keep type-only
imports under `TYPE_CHECKING`. Django relational fields must reference models
with strings, such as `"organizacoes.Organizacao"`, so model loading does not
depend on import order.

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
