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

## Ambiente com Dev Containers

O diretório `.devcontainer/` abre o editor no serviço `app` e sobe também
PostgreSQL 16, Redis 7, um worker Celery e o nginx. O ambiente usa somente
credenciais locais versionadas e não lê o `.env` do host.

Depois da criação:

```bash
make migrate
uv run python manage.py seed_demo
make run
```

A API responde em `http://localhost:8000` **pelo nginx** — o `RUN_HOST=0.0.0.0`
do compose faz o `runserver` escutar em todas as interfaces para o proxy
alcançá-lo. Ver [Proxy reverso com nginx](proxy-nginx.md).

Para inspecionar os serviços a partir do host:

```bash
docker compose -f .devcontainer/docker-compose.yml ps
docker compose -f .devcontainer/docker-compose.yml logs -f worker
```

O Celery Beat e a stack de observabilidade não sobem automaticamente com o
devcontainer. Para conectar a stack opcional à rede dele, execute no host, depois
que o devcontainer estiver aberto:

```bash
make dev-obs-up
```

Então, no terminal do devcontainer, use `make run-observed` para enviar traces
ao Tempo. O arquivo JSON consumido pelo Alloy já fica habilitado nesse ambiente.
Para observar também as tasks, pare primeiro o worker automático no host e inicie
um worker instrumentado dentro do devcontainer:

```bash
docker compose -f .devcontainer/docker-compose.yml stop worker
make worker-observed
```

Ao terminar, execute `make dev-obs-down` no host. Consulte
[Subir a observabilidade localmente](observabilidade-local.md) para validar
métricas, logs e traces no Grafana.
