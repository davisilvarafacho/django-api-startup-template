from django.db import migrations


SQL = """
GRANT SELECT (id, organizacao_id, assinatura_id, variante, identificador_externo, is_active, is_deleted)
ON public.assinatura_gateway TO billing_ingress_runtime;
GRANT SELECT (id, organizacao_id, assinatura_id, variante, finalidade, identificador_externo, is_active, is_deleted)
ON public.checkout_cobranca TO billing_ingress_runtime;
"""

REVERSE_SQL = """
REVOKE SELECT (id, organizacao_id, assinatura_id, variante, identificador_externo, is_active, is_deleted)
ON public.assinatura_gateway FROM billing_ingress_runtime;
REVOKE SELECT (id, organizacao_id, assinatura_id, variante, finalidade, identificador_externo, is_active, is_deleted)
ON public.checkout_cobranca FROM billing_ingress_runtime;
"""


def aplicar(apps, schema_editor):
    del apps
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(SQL)


def reverter(apps, schema_editor):
    del apps
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(REVERSE_SQL)


class Migration(migrations.Migration):
    dependencies = [("faturamento", "0011_fatos_invoice_e_lease_recovery")]
    operations = [migrations.RunPython(aplicar, reverter)]
