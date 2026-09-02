from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


SQL = r"""
CREATE FUNCTION public.faturamento_destino_reconcile(bigint)
RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE destino bigint;
BEGIN
 IF NULLIF(current_setting('role',true),'') IS DISTINCT FROM 'billing_ingress_runtime'
 OR NULLIF(current_setting('rls.tenant_id',true),'')::integer IS DISTINCT FROM 0
 OR NULLIF(current_setting('rls.billing_ingress',true),'') IS DISTINCT FROM '1' THEN
  RAISE EXCEPTION 'papel e contexto operacionais obrigatorios' USING ERRCODE='42501'; END IF;
 SELECT organizacao_id INTO destino FROM public.evento_cobranca
 WHERE id=$1 AND organizacao_id IS NOT NULL AND aguarda_reconciliacao;
 RETURN destino;
END;
$$;
REVOKE ALL ON FUNCTION public.faturamento_destino_reconcile(bigint) FROM PUBLIC;
ALTER FUNCTION public.faturamento_destino_reconcile(bigint) OWNER TO __BILLING_OWNER__;
GRANT EXECUTE ON FUNCTION public.faturamento_destino_reconcile(bigint) TO billing_ingress_runtime;
"""

REVERSE_SQL = r"""
REVOKE ALL ON FUNCTION public.faturamento_destino_reconcile(bigint) FROM billing_ingress_runtime;
DROP FUNCTION public.faturamento_destino_reconcile(bigint);
"""


def instalar(apps, schema_editor):
    del apps
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(SQL.replace("__BILLING_OWNER__", settings.BILLING_DATABASE_OWNER_ROLE))


def remover(apps, schema_editor):
    del apps
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(REVERSE_SQL)


class Migration(migrations.Migration):
    dependencies = [("faturamento", "0008_fronteira_recovery_e_rls_auditoria")]
    operations = [
        migrations.AddField(
            model_name="eventocobranca",
            name="aguarda_reconciliacao",
            field=models.BooleanField(db_default=False, default=False),
        ),
        migrations.AddField(
            model_name="reaberturaeventocobranca",
            name="automatica",
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name="reaberturaeventocobranca",
            name="ator",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="reaberturas_eventos_cobranca",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(instalar, remover),
    ]
