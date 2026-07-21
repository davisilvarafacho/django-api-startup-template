"""Tasks assíncronas de uso geral."""
from celery import shared_task


@shared_task
def ping():
    """Task trivial para validar o pipeline do Celery ponta a ponta."""
    return "pong"
