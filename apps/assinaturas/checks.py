"""Django system checks do catálogo comercial."""

from django.core.checks import Error, register


@register()
def recursos_planos_check(app_configs, **kwargs):
    from apps.assinaturas import features

    return [Error(f"Recurso '{chave}': {mensagem}", id="assinaturas.E001") for chave, mensagem in features.CATALOGO_RECURSOS.erros_declaracoes()]


@register()
def catalogo_planos_check(app_configs, **kwargs):
    from apps.assinaturas.catalogs import PLANOS_BOOTSTRAP, erros_definicoes_planos

    return [Error(mensagem, id="assinaturas.E002") for mensagem in erros_definicoes_planos(PLANOS_BOOTSTRAP)]
