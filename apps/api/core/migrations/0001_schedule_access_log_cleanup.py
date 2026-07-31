from django.db import migrations

TAREFA = "apps.api.core.tasks.limpar_logs_de_acesso_antigos"


def agendar(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    agenda, _ = CrontabSchedule.objects.get_or_create(
        minute="0",
        hour="3",
        day_of_week="*",
        day_of_month="*",
        month_of_year="*",
    )
    PeriodicTask.objects.update_or_create(
        task=TAREFA,
        defaults={
            "name": "Expurgar logs de acesso antigos",
            "crontab": agenda,
            "enabled": True,
        },
    )


def desagendar(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    tarefas = PeriodicTask.objects.filter(task=TAREFA)
    agendas = list(tarefas.values_list("crontab_id", flat=True))
    tarefas.delete()

    for agenda_id in agendas:
        if agenda_id and not PeriodicTask.objects.filter(crontab_id=agenda_id).exists():
            CrontabSchedule.objects.filter(pk=agenda_id).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [
        migrations.RunPython(agendar, desagendar),
    ]
