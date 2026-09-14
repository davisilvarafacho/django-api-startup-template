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


def _owner_financeiro() -> str:
    owner = settings.BILLING_DATABASE_OWNER_ROLE
    if owner != "billing_functions_owner":
        raise RuntimeError("Owner financeiro incompatível com o contrato operacional.")
    return owner


def instalar(apps, schema_editor):
    del apps
    if schema_editor.connection.vendor == "postgresql":
        owner = _owner_financeiro()
        owner_quoted = schema_editor.connection.ops.quote_name(owner)
        with schema_editor.connection.cursor() as cursor:
            cursor.execute("SELECT has_schema_privilege(%s,'public','CREATE')", [owner])
            owner_tinha_create = cursor.fetchone()[0]
        if not owner_tinha_create:
            schema_editor.execute(f"GRANT CREATE ON SCHEMA public TO {owner_quoted}")
        schema_editor.execute(f"SET LOCAL ROLE {owner_quoted}")
        schema_editor.execute(SQL.replace("__BILLING_OWNER__", owner))
        schema_editor.execute("RESET ROLE")
        if not owner_tinha_create:
            schema_editor.execute(f"REVOKE CREATE ON SCHEMA public FROM {owner_quoted}")


def remover(apps, schema_editor):
    del apps
    if schema_editor.connection.vendor == "postgresql":
        owner = schema_editor.connection.ops.quote_name(_owner_financeiro())
        schema_editor.execute(f"SET LOCAL ROLE {owner}")
        schema_editor.execute(REVERSE_SQL)
        schema_editor.execute("RESET ROLE")


def preparar_reverse(apps, schema_editor):
    """Remove auditorias automáticas, que não possuem ator no schema anterior."""
    alias = schema_editor.connection.alias
    if schema_editor.connection.vendor == "postgresql":
        with schema_editor.connection.cursor() as cursor:
            cursor.execute("SELECT current_user")
            papel_ddl = schema_editor.connection.ops.quote_name(cursor.fetchone()[0])
        schema_editor.execute(
            f"""
            DROP POLICY IF EXISTS faturamento_reverse_0009_automaticas_select ON public.reabertura_evento_cobranca;
            DROP POLICY IF EXISTS faturamento_reverse_0009_automaticas_delete ON public.reabertura_evento_cobranca;
            CREATE POLICY faturamento_reverse_0009_automaticas_select ON public.reabertura_evento_cobranca
            FOR SELECT TO {papel_ddl} USING (ator_id IS NULL);
            CREATE POLICY faturamento_reverse_0009_automaticas_delete ON public.reabertura_evento_cobranca
            FOR DELETE TO {papel_ddl} USING (ator_id IS NULL);
            DELETE FROM public.reabertura_evento_cobranca WHERE ator_id IS NULL;
            DROP POLICY faturamento_reverse_0009_automaticas_delete ON public.reabertura_evento_cobranca;
            DROP POLICY faturamento_reverse_0009_automaticas_select ON public.reabertura_evento_cobranca;
            SET CONSTRAINTS ALL IMMEDIATE;
            """
        )
        return
    apps.get_model("faturamento", "ReaberturaEventoCobranca").objects.using(alias).filter(ator__isnull=True).delete()


def noop(apps, schema_editor):
    del apps, schema_editor


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
        # No caminho reverso esta operação roda antes do AlterField acima,
        # evitando restaurar NOT NULL sobre auditorias automáticas sem ator.
        migrations.RunPython(noop, preparar_reverse),
        migrations.RunPython(instalar, remover),
    ]
