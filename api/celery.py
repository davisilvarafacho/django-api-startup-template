"""Instância central do Celery.

O worker é iniciado com `celery -A api worker` e o beat com
`celery -A api beat --scheduler django_celery_beat.schedulers:DatabaseScheduler`.
Toda a configuração vem do settings do Django, com o prefixo `CELERY_`.
"""
import logging
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "api.settings")

app = Celery("api")

# Lê toda configuração com prefixo CELERY_ do settings do Django.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Autodescobre `tasks.py` em cada app instalada.
app.autodiscover_tasks()

logger = logging.getLogger(__name__)


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Task de sanidade para validar que o worker está processando."""
    logger.info("Celery debug task executada. Request: %r", self.request)
