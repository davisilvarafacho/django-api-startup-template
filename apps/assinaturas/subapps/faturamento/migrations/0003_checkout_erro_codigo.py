from django.db import migrations, models

TRIGGER_IDENTIDADE_SQL = """
DROP TRIGGER checkout_cobranca_coerencia ON public.checkout_cobranca;
CREATE TRIGGER checkout_cobranca_coerencia
BEFORE INSERT OR UPDATE OF organizacao_id, assinatura_id, alteracao_id, proposta_id, finalidade
ON public.checkout_cobranca
FOR EACH ROW EXECUTE FUNCTION public.faturamento_validar_coerencia_organizacao();
"""

TRIGGER_TODOS_UPDATES_SQL = """
DROP TRIGGER checkout_cobranca_coerencia ON public.checkout_cobranca;
CREATE TRIGGER checkout_cobranca_coerencia BEFORE INSERT OR UPDATE ON public.checkout_cobranca
FOR EACH ROW EXECUTE FUNCTION public.faturamento_validar_coerencia_organizacao();
"""


def restringir_trigger(apps, schema_editor):
    del apps
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(TRIGGER_IDENTIDADE_SQL)


def restaurar_trigger(apps, schema_editor):
    del apps
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(TRIGGER_TODOS_UPDATES_SQL)


class Migration(migrations.Migration):
    dependencies = [("faturamento", "0002_checkout_operacao_snapshot")]

    operations = [
        migrations.AddField(
            model_name="checkoutcobranca",
            name="erro_codigo",
            field=models.CharField(blank=True, default="", max_length=64),
            preserve_default=False,
        ),
        migrations.RunPython(restringir_trigger, reverse_code=restaurar_trigger),
    ]
