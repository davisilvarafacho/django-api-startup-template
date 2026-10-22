#!/bin/sh
set -eu

if [ "$DATABASE_BILLING_WEB_USER" = "$POSTGRES_USER" ] || \
   [ "$DATABASE_BILLING_INGRESS_USER" = "$POSTGRES_USER" ] || \
   [ "$DATABASE_BILLING_INGRESS_WORKER_USER" = "$POSTGRES_USER" ] || \
   [ "$DATABASE_BILLING_WEB_USER" = "$DATABASE_BILLING_INGRESS_USER" ] || \
   [ "$DATABASE_BILLING_WEB_USER" = "$DATABASE_BILLING_INGRESS_WORKER_USER" ] || \
   [ "$DATABASE_BILLING_INGRESS_USER" = "$DATABASE_BILLING_INGRESS_WORKER_USER" ]; then
    echo "Os logins PostgreSQL admin, web, HTTP ingress e worker ingress devem ser distintos" >&2
    exit 1
fi

psql -v ON_ERROR_STOP=1 \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" <<'SQL'
\getenv web_user DATABASE_BILLING_WEB_USER
\getenv web_password DATABASE_BILLING_WEB_PASSWORD
\getenv ingress_user DATABASE_BILLING_INGRESS_USER
\getenv ingress_password DATABASE_BILLING_INGRESS_PASSWORD
\getenv ingress_worker_user DATABASE_BILLING_INGRESS_WORKER_USER
\getenv ingress_worker_password DATABASE_BILLING_INGRESS_WORKER_PASSWORD
\getenv migration_user POSTGRES_USER

SELECT 'CREATE ROLE billing_functions_owner NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB NOINHERIT NOREPLICATION'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'billing_functions_owner') \gexec

SELECT 'CREATE ROLE billing_ingress_runtime NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB NOINHERIT NOREPLICATION'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'billing_ingress_runtime') \gexec

SELECT format(
    'CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB NOINHERIT NOREPLICATION',
    :'web_user', :'web_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'web_user') \gexec

SELECT format(
    'CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB NOINHERIT NOREPLICATION',
    :'ingress_user', :'ingress_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'ingress_user') \gexec

SELECT format(
    'CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB NOINHERIT NOREPLICATION',
    :'ingress_worker_user', :'ingress_worker_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'ingress_worker_user') \gexec

SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), :'web_user') \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), :'ingress_user') \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), :'ingress_worker_user') \gexec
SELECT format('GRANT USAGE ON SCHEMA public TO %I', :'web_user') \gexec
SELECT format('GRANT USAGE ON SCHEMA public TO %I', :'ingress_user') \gexec
SELECT format('GRANT USAGE ON SCHEMA public TO %I', :'ingress_worker_user') \gexec
SELECT format('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO %I', :'web_user') \gexec
SELECT format('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO %I', :'ingress_worker_user') \gexec
SELECT format('GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO %I', :'web_user') \gexec
SELECT format('GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO %I', :'ingress_worker_user') \gexec
SELECT format(
    'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I',
    :'migration_user', :'web_user'
) \gexec
SELECT format(
    'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I',
    :'migration_user', :'ingress_worker_user'
) \gexec
SELECT format(
    'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO %I',
    :'migration_user', :'web_user'
) \gexec
SELECT format(
    'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO %I',
    :'migration_user', :'ingress_worker_user'
) \gexec
SELECT format('GRANT billing_ingress_runtime TO %I WITH SET TRUE', :'ingress_user') \gexec
SELECT format('GRANT billing_ingress_runtime TO %I WITH SET TRUE', :'ingress_worker_user') \gexec
SELECT format('GRANT billing_functions_owner TO %I WITH SET TRUE', :'migration_user') \gexec

SELECT 'GRANT SELECT (id, organizacao_id, assinatura_id, variante, identificador_externo, is_active, is_deleted) '
       'ON public.assinatura_gateway TO billing_ingress_runtime'
WHERE to_regclass('public.assinatura_gateway') IS NOT NULL \gexec
SELECT 'GRANT SELECT (id, organizacao_id, assinatura_id, variante, finalidade, identificador_externo, is_active, is_deleted) '
       'ON public.checkout_cobranca TO billing_ingress_runtime'
WHERE to_regclass('public.checkout_cobranca') IS NOT NULL \gexec
SQL
