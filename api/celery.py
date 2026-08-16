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

# Header que carrega a organização de origem até o worker.
HEADER_ORGANIZACAO = "organizacao_id"

# Chave de contexto do django-rls que guarda a organização.
CHAVE_TENANT = "tenant_id"

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
    from internal_frameworks.context import ContextVariable

    token = _tokens.pop(task_id, None)
    if token is not None:
        reset_request_id(token)
    ContextVariable.clear_context()


@before_task_publish.connect
def propagar_organizacao(headers=None, **kwargs):
    """Carimba na mensagem a organização vigente em quem enfileirou.

    Sem isso a task acorda sem tenant e qualquer consulta a modelo isolado
    levanta `RLSContextRequiredError`.
    """
    from django_rls.context import get_active_rls_context

    if headers is None:
        return

    organizacao_id = get_active_rls_context().get(CHAVE_TENANT)
    if organizacao_id:
        headers[HEADER_ORGANIZACAO] = organizacao_id


@task_prerun.connect
def aplicar_organizacao(task=None, **kwargs):
    """Aplica no worker a organização que veio na mensagem.

    Diferente do caminho de request, aqui o `SET` é de **sessão**, não `LOCAL`:
    envolver o corpo da task numa transação transformaria toda task longa numa
    transação longa, segurando locks e atrapalhando o vacuum. Em troca, o worker
    precisa falar direto com o Postgres — atrás de um pooler em *transaction
    mode* um `SET` de sessão vaza entre clientes.

    `system=True` porque o worker reaproveita a conexão entre tasks e o
    `tenant_id` é imutável: sem isso, a segunda task de outra organização
    falharia ao tentar sobrescrever o contexto da primeira.
    """
    from django_rls.context import set_rls_context

    request = getattr(task, "request", None)
    organizacao_id = getattr(request, HEADER_ORGANIZACAO, None) if request else None

    if organizacao_id:
        set_rls_context(CHAVE_TENANT, organizacao_id, system=True, source="celery")


@task_postrun.connect
def limpar_organizacao(**kwargs):
    """Descarrega o tenant ao fim da task, para não vazar para a próxima."""
    from django_rls.context import clear_rls_context

    clear_rls_context({CHAVE_TENANT})


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Task de sanidade para validar que o worker está processando."""
    logger.info("Celery debug task executada. Request: %r", self.request)
