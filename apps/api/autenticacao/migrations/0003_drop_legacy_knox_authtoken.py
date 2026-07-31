"""Remove a tabela Knox legada substituída por ``autenticacao.AuthToken``."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("autenticacao", "0002_mfafactor_mfachallenge_mfarecoverycode_trusteddevice_and_more")]

    operations = [
        migrations.RunSQL(
            sql="DROP TABLE IF EXISTS knox_authtoken CASCADE;",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
