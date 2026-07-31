"""Substitui os campos de auditoria legados pelos campos comuns atuais."""

from datetime import datetime

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


AUDITED_MODELS = ("convite", "organizacao", "time", "vinculo")


def copy_legacy_timestamps(apps, schema_editor):
    current_timezone = timezone.get_current_timezone()

    for model_name in AUDITED_MODELS:
        model = apps.get_model("organizacoes", model_name)
        for instance in model._base_manager.all().iterator():
            created_at = timezone.make_aware(datetime.combine(instance.data_criacao, instance.hora_criacao), current_timezone)
            last_modified_at = timezone.make_aware(datetime.combine(instance.data_ultima_alteracao, instance.hora_ultima_alteracao), current_timezone)
            model._base_manager.filter(pk=instance.pk).update(created_at=created_at, last_modified_at=last_modified_at)


class Migration(migrations.Migration):
    dependencies = [
        ("organizacoes", "0002_soft_delete"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="convite",
            options={
                "ordering": ["-id"],
                "permissions": [("can_accept_convite", "Pode aceitar convite")],
                "verbose_name": "Convite",
                "verbose_name_plural": "Convites",
            },
        ),
    ]

    for model_name in AUDITED_MODELS:
        operations.extend(
            [
                migrations.AddField(model_name=model_name, name="created_at", field=models.DateTimeField(null=True, verbose_name="criado em")),
                migrations.AddField(model_name=model_name, name="created_by", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to=settings.AUTH_USER_MODEL, verbose_name="criado por")),
                migrations.AddField(model_name=model_name, name="last_modified_at", field=models.DateTimeField(null=True, verbose_name="última alteração em")),
            ]
        )

    operations.append(migrations.RunPython(copy_legacy_timestamps, migrations.RunPython.noop))

    for model_name in AUDITED_MODELS:
        operations.extend(
            [
                migrations.AlterField(model_name=model_name, name="created_at", field=models.DateTimeField(auto_now_add=True, verbose_name="criado em")),
                migrations.AlterField(model_name=model_name, name="last_modified_at", field=models.DateTimeField(auto_now=True, verbose_name="última alteração em")),
                migrations.RemoveField(model_name=model_name, name="data_criacao"),
                migrations.RemoveField(model_name=model_name, name="data_ultima_alteracao"),
                migrations.RemoveField(model_name=model_name, name="hora_criacao"),
                migrations.RemoveField(model_name=model_name, name="hora_ultima_alteracao"),
            ]
        )

    operations.append(migrations.RemoveField(model_name="time", name="owner"))
