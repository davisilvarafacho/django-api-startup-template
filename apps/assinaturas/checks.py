"""Django system checks do catálogo comercial."""

from django.core.checks import Error, Tags, register
from django.db import connection

from apps.assinaturas.models import StatusAssinatura


@register()
def recursos_planos_check(app_configs, **kwargs):
    from apps.assinaturas import features

    return [Error(f"Recurso '{chave}': {mensagem}", id="assinaturas.E001") for chave, mensagem in features.CATALOGO_RECURSOS.erros_declaracoes()]


@register()
def catalogo_planos_check(app_configs, **kwargs):
    from apps.assinaturas.catalogs import PLANOS_BOOTSTRAP, erros_definicoes_planos

    return [Error(mensagem, id="assinaturas.E002") for mensagem in erros_definicoes_planos(PLANOS_BOOTSTRAP)]


@register(Tags.database, deploy=True)
def organizacoes_ativas_sem_assinatura_check(app_configs, **kwargs):
    """Falha o deploy quando o middleware bloquearia uma organização ativa."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM organizacao
                WHERE organizacao.is_active = TRUE
                  AND organizacao.is_deleted = FALSE
                  AND NOT EXISTS (
                      SELECT 1
                      FROM assinatura_organizacao
                      WHERE assinatura_organizacao.organizacao_id = organizacao.id
                        AND assinatura_organizacao.status IN (%s, %s, %s)
                  )
            )
            """,
            [StatusAssinatura.PENDENTE, StatusAssinatura.EM_TRIAL, StatusAssinatura.ATIVA],
        )
        existe_lacuna = cursor.fetchone()[0]
    if not existe_lacuna:
        return []
    return [
        Error(
            "Existem organizações ativas sem assinatura corrente.",
            hint="Execute initialize_subscriptions em dry-run e depois com --apply antes do deploy.",
            id="assinaturas.E003",
        )
    ]
