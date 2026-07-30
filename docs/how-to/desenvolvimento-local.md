# Desenvolvimento local

Execute `make install` para sincronizar dependências e `make hooks` para instalar
os hooks de qualidade. Antes de enviar uma alteração, rode:

```bash
make lint
make test
make docs
```

Os commits devem seguir Conventional Commits, por exemplo `feat: adiciona filtro`.

## Ambiente com Dev Containers

O diretório `.devcontainer/` abre o editor no serviço `app` e sobe também
PostgreSQL 16, Redis 7 e um worker Celery. O ambiente usa somente credenciais
locais versionadas e não lê o `.env` do host.

Depois da criação:

```bash
make migrate
uv run python manage.py seed_demo
make run
```

Para inspecionar os serviços a partir do host:

```bash
docker compose -f .devcontainer/docker-compose.yml ps
docker compose -f .devcontainer/docker-compose.yml logs -f worker
```

O Celery Beat e a stack de observabilidade não fazem parte do devcontainer.
