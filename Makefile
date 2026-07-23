.PHONY: help install hooks up down migrate run worker beat test lint format check precommit shell docs docs-serve commitlint version-check obs-up obs-down

help: ## Lista os comandos disponíveis
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "\033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Instala dependências (inclui grupo dev)
	uv sync

hooks: ## Ativa os hooks de pre-commit
	uv run pre-commit install
	npm install

up: ## Sobe Postgres + Redis (docker compose)
	docker compose up -d db redis

down: ## Derruba os serviços do docker compose
	docker compose down

obs-up: ## Sobe a stack de observabilidade (Grafana, Tempo, Loki, Prometheus)
	docker compose -f docker-compose.observability.yml up -d

obs-down: ## Derruba a stack de observabilidade
	docker compose -f docker-compose.observability.yml down

migrate: ## Aplica as migrações
	uv run python manage.py migrate

run: ## Sobe o servidor de desenvolvimento
	uv run python manage.py runserver

worker: ## Sobe o worker do Celery
	uv run celery -A api worker -l info

beat: ## Sobe o beat do Celery (agendador via banco)
	uv run celery -A api beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler

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
