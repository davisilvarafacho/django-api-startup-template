"""Contexto de tenant para o RLS.

O contexto é sempre aplicado com `SET LOCAL` (`is_local=True`), ou seja, com
escopo da **transação** corrente. É isso que torna o isolamento seguro sob um
connection pool em *transaction mode* (PgBouncer), onde a conexão volta ao pool
a cada transação: um `SET` de sessão poderia vazar para outro cliente, um
`SET LOCAL` morre no commit.
"""
from contextlib import contextmanager

from django.db import transaction

from django_rls.context import set_rls_context


def definir_organizacao_atual(organizacao_id):
    """Aplica a organização na transação corrente.

    Deve ser chamado **dentro** de uma transação — fora dela o `SET LOCAL` não
    tem efeito duradouro.
    """
    set_rls_context("tenant_id", organizacao_id, is_local=True)


@contextmanager
def organizacao_atual(organizacao_id):
    """Executa um bloco sob a organização informada, em transação própria.

    Use em tasks do Celery, management commands e qualquer código que rode fora
    do ciclo de request — sem isso as queries não enxergam nenhuma linha.
    """
    with transaction.atomic():
        definir_organizacao_atual(organizacao_id)
        yield
