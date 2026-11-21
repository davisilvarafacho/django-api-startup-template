"""Escopo de organização para código de cobrança fora do ciclo de request."""

from contextlib import nullcontext

from django_rls.context import get_active_rls_context

from apps.organizacoes.context import CHAVE_TENANT, organizacao_atual


def organization_scope(organization_id: int):
    """Garante o contexto RLS da organização sem abrir um segundo por cima do atual.

    Se a organização informada já é a do contexto ativo, não faz nada. Caso contrário,
    abre `organizacao_atual`, que é quem decide sobre contexto já existente.

    Args:
        organization_id (int): Organização sob a qual o bloco roda.

    Returns:
        ContextManager: Um `nullcontext` ou o contexto de `organizacao_atual`.
    """
    if str(get_active_rls_context().get(CHAVE_TENANT)) == str(organization_id):
        return nullcontext()
    return organizacao_atual(organization_id)
