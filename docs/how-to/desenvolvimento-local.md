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
