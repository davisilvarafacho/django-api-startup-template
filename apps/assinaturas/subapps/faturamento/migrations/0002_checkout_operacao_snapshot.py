from django.db import migrations, models


def preencher_identidade_legada(apps, schema_editor):
    Checkout = apps.get_model("faturamento", "CheckoutCobranca")
    usando = schema_editor.connection.alias
    for checkout_id in Checkout.objects.using(usando).values_list("pk", flat=True).iterator():
        Checkout.objects.using(usando).filter(pk=checkout_id).update(
            operacao_chave=f"legado:{checkout_id}", snapshot_hash="0" * 64
        )


class Migration(migrations.Migration):
    dependencies = [("faturamento", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="checkoutcobranca", name="operacao_chave", field=models.CharField(max_length=180, null=True)
        ),
        migrations.AddField(
            model_name="checkoutcobranca", name="snapshot_hash", field=models.CharField(max_length=64, null=True)
        ),
        migrations.RunPython(preencher_identidade_legada, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="checkoutcobranca", name="operacao_chave", field=models.CharField(max_length=180)
        ),
        migrations.AlterField(
            model_name="checkoutcobranca", name="snapshot_hash", field=models.CharField(max_length=64)
        ),
        migrations.AddConstraint(
            model_name="checkoutcobranca",
            constraint=models.UniqueConstraint(
                condition=~models.Q(operacao_chave="") & models.Q(status__in=(10, 20, 30)),
                fields=("organizacao", "operacao_chave"),
                name="checkout_operacao_pendente_unica",
            ),
        ),
    ]
