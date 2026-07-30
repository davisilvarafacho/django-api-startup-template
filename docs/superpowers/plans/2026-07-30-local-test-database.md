# Banco local para a suíte de testes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fazer `make test` conectar-se por padrão ao PostgreSQL local iniciado pelo Docker Compose.

**Architecture:** O Makefile será a única camada modificada. Ele exportará valores condicionais que espelham os defaults de `docker-compose.yml`; valores já presentes no ambiente permanecem inalterados e são consumidos pelo `pytest` ao carregar as settings Django.

**Tech Stack:** GNU Make, pytest, Django 5.2, PostgreSQL 16 via Docker Compose.

## Global Constraints

- Usar `base`, `postgres`, `postgres`, `127.0.0.1` e `5432` exatamente como defaults de `docker-compose.yml`.
- Uma variável `DATABASE_*` explicitamente fornecida pelo desenvolvedor deve prevalecer sobre o default.
- Não alterar `api/settings.py`, `docker-compose.yml`, dependências ou arquivos de segredo.
- A validação completa é `make test`, com PostgreSQL e Redis locais em execução.

---

### Task 1: Tornar o alvo `test` autocontido para o Compose local

**Files:**
- Modify: `Makefile:1-38`
- Test: `Makefile` via `make -pn DATABASE_PORT=5544` e `make test`

**Interfaces:**
- Consumes: variáveis opcionais `DATABASE_NAME`, `DATABASE_USER`, `DATABASE_PASSWORD`, `DATABASE_HOST` e `DATABASE_PORT` do ambiente do Make.
- Produces: as mesmas cinco variáveis exportadas para `uv run --group test pytest`.

- [ ] **Step 1: Reproduzir a falha sem configuração de banco**

Run:

```bash
env -u DATABASE_NAME -u DATABASE_USER -u DATABASE_PASSWORD -u DATABASE_HOST -u DATABASE_PORT make test
```

Expected: os testes que usam banco falham na preparação com `DATABASE_NAME`
igual a `None`.

- [ ] **Step 2: Declarar defaults condicionais e exportá-los**

Adicionar antes do alvo `test` no `Makefile`:

```make
DATABASE_NAME ?= base
DATABASE_USER ?= postgres
DATABASE_PASSWORD ?= postgres
DATABASE_HOST ?= 127.0.0.1
DATABASE_PORT ?= 5432

export DATABASE_NAME DATABASE_USER DATABASE_PASSWORD DATABASE_HOST DATABASE_PORT
```

Não modificar a receita do alvo `test`:

```make
test: ## Roda a suíte com cobertura
	uv run --group test pytest
```

- [ ] **Step 3: Verificar que uma sobrescrita continua sendo respeitada**

Run:

```bash
make -pn DATABASE_PORT=5544 | rg '^DATABASE_PORT = 5544$'
```

Expected: `DATABASE_PORT = 5544`; a atribuição de linha de comando permanece
a fonte de `DATABASE_PORT` para o subprocesso.

- [ ] **Step 4: Executar a suíte completa contra o Compose local**

Run:

```bash
env -u DATABASE_NAME -u DATABASE_USER -u DATABASE_PASSWORD -u DATABASE_HOST -u DATABASE_PORT make test
```

Expected: `123 passed` e nenhum erro de setup por `DATABASE_NAME=None`.

- [ ] **Step 5: Revisar e registrar a alteração**

Run:

```bash
git diff --check
git status --short
git add Makefile
git commit -m "fix(test): configure local postgres defaults"
```

Expected: apenas `Makefile` entra neste commit de implementação.
