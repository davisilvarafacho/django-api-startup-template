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
            """SELECT rolcanlogin, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolinherit,
                      pg_has_role(current_user, oid, 'MEMBER')
               FROM pg_roles WHERE rolname=%s""",
            [role],
        )
        atributos = cursor.fetchone()
    if atributos is None or atributos[:6] != (False, False, False, False, False, False):
        return [
            Error(
                "A role PostgreSQL billing_ingress_runtime está ausente ou possui atributos inseguros.",
                hint="Pré-provisione-a como NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB NOINHERIT.",
                id="faturamento.E003",
            )
        ]
    if settings.BILLING_INGRESS_REQUIRE_MEMBERSHIP and not atributos[6]:
        return [
            Error(
                "O DATABASE_USER do worker não possui membership em billing_ingress_runtime.",
                hint="Conceda membership somente ao DATABASE_USER dedicado ao worker de ingresso.",
                id="faturamento.E004",
            )
        ]
    return []
