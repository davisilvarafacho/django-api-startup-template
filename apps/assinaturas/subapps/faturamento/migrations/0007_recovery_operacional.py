from django.conf import settings
from django.db import migrations


SQL = """
GRANT SELECT ON public.checkout_cobranca TO __BILLING_OWNER__;
CREATE POLICY checkout_recovery_owner_select ON public.checkout_cobranca
FOR SELECT TO __BILLING_OWNER__ USING (true);
"""

REVERSE_SQL = """
DROP POLICY IF EXISTS checkout_recovery_owner_select ON public.checkout_cobranca;
REVOKE SELECT ON public.checkout_cobranca FROM __BILLING_OWNER__;
"""


def instalar(apps, schema_editor):
    del apps
    if schema_editor.connection.vendor != "postgresql":
        return
    owner = settings.BILLING_DATABASE_OWNER_ROLE
    if owner != "billing_functions_owner":
        raise RuntimeError("A role operacional de billing não corresponde ao contrato instalado.")
    schema_editor.execute(SQL.replace("__BILLING_OWNER__", owner))


def remover(apps, schema_editor):
    del apps
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(REVERSE_SQL.replace("__BILLING_OWNER__", settings.BILLING_DATABASE_OWNER_ROLE))


class Migration(migrations.Migration):
    dependencies = [("faturamento", "0006_tentativas_automaticas_ciclo")]
    operations = [migrations.RunPython(instalar, remover)]
