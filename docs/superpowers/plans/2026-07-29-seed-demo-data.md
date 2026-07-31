# Demo Seed Data Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an idempotent `seed_demo` management command that creates the generic organization, team, user, and membership needed to exercise the multi-tenant API locally.

**Architecture:** Keep persistence inside one focused Django management command under `apps.api.core`. Use stable natural keys and one `transaction.atomic()` block; existing records are read but never overwritten. The command is blocked when `settings.IN_PRODUCTION` is true unless the operator passes `--allow-production`.

**Tech Stack:** Python 3.12, Django 5.2, PostgreSQL 16, pytest-django, Django management commands.

## Global Constraints

- Command name: `seed_demo`.
- Defaults: organization `Organização Demo` / `demo`, team `Time Demo`, user `demo@example.com` / `Usuário Demo` / `demo123456`, membership role `Papel.PROPRIETARIO`.
- The demo user must have `is_staff=False` and `is_superuser=False`.
- Do not create Knox tokens; authentication remains `POST /auth/login/`.
- A repeated run must not overwrite names, password, flags, role, or extra team associations.
- Missing edges needed by the demo graph may be restored.
- Production must fail before any write unless `--allow-production` is explicit.
- Do not add an extensible seed-provider registry or project-specific domain data.

---

### Task 1: Management command behavior

**Files:**
- Create: `apps/api/core/management/commands/seed_demo.py`
- Create: `apps/api/core/tests/test_seed_demo.py`

**Interfaces:**
- Consumes: `settings.IN_PRODUCTION`, `Organizacao`, `Time`, `Vinculo`, `Papel`, and `Usuario`.
- Produces: CLI command `python manage.py seed_demo [--allow-production]`.

- [ ] **Step 1: Write failing integration tests for creation and output**

Create `apps/api/core/tests/test_seed_demo.py`:

```python
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError

import pytest

from apps.organizacoes.models import Organizacao, Papel, Time, Vinculo
from apps.usuarios.models import Usuario

pytestmark = pytest.mark.django_db


def executar_seed(**options):
    stdout = StringIO()
    call_command("seed_demo", stdout=stdout, **options)
    return stdout.getvalue()


def test_seed_demo_cria_grafo_generico():
    output = executar_seed()

    organizacao = Organizacao.objects.get(slug="demo")
    time = Time.objects.get(organizacao=organizacao, nome="Time Demo")
    usuario = Usuario.objects.get(email="demo@example.com")
    vinculo = Vinculo.objects.get(organizacao=organizacao, usuario=usuario)

    assert organizacao.nome == "Organização Demo"
    assert usuario.first_name == "Usuário"
    assert usuario.last_name == "Demo"
    assert usuario.check_password("demo123456")
    assert usuario.is_staff is False
    assert usuario.is_superuser is False
    assert vinculo.papel == Papel.PROPRIETARIO
    assert list(vinculo.times.all()) == [time]
    assert "demo@example.com" in output
    assert "demo123456" in output
    assert "X-Organization: demo" in output
    assert "POST /auth/login/" in output


def test_seed_demo_bloqueia_producao_antes_de_escrever(settings):
    settings.IN_PRODUCTION = True

    with pytest.raises(CommandError, match="--allow-production"):
        executar_seed()

    assert not Organizacao.objects.filter(slug="demo").exists()
    assert not Usuario.objects.filter(email="demo@example.com").exists()


def test_seed_demo_permite_execucao_deliberada_em_producao(settings):
    settings.IN_PRODUCTION = True

    executar_seed(allow_production=True)

    assert Organizacao.objects.filter(slug="demo").exists()
    assert Usuario.objects.filter(email="demo@example.com").exists()
```

- [ ] **Step 2: Run the new tests and verify the command is missing**

Run:

```bash
uv run pytest apps/api/core/tests/test_seed_demo.py -q
```

Expected: FAIL because Django cannot find a `seed_demo` command.

- [ ] **Step 3: Implement the minimal transactional command**

Create `apps/api/core/management/commands/seed_demo.py`:

