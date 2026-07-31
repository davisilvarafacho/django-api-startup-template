from django.db import migrations

TAREFA = "apps.api.core.tasks.limpar_logs_de_acesso_antigos"
NOME_TAREFA = "Expurgar logs de acesso antigos"


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
    tarefas = PeriodicTask.objects.filter(task=TAREFA).order_by("pk")
    tarefa = PeriodicTask.objects.filter(name=NOME_TAREFA).first() or tarefas.first()
    if tarefa is None:
        PeriodicTask.objects.create(name=NOME_TAREFA, task=TAREFA, crontab=agenda, enabled=True)
        return

    tarefa.name = NOME_TAREFA
    tarefa.task = TAREFA
    tarefa.crontab = agenda
    tarefa.enabled = True
    tarefa.save(update_fields=["name", "task", "crontab", "enabled"])
    tarefas.exclude(pk=tarefa.pk).delete()


def desagendar(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(task=TAREFA).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [
        migrations.RunPython(agendar, desagendar),
    ]
