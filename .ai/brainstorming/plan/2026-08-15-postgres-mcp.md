# PostgreSQL MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Configurar no Codex local um MCP DBHub fixado em versão exata, conectado a `base` por um papel PostgreSQL tecnicamente incapaz de escrever.

**Architecture:** O PostgreSQL será a barreira de segurança principal por meio de `codex_readonly`; o classificador read-only do DBHub será apenas defesa adicional. Um TOML local sem segredo define source e tools, enquanto a senha aleatória fica somente no `~/.codex/config.toml` por meio do ambiente do servidor STDIO.

**Tech Stack:** PostgreSQL 16, Docker Compose, Codex CLI MCP, Node.js/npx, `@bytebase/dbhub@1.2.0`, TOML.

## Global Constraints

- Executar este plano somente depois da nova baseline estar aplicada em `base`.
- Usar o nome MCP exato `postgres_best_django_api_template`.
- Fixar `@bytebase/dbhub@1.2.0`; não usar `@latest`.
- Usar STDIO; não publicar servidor HTTP nem abrir uma nova porta.
- O DBHub recebe `readonly = true` e `max_rows = 500` no TOML.
- O papel `codex_readonly` usa `NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS`.
- O papel recebe somente grants explícitos de `CONNECT`, `USAGE` e `SELECT`; não recebe ownership, `CREATE`, DML ou uso de sequences.
- A senha é aleatória, nunca impressa, nunca escrita no repositório e removida do shell ao final.
- O TOML local fica em `/home/rafacho/.codex/dbhub-postgres.toml` com modo `0600` e sem senha literal.
- A sessão atual não ganhará a tool dinamicamente; a verificação MCP funcional termina em uma sessão nova.

---

## File map

| Arquivo/estado | Responsabilidade |
|---|---|
| `/home/rafacho/.codex/dbhub-postgres.toml` | Source `base` e tools DBHub; local, modo `0600`, sem segredo. |
| `/home/rafacho/.codex/config.toml` | Registro STDIO do Codex e variável secreta `DBHUB_POSTGRES_PASSWORD`. |
| Papel PostgreSQL `codex_readonly` | Impedir escrita e privilégios administrativos no servidor. |

### Task 1: Criar a configuração DBHub local sem segredo

**Files:**
- Create: `/home/rafacho/.codex/dbhub-postgres.toml`

**Interfaces:**
- Consumes: env var `DBHUB_POSTGRES_PASSWORD` supplied by Codex.
- Produces: DBHub source `local_postgres` and tools `execute_sql`/`search_objects`.

- [ ] **Step 1: Confirmar versão e ausência do servidor**

Run:

```bash
npm view @bytebase/dbhub version
codex mcp list
docker compose exec -T db psql -U postgres -d postgres -Atc "SELECT rolname FROM pg_roles WHERE rolname = 'codex_readonly';"
```

Expected: npm prints `1.2.0`; `postgres_best_django_api_template` is absent; the role query prints nothing. If that exact MCP name or role exists, inspect it and stop instead of sobrescrevê-lo.

- [ ] **Step 2: Criar o TOML por patch**

Usar `apply_patch` para criar `/home/rafacho/.codex/dbhub-postgres.toml` com o conteúdo exato:

```toml
[[sources]]
id = "local_postgres"
dsn = "postgres://codex_readonly:${DBHUB_POSTGRES_PASSWORD}@127.0.0.1:5432/base?sslmode=disable"
connection_timeout = 5
query_timeout = 10

[[tools]]
name = "execute_sql"
source = "local_postgres"
readonly = true
max_rows = 500

[[tools]]
name = "search_objects"
source = "local_postgres"
```

- [ ] **Step 3: Restringir permissão do arquivo e validar sintaxe pelo DBHub**

Run:

```bash
chmod 600 /home/rafacho/.codex/dbhub-postgres.toml
stat -c '%a %n' /home/rafacho/.codex/dbhub-postgres.toml
DBHUB_POSTGRES_PASSWORD=validation-only timeout 10s npx -y @bytebase/dbhub@1.2.0 --transport stdio --config /home/rafacho/.codex/dbhub-postgres.toml
```

Expected: `stat` prints `600`; DBHub parses the TOML and then either waits until `timeout` exits 124 or reports only connection refusal for the intentionally invalid password. It must not report TOML/schema/unknown-option errors.

### Task 2: Criar o papel read-only e registrar o servidor no Codex

**Files:**
- Modify: `/home/rafacho/.codex/config.toml` via `codex mcp add`
- Modify: PostgreSQL cluster role and grants for database `base`

**Interfaces:**
- Consumes: `dbhub-postgres.toml` from Task 1 and the owner local `postgres`.
- Produces: role `codex_readonly` and MCP config `postgres_best_django_api_template`.

- [ ] **Step 1: Confirmar que a baseline existe antes dos grants**

Run:

```bash
docker compose exec -T db psql -U postgres -d base -Atc "SELECT count(*) FROM django_migrations;"
```

