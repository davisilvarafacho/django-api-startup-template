"""Instância central do Celery.

O worker é iniciado com `celery -A api worker` e o beat com
`celery -A api beat --scheduler django_celery_beat.schedulers:DatabaseScheduler`.
Toda a configuração vem do settings do Django, com o prefixo `CELERY_`.
"""
import logging
import os

from celery import Celery
from celery.signals import before_task_publish, task_postrun, task_prerun

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "api.settings")

app = Celery("api")

# Lê toda configuração com prefixo CELERY_ do settings do Django.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Autodescobre `tasks.py` em cada app instalada.
app.autodiscover_tasks()

logger = logging.getLogger(__name__)


# Nome do header da mensagem que carrega o correlation id até o worker.
HEADER_REQUEST_ID = "request_id"

# Token de reversão do contextvar, por id de task (um worker executa uma task
# por vez em cada thread/processo, mas prefork pode reciclar).
_tokens = {}


@before_task_publish.connect
def propagar_request_id(headers=None, **kwargs):
    """Carimba o correlation id da request na mensagem da task.

    Sem isso a trilha se perde no `delay()`: o log da request e o log do worker
    que a atendeu ficam sem nada em comum.
    """
    from apps.api.core.request_id import get_request_id

    if headers is None:
        return

    request_id = get_request_id()
    if request_id:
        headers[HEADER_REQUEST_ID] = request_id


@task_prerun.connect
def aplicar_request_id(task_id=None, task=None, **kwargs):
    """Publica no worker o correlation id que veio na mensagem."""
    from apps.api.core.request_id import gerar_request_id, set_request_id

    request = getattr(task, "request", None)
    request_id = getattr(request, HEADER_REQUEST_ID, None) if request else None

    # Task disparada pelo beat ou por um command não tem request de origem;
    # gera um id próprio para a execução continuar rastreável.
    _tokens[task_id] = set_request_id(request_id or gerar_request_id())


@task_postrun.connect
def limpar_request_id(task_id=None, **kwargs):
    """Libera o contexto ao fim da task."""
    from apps.api.core.request_id import reset_request_id

    token = _tokens.pop(task_id, None)
    if token is not None:
        reset_request_id(token)


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Task de sanidade para validar que o worker está processando."""
    logger.info("Celery debug task executada. Request: %r", self.request)