```python
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.organizacoes.models import Organizacao, Papel, Time, Vinculo
from apps.usuarios.models import Usuario


class Command(BaseCommand):
    help = "Cria dados genéricos e idempotentes para demonstração local."

    def add_arguments(self, parser):
        parser.add_argument(
            "--allow-production",
            action="store_true",
            help="Permite deliberadamente o seed quando DJANGO_ENVIRONMENT=production.",
        )

    def handle(self, *args, **options):
        if settings.IN_PRODUCTION and not options["allow_production"]:
            raise CommandError(
                "seed_demo é bloqueado em produção; use --allow-production para confirmar."
            )

        with transaction.atomic():
            estados = self._criar_grafo()

        for entidade, criado in estados:
            estado = "criado" if criado else "já existia"
            self.stdout.write(f"{entidade}: {estado}")

        self.stdout.write(
            self.style.SUCCESS(
                "Credenciais: demo@example.com / demo123456\n"
                "Login: POST /auth/login/\n"
                "Tenant: X-Organization: demo"
            )
        )

    def _criar_grafo(self):
        organizacao, organizacao_criada = Organizacao.objects.get_or_create(
            slug="demo",
            defaults={"nome": "Organização Demo"},
        )
        time, time_criado = Time.objects.get_or_create(
            organizacao=organizacao,
            nome="Time Demo",
        )
        usuario, usuario_criado = Usuario.objects.get_or_create(
            email="demo@example.com",
            defaults={
                "first_name": "Usuário",
                "last_name": "Demo",
                "password": make_password("demo123456"),
                "is_staff": False,
                "is_superuser": False,
            },
        )
        vinculo, vinculo_criado = Vinculo.objects.get_or_create(
            organizacao=organizacao,
            usuario=usuario,
            defaults={"papel": Papel.PROPRIETARIO},
        )
        vinculo.times.add(time)

        return [
            ("Organização Demo", organizacao_criada),
            ("Time Demo", time_criado),
            ("Usuário Demo", usuario_criado),
            ("Vínculo Demo", vinculo_criado),
        ]
```

- [ ] **Step 4: Run the creation and production tests**

Run:

```bash
uv run pytest apps/api/core/tests/test_seed_demo.py -q
```

Expected: `3 passed`.

- [ ] **Step 5: Commit the first deliverable**

```bash
git add apps/api/core/management/commands/seed_demo.py apps/api/core/tests/test_seed_demo.py
git commit -m "feat: add demo seed command"
```

---

### Task 2: Idempotency and rollback guarantees

**Files:**
- Modify: `apps/api/core/tests/test_seed_demo.py`

**Interfaces:**
- Consumes: `executar_seed()` and the `seed_demo` command from Task 1.
- Produces: regression coverage for create-only behavior and transaction rollback.

- [ ] **Step 1: Add failing tests for preservation and missing-edge repair**

Append to `apps/api/core/tests/test_seed_demo.py`:

```python
def test_seed_demo_e_idempotente_e_preserva_edicoes_manuais():
    executar_seed()
    organizacao = Organizacao.objects.get(slug="demo")
    time_demo = Time.objects.get(organizacao=organizacao, nome="Time Demo")
    usuario = Usuario.objects.get(email="demo@example.com")
    vinculo = Vinculo.objects.get(organizacao=organizacao, usuario=usuario)
    time_extra = Time.objects.create(organizacao=organizacao, nome="Produto")

    organizacao.nome = "Nome editado"
    organizacao.save(update_fields=["nome"])
    usuario.first_name = "Nome"
    usuario.last_name = "Editado"
    usuario.set_password("senha-editada")
    usuario.save(update_fields=["first_name", "last_name", "password"])
    vinculo.papel = Papel.MEMBRO
    vinculo.save(update_fields=["papel"])
    vinculo.times.set([time_extra])

    output = executar_seed()

    assert Organizacao.objects.filter(slug="demo").count() == 1
    assert Time.objects.filter(organizacao=organizacao, nome="Time Demo").count() == 1
    assert Usuario.objects.filter(email="demo@example.com").count() == 1
    assert Vinculo.objects.filter(organizacao=organizacao, usuario=usuario).count() == 1

    organizacao.refresh_from_db()
    usuario.refresh_from_db()
    vinculo.refresh_from_db()
    assert organizacao.nome == "Nome editado"
    assert usuario.get_full_name() == "Nome Editado"
    assert usuario.check_password("senha-editada")
    assert vinculo.papel == Papel.MEMBRO
    assert set(vinculo.times.all()) == {time_demo, time_extra}
    assert "já existia" in output


def test_seed_demo_reverte_todo_o_grafo_quando_o_banco_falha(monkeypatch):
    def falhar_ao_criar_time(*args, **kwargs):
        raise RuntimeError("falha simulada")

    monkeypatch.setattr(Time.objects, "get_or_create", falhar_ao_criar_time)

    with pytest.raises(RuntimeError, match="falha simulada"):
        executar_seed()

    assert not Organizacao.objects.filter(slug="demo").exists()
    assert not Usuario.objects.filter(email="demo@example.com").exists()
```

- [ ] **Step 2: Run the regression tests**

Run:

```bash
uv run pytest apps/api/core/tests/test_seed_demo.py -q
```

