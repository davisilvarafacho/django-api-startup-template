.PHONY: help install hooks up down stack migrate run worker beat test lint format check precommit shell docs docs-serve commitlint version-check obs-up obs-down nginx-test nginx-reload

help: ## Lista os comandos disponíveis
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "\033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Instala dependências (inclui grupo dev)
	uv sync

hooks: ## Ativa os hooks de pre-commit
	uv run pre-commit install
	npm install

up: ## Sobe Postgres + Redis (docker compose)
	docker compose up -d db redis

stack: ## Sobe a stack completa, com a API atrás do nginx (http://localhost:8000)
	docker compose up -d --build

down: ## Derruba os serviços do docker compose
	docker compose down

nginx-test: ## Valida a configuração do nginx (os dois ambientes) sem subir a stack
	@for ambiente in production development; do \
		echo "==> $$ambiente"; \
		docker run --rm \
			--add-host web:127.0.0.1 --add-host app:127.0.0.1 \
			-v "$(CURDIR)/docker/nginx/nginx.conf:/etc/nginx/nginx.conf:ro" \
			-v "$(CURDIR)/docker/nginx/snippets:/etc/nginx/snippets:ro" \
			-v "$(CURDIR)/docker/nginx/sites/$$ambiente:/etc/nginx/conf.d:ro" \
			nginx:1.29-alpine nginx -t || exit 1; \
	done

nginx-reload: ## Recarrega o nginx sem derrubar conexões (após editar docker/nginx/)
	docker compose exec nginx nginx -t
	docker compose exec nginx nginx -s reload

obs-up: ## Sobe a stack de observabilidade (Grafana, Tempo, Loki, Prometheus)
	docker compose -f docker-compose.observability.yml up -d

obs-down: ## Derruba a stack de observabilidade
	docker compose -f docker-compose.observability.yml down

migrate: ## Aplica as migrações
	uv run python manage.py migrate

# Dentro de um container (dev container) o runserver precisa escutar em 0.0.0.0
# para o nginx alcançá-lo; no host, 127.0.0.1 evita expor a API na rede local.
RUN_HOST ?= 127.0.0.1
RUN_PORT ?= 8000

run: ## Sobe o servidor de desenvolvimento (RUN_HOST/RUN_PORT ajustam o bind)
	uv run python manage.py runserver $(RUN_HOST):$(RUN_PORT)

worker: ## Sobe o worker do Celery
	uv run celery -A api worker -l info

beat: ## Sobe o beat do Celery (agendador via banco)
	uv run celery -A api beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler

DATABASE_NAME ?= base
DATABASE_USER ?= postgres
DATABASE_PASSWORD ?= postgres
DATABASE_HOST ?= 127.0.0.1
DATABASE_PORT ?= 5432

export DATABASE_NAME DATABASE_USER DATABASE_PASSWORD DATABASE_HOST DATABASE_PORT

test: ## Roda a suíte com cobertura
	uv run --group test pytest

lint: ## Checa lint (ruff)
	uv run ruff check .

format: ## Formata o código (ruff)
	uv run ruff format .

check: ## Django system checks (deploy)
	uv run python manage.py check --deploy

precommit: ## Roda os hooks de pre-commit em todos os arquivos
	uv run pre-commit run --all-files

docs: ## Gera a documentação estática
	uv run mkdocs build --strict

docs-serve: ## Sobe a documentação localmente
	uv run mkdocs serve

commitlint: ## Valida uma mensagem de commit
	npx --no-install commitlint --edit .git/COMMIT_EDITMSG

version-check: ## Confere se a versão do pacote e do OpenAPI são iguais
	uv run python scripts/check_version.py

shell: ## Abre o shell interativo do Django
	uv run python manage.py shell
