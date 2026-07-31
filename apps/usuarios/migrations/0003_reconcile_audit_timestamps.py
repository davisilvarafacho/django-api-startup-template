"""Substitui os timestamps legados pelos campos de auditoria atuais."""

from datetime import datetime

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


def copy_legacy_timestamps(apps, schema_editor):
    usuario = apps.get_model("usuarios", "Usuario")
    current_timezone = timezone.get_current_timezone()

    for instance in usuario._base_manager.all().iterator():
        created_at = timezone.make_aware(datetime.combine(instance.data_criacao, instance.hora_criacao), current_timezone)
        last_modified_at = timezone.make_aware(datetime.combine(instance.data_ultima_alteracao, instance.hora_ultima_alteracao), current_timezone)
        usuario._base_manager.filter(pk=instance.pk).update(created_at=created_at, last_modified_at=last_modified_at)


class Migration(migrations.Migration):
    dependencies = [("usuarios", "0002_soft_delete")]

    operations = [
        migrations.AddField(model_name="usuario", name="created_at", field=models.DateTimeField(null=True, verbose_name="criado em")),
        migrations.AddField(model_name="usuario", name="created_by", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to=settings.AUTH_USER_MODEL, verbose_name="criado por")),
        migrations.AddField(model_name="usuario", name="last_modified_at", field=models.DateTimeField(null=True, verbose_name="última alteração em")),
        migrations.RunPython(copy_legacy_timestamps, migrations.RunPython.noop),
        migrations.AlterField(model_name="usuario", name="created_at", field=models.DateTimeField(auto_now_add=True, verbose_name="criado em")),
        migrations.AlterField(model_name="usuario", name="last_modified_at", field=models.DateTimeField(auto_now=True, verbose_name="última alteração em")),
        migrations.RemoveField(model_name="usuario", name="data_criacao"),
        migrations.RemoveField(model_name="usuario", name="data_ultima_alteracao"),
        migrations.RemoveField(model_name="usuario", name="hora_criacao"),
        migrations.RemoveField(model_name="usuario", name="hora_ultima_alteracao"),
    ]
