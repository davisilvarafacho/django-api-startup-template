"""Testes da propagação da organização até o worker.

Exercitam os handlers diretamente, sem broker: o que importa é o contrato entre
quem enfileira (carimba o header) e quem executa (aplica e depois limpa).
"""

from types import SimpleNamespace

import pytest
from django_rls.context import clear_rls_context, get_active_rls_context, set_rls_context

from api.celery import (
    CHAVE_TENANT,
    HEADER_ORGANIZACAO,
    aplicar_organizacao,
    limpar_organizacao,
    propagar_organizacao,
)

# Os handlers aplicam o contexto via `SELECT set_config(...)`, então tocam a conexão.
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _contexto_limpo():
    """Garante que um teste não herde o tenant deixado por outro."""
    clear_rls_context({CHAVE_TENANT})
    yield
    clear_rls_context({CHAVE_TENANT})


def test_propaga_a_organizacao_vigente_para_o_header():
    set_rls_context(CHAVE_TENANT, 7, system=True, source="teste")
    headers = {}

    propagar_organizacao(headers=headers)

    assert headers[HEADER_ORGANIZACAO] == "7"


def test_nao_carimba_header_quando_nao_ha_organizacao():
    headers = {}

    propagar_organizacao(headers=headers)

    assert HEADER_ORGANIZACAO not in headers


def test_publish_sem_headers_nao_quebra():
    assert propagar_organizacao(headers=None) is None


def test_worker_aplica_a_organizacao_recebida():
    task = SimpleNamespace(request=SimpleNamespace(**{HEADER_ORGANIZACAO: "42"}))

    aplicar_organizacao(task=task)

    assert get_active_rls_context().get(CHAVE_TENANT) == "42"


def test_worker_sem_header_fica_sem_contexto():
    """Task do beat ou de um command não tem organização de origem.

    Fica sem contexto de propósito: qualquer consulta a modelo isolado vai
    levantar, em vez de rodar silenciosamente sem escopo.
    """
    task = SimpleNamespace(request=SimpleNamespace())

    aplicar_organizacao(task=task)

    assert CHAVE_TENANT not in get_active_rls_context()


def test_postrun_limpa_para_nao_vazar_para_a_proxima_task():
    set_rls_context(CHAVE_TENANT, 7, system=True, source="teste")

    limpar_organizacao()

    assert CHAVE_TENANT not in get_active_rls_context()


def test_worker_troca_de_organizacao_entre_tasks():
    """O worker reaproveita a conexão; a segunda task é de outra organização."""
    aplicar_organizacao(task=SimpleNamespace(request=SimpleNamespace(**{HEADER_ORGANIZACAO: "1"})))
    limpar_organizacao()
    aplicar_organizacao(task=SimpleNamespace(request=SimpleNamespace(**{HEADER_ORGANIZACAO: "2"})))

    assert get_active_rls_context().get(CHAVE_TENANT) == "2"
