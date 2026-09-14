"""Django system checks do catálogo comercial."""

from django.core.checks import Error, Tags, register

from apps.assinaturas.operational import FinalidadeOperacionalAssinatura, selecionar_organizacoes_operacionais


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
    existe_lacuna = bool(
        selecionar_organizacoes_operacionais(
            FinalidadeOperacionalAssinatura.ROLLOUT,
            limite=1,
        )
    )
    if not existe_lacuna:
        return []
    return [
        Error(
            "Existem organizações ativas sem assinatura corrente.",
            hint="Execute initialize_subscriptions em dry-run e depois com --apply antes do deploy.",
            id="assinaturas.E003",
        )
    ]