Expected: `5 passed`. If either test fails, make the smallest correction in
`seed_demo.py` shown in Task 1, preserving the create-only calls and the single
`transaction.atomic()` boundary.

- [ ] **Step 3: Run lint for the command and its tests**

Run:

```bash
uv run ruff check apps/api/core/management/commands/seed_demo.py apps/api/core/tests/test_seed_demo.py
```

Expected: exit code `0`.

- [ ] **Step 4: Commit the guarantees**

```bash
git add apps/api/core/management/commands/seed_demo.py apps/api/core/tests/test_seed_demo.py
git commit -m "test: cover demo seed invariants"
```

---

### Task 3: Local development documentation

**Files:**
- Modify: `docs/how-to/desenvolvimento-local.md`
- Modify: `docs/tutorial/primeiro-ambiente.md`

**Interfaces:**
- Consumes: CLI `uv run python manage.py seed_demo`.
- Produces: onboarding instructions for credentials, login, and tenant header.

- [ ] **Step 1: Expand the local-development how-to**

Replace `docs/how-to/desenvolvimento-local.md` with:

````markdown
# Desenvolvimento local

Execute `make install` para sincronizar dependências e `make hooks` para instalar
os hooks de qualidade. Suba PostgreSQL e Redis, aplique as migrações e crie os
dados genéricos:

```bash
make up
make migrate
uv run python manage.py seed_demo
```

O comando cria:

- organização `Organização Demo`, slug `demo`;
- time `Time Demo`;
- usuário `demo@example.com`, senha `demo123456`;
- vínculo de proprietário do usuário com a organização e o time.

Obtenha um token descartável pela autenticação real:

```http
POST /auth/login/
Content-Type: application/json

{"username": "demo@example.com", "password": "demo123456"}
```

Nas rotas isoladas por tenant, envie o token e o header:

```http
Authorization: Bearer <token>
X-Organization: demo
```

O seed é idempotente e não substitui dados existentes. Em produção, ele é
bloqueado por padrão; `--allow-production` exige confirmação deliberada do
operador e não deve aparecer em scripts de deploy.

Antes de enviar uma alteração, rode:

```bash
make lint
make test
make docs
```

Os commits devem seguir Conventional Commits, por exemplo `feat: adiciona filtro`.
````

- [ ] **Step 2: Add the seed to the first-environment tutorial**

In `docs/tutorial/primeiro-ambiente.md`, insert this step immediately after the
migration step:

```markdown
5. Crie os dados genéricos com `uv run python manage.py seed_demo`.
6. Inicie a API com `uv run python manage.py runserver`.
```

Renumber the existing server step and add this sentence before the final
paragraph:

```markdown
Use `demo@example.com` / `demo123456` no login e envie
`X-Organization: demo` nas rotas isoladas por tenant.
```

- [ ] **Step 3: Verify command discovery, tests, and docs**

Run:

```bash
uv run python manage.py help seed_demo
uv run pytest apps/api/core/tests/test_seed_demo.py -q
uv run mkdocs build --strict
```

Expected: command help exits `0`, `5 passed`, and MkDocs exits `0`.

- [ ] **Step 4: Commit the onboarding documentation**

```bash
git add docs/how-to/desenvolvimento-local.md docs/tutorial/primeiro-ambiente.md
git commit -m "docs: add demo seed onboarding"
```

---

### Task 4: Seed feature verification

**Files:**
- Verify only; no expected file changes.

**Interfaces:**
- Consumes: all deliverables from Tasks 1–3.
- Produces: evidence that the seed feature is ready to integrate.

- [ ] **Step 1: Start the required services**

Run:

```bash
docker compose up -d db redis
```

Expected: both services become healthy.

- [ ] **Step 2: Run focused quality checks**

Run:

```bash
uv run ruff check apps/api/core/management/commands/seed_demo.py apps/api/core/tests/test_seed_demo.py
uv run pytest apps/api/core/tests/test_seed_demo.py -q
uv run mkdocs build --strict
```

Expected: Ruff exits `0`, `5 passed`, and MkDocs exits `0`.

- [ ] **Step 3: Run the complete suite with the repository test environment**

Run:

```bash
DJANGO_ENVIRONMENT=test \
DJANGO_SECRET_KEY=test-secret \
DATABASE_NAME=base \
DATABASE_USER=postgres \
DATABASE_PASSWORD=postgres \
DATABASE_HOST=127.0.0.1 \
DATABASE_PORT=5432 \
REDIS_HOST=127.0.0.1 \
REDIS_PORT=6379 \
uv run pytest
```

Expected: full suite exits `0`.
