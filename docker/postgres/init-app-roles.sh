#!/bin/sh
# Cria o login de runtime da aplicação (web, workers, beat, MCP).
#
# O login é deliberadamente NOSUPERUSER/NOBYPASSRLS: superusuário e dono das
# tabelas ignoram as policies do RLS, então a aplicação nunca conecta como o
# admin do PostgreSQL. Migrations rodam com a credencial admin (`make migrate`),
# e os default privileges abaixo estendem ao login web as tabelas que elas criam.
set -eu

if [ "$DATABASE_WEB_USER" = "$POSTGRES_USER" ]; then
    echo "O login web deve ser distinto do admin do PostgreSQL" >&2
    exit 1
fi

psql -v ON_ERROR_STOP=1 \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" <<'SQL'
\getenv web_user DATABASE_WEB_USER
\getenv web_password DATABASE_WEB_PASSWORD
\getenv migration_user POSTGRES_USER

SELECT format(
    'CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB NOINHERIT NOREPLICATION',
    :'web_user', :'web_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'web_user') \gexec

SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), :'web_user') \gexec
SELECT format('GRANT USAGE ON SCHEMA public TO %I', :'web_user') \gexec
SELECT format('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO %I', :'web_user') \gexec
SELECT format('GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO %I', :'web_user') \gexec
SELECT format(
    'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I',
    :'migration_user', :'web_user'
) \gexec
SELECT format(
    'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO %I',
    :'migration_user', :'web_user'
) \gexec
SQL
