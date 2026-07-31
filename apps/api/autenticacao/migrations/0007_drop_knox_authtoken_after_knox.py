"""Remove a tabela Knox legada depois das migrations do pacote Knox."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("autenticacao", "0006_drop_legacy_knox_authtoken"),
        ("knox", "0009_extend_authtoken_field"),
    ]

    operations = [
        migrations.RunSQL(
            sql="DROP TABLE IF EXISTS knox_authtoken CASCADE;",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
