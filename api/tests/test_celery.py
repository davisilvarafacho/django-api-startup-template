"""Testes dos sinais de contexto do worker Celery."""

import api.celery as celery
from apps.api.core.context import token_atual
from apps.api.core.request_id import get_request_id, reset_request_id, set_request_id
from internal_frameworks.context import ContextVariable


def test_limpar_request_id_restaura_token_antes_de_limpar_contexto(monkeypatch):
    ContextVariable.clear_context()
    token_atual.set("token-da-task")
    token = set_request_id("request-da-task")
    celery._tokens["task-id"] = token

    def reset_com_contexto_ainda_disponivel(token):
        assert token_atual.is_set()
        reset_request_id(token)

    monkeypatch.setattr("apps.api.core.request_id.reset_request_id", reset_com_contexto_ainda_disponivel)

    celery.limpar_request_id(task_id="task-id")

    assert get_request_id() is None
    assert not token_atual.is_set()