Expected: a positive integer; absence of `django_migrations` blocks this plan.

- [ ] **Step 2: Criar senha, role, grants e MCP numa única shell**

Run the following as one command so the secret never needs to cross tool calls:

```bash
set -e
MCP_POSTGRES_ROLE_PASSWORD=$(openssl rand -hex 24)
export MCP_POSTGRES_ROLE_PASSWORD

docker compose exec -T db psql \
  -U postgres -d postgres -v ON_ERROR_STOP=1 \
  -v role_password="$MCP_POSTGRES_ROLE_PASSWORD" <<'SQL'
CREATE ROLE codex_readonly WITH
    LOGIN PASSWORD :'role_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
GRANT CONNECT ON DATABASE base TO codex_readonly;
SQL

docker compose exec -T db psql -U postgres -d base -v ON_ERROR_STOP=1 <<'SQL'
GRANT USAGE ON SCHEMA public TO codex_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO codex_readonly;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
    GRANT SELECT ON TABLES TO codex_readonly;
SQL

codex mcp add postgres_best_django_api_template \
  --env DBHUB_POSTGRES_PASSWORD="$MCP_POSTGRES_ROLE_PASSWORD" \
  -- npx -y @bytebase/dbhub@1.2.0 \
  --transport stdio \
  --config /home/rafacho/.codex/dbhub-postgres.toml

unset MCP_POSTGRES_ROLE_PASSWORD
```

Expected: SQL exits 0; `codex mcp add` reports the new server; no command prints the password.

- [ ] **Step 3: Confirmar os atributos e grants sem revelar credenciais**

Run:

```bash
docker compose exec -T db psql -U postgres -d postgres -Atc "SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls FROM pg_roles WHERE rolname = 'codex_readonly';"
docker compose exec -T db psql -U postgres -d base -Atc "SELECT privilege_type FROM information_schema.role_table_grants WHERE grantee = 'codex_readonly' GROUP BY privilege_type ORDER BY privilege_type;"
codex mcp get postgres_best_django_api_template
codex mcp list
```

Expected: role row is `codex_readonly|f|f|f|f|f`; table privilege output contains only `SELECT`; MCP command contains `@bytebase/dbhub@1.2.0`, points to the local TOML and masks the environment value.

### Task 3: Provar leitura permitida e escrita recusada

**Files:**
- No file changes.

**Interfaces:**
- Consumes: role and MCP config from Task 2.
- Produces: database-level read-only evidence and post-restart MCP evidence.

- [ ] **Step 1: Testar diretamente com a senha armazenada sem imprimi-la**

Obter `DBHUB_POSTGRES_PASSWORD` do bloco local do servidor sem imprimir o valor, executar as duas provas no mesmo processo e apagar a variável. Preferir um parser TOML da biblioteca padrão em vez de `rg`/`sed` sobre o segredo:

```bash
MCP_POSTGRES_ROLE_PASSWORD=$(uv run python - <<'PY'
import tomllib
from pathlib import Path

config = tomllib.loads(Path.home().joinpath('.codex/config.toml').read_text(encoding='utf-8'))
print(config['mcp_servers']['postgres_best_django_api_template']['env']['DBHUB_POSTGRES_PASSWORD'])
PY
)

docker compose exec -T -e PGPASSWORD="$MCP_POSTGRES_ROLE_PASSWORD" db \
  psql -U codex_readonly -d base -Atc "SELECT count(*) FROM django_migrations;"

if docker compose exec -T -e PGPASSWORD="$MCP_POSTGRES_ROLE_PASSWORD" db \
  psql -v ON_ERROR_STOP=1 -U codex_readonly -d base \
  -c "CREATE TABLE codex_mcp_write_probe (id integer);"; then
  unset MCP_POSTGRES_ROLE_PASSWORD
  exit 1
fi

unset MCP_POSTGRES_ROLE_PASSWORD
```

Expected: SELECT prints a positive integer; CREATE TABLE fails with `permission denied for schema public`; shell exits 0 because refusal is the expected branch; table is absent.

- [ ] **Step 2: Reiniciar o cliente Codex**

Close and reopen the Codex desktop client, or start a new Codex session rooted at this repository.

Expected: startup no longer reports that a PostgreSQL MCP is unavailable; the server name is `postgres_best_django_api_template`.

- [ ] **Step 3: Validar as tools na sessão nova**

Use `search_objects_local_postgres` to locate `django_migrations`, then use `execute_sql_local_postgres` with:

```sql
SELECT app, name FROM django_migrations ORDER BY applied DESC LIMIT 5;
```

Expected: both calls succeed and return schema/migration data. Do not use MCP for the write probe; the direct PostgreSQL denial from Step 1 is the authoritative security check.

## References

- Codex MCP configuration: <https://developers.openai.com/codex/mcp/>
- DBHub TOML configuration: <https://dbhub.ai/config/toml>
- DBHub read-only advisory fixed before the pinned release: <https://github.com/bytebase/dbhub/security/advisories/GHSA-mwwr-p57h-56pf>
