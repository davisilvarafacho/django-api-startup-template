"""Contexto RLS usado para selecionar Workspaces dentro de uma Organização."""

from django_rls.context import set_rls_context

CHAVE_MEMBERSHIP = "membership_id"
CHAVE_CURRENT_WORKSPACE = "current_workspace_id"
CHAVE_WORKSPACE_MODE = "workspace_mode"
CHAVES_WORKSPACE = frozenset({CHAVE_MEMBERSHIP, CHAVE_CURRENT_WORKSPACE, CHAVE_WORKSPACE_MODE})

MODO_MEMBERSHIP = "membership"
MODO_API_KEY = "api_key"
MODO_CONTROL = "control"
MODO_SYSTEM = "system"


def _publicar_contexto(*, modo: str, membership_id: int | None = None, current_workspace_id: int | None = None) -> None:
    valores = {
        CHAVE_MEMBERSHIP: membership_id,
        CHAVE_CURRENT_WORKSPACE: current_workspace_id,
        CHAVE_WORKSPACE_MODE: modo,
    }
    for chave, valor in valores.items():
        set_rls_context(chave, "" if valor is None else valor, is_local=True, system=True)


def definir_contexto_workspace_membership(*, membership_id: int, current_workspace_id: int | None) -> None:
    """Publica o vínculo humano e o Workspace atual na transação corrente."""
    _publicar_contexto(modo=MODO_MEMBERSHIP, membership_id=membership_id, current_workspace_id=current_workspace_id)


def definir_contexto_workspace_api_key() -> None:
    """Publica o modo amplo de API key, sem vínculo ou Workspace atual."""
    _publicar_contexto(modo=MODO_API_KEY)


def definir_contexto_workspace_control() -> None:
    """Publica o modo de controle, visível somente para registros compartilhados."""
    _publicar_contexto(modo=MODO_CONTROL)


def definir_contexto_workspace_system() -> None:
    """Publica o modo operacional que atravessa Workspaces do tenant atual."""
    _publicar_contexto(modo=MODO_SYSTEM)
