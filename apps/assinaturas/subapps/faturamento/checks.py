from django.conf import settings
from django.core.checks import Error, Tags, register
from django.db import connection


@register()
def configuracao_faturamento_check(app_configs, **kwargs):
    del app_configs, kwargs
    stripe = settings.CHECKOUT_VARIANTS.get("stripe")
    configuracao = stripe[1] if isinstance(stripe, (tuple, list)) and len(stripe) == 2 and isinstance(stripe[1], dict) else {}
    if not all(configuracao.get(key) for key in ("api_key", "webhook_secret")):
        return [Error("Credenciais Stripe não configuradas.", id="faturamento.E001")]
    return []


@register(Tags.database, deploy=True)
def role_ingresso_check(app_configs, **kwargs):
    del app_configs, kwargs
    role = settings.BILLING_INGRESS_DATABASE_ROLE
    if role != "billing_ingress_runtime":
        return [Error("BILLING_INGRESS_DATABASE_ROLE deve ser billing_ingress_runtime.", id="faturamento.E002")]
    if connection.vendor != "postgresql":
        return []
    with connection.cursor() as cursor:
        cursor.execute(
            """WITH RECURSIVE alvo AS (
                   SELECT oid, rolcanlogin, rolsuper, rolbypassrls, rolcreaterole,
                          rolcreatedb, rolinherit, rolreplication
                   FROM pg_roles WHERE rolname=%s
               ), memberships(roleid, caminho, ciclo) AS (
                   SELECT membro.roleid, ARRAY[alvo.oid, membro.roleid], membro.roleid = alvo.oid
                   FROM alvo JOIN pg_auth_members membro ON membro.member=alvo.oid
                   UNION ALL
                   SELECT membro.roleid, memberships.caminho || membro.roleid,
                          membro.roleid = ANY(memberships.caminho)
                   FROM memberships JOIN pg_auth_members membro ON membro.member=memberships.roleid
                   WHERE NOT memberships.ciclo
               )
               SELECT rolcanlogin, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb,
                      rolinherit, rolreplication, pg_has_role(current_user, alvo.oid, 'MEMBER'),
                      EXISTS (SELECT 1 FROM memberships)
               FROM alvo""",
            [role],
        )
        atributos = cursor.fetchone()
    if atributos is None or atributos[:7] != (False, False, False, False, False, False, False) or atributos[8]:
        return [
            Error(
                "A role PostgreSQL billing_ingress_runtime está ausente ou possui atributos inseguros.",
                hint=(
                    "Pré-provisione-a como NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB "
                    "NOINHERIT NOREPLICATION e não conceda outras roles a ela."
                ),
                id="faturamento.E003",
            )
        ]
    if settings.BILLING_INGRESS_REQUIRE_MEMBERSHIP and not atributos[7]:
        return [
            Error(
                "O DATABASE_USER do worker não possui membership em billing_ingress_runtime.",
                hint="Conceda membership somente ao DATABASE_USER dedicado ao worker de ingresso.",
                id="faturamento.E004",
            )
        ]
    return []
