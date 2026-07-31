"""Tasks assíncronas de uso geral."""
from axes.handlers.proxy import AxesProxyHandler
from celery import shared_task


@shared_task
def ping():
    """Task trivial para validar o pipeline do Celery ponta a ponta."""
    return "pong"


@shared_task
def limpar_logs_de_acesso_antigos(dias: int = 90) -> int:
    """Remove registros de `AccessLog` mais velhos que o prazo informado.

    O `AccessAttempt` não entra: ele se autolimpa quando o cooloff expira. O
    `AccessLog` é a única tabela do django-axes que cresce sem limite, porque
    grava um registro por login bem-sucedido.

    Args:
        dias: Idade máxima, em dias, dos registros preservados.

    Returns:
        Quantidade de registros removidos.
    """
    return AxesProxyHandler.reset_logs(age_days=dias)
