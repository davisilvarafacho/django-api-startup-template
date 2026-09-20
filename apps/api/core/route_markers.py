"""Marcadores de rota aplicados diretamente na view.

Complementam os registries de prefixo (`routes_registry` e
`tenant_free_registry`). Os dois mecanismos coexistem porque resolvem casos
diferentes:

- **Decorator** — para as views deste projeto. Fica junto da view, sobrevive a
  mudanças de URL e é visível para quem lê o código.
- **Prefixo** — para o que não dá para decorar: `/admin/`, arquivos estáticos,
  views de bibliotecas de terceiros e subárvores inteiras.

A checagem é sempre `decorator OU prefixo`.

Atenção: o marcador é um atributo de classe, então **subclasses herdam**. Não
decore uma view base a menos que queira liberar todas as filhas.
"""

from typing import Any

from django.core.checks import Error, Tags, register
from django.urls import Resolver404, URLResolver, get_resolver, resolve

from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

__all__ = [
    "MARCADOR_PUBLICA",
    "MARCADOR_IO_EXTERNO_SEM_TRANSACAO",
    "MARCADOR_REGULARIZACAO_ASSINATURA",
    "MARCADOR_SEM_TENANCY",
    "MARCADORES_ROTA",
    "no_tenancy",
    "io_externo_sem_transacao",
    "public",
    "regularizacao_assinatura",
    "rota_tem_marcador",
    "tem_marcador",
    "view_do_path",
]

MARCADOR_PUBLICA = "_rota_publica"
MARCADOR_SEM_TENANCY = "_rota_sem_tenancy"
MARCADOR_REGULARIZACAO_ASSINATURA = "_rota_regularizacao_assinatura"
MARCADOR_IO_EXTERNO_SEM_TRANSACAO = "_rota_io_externo_sem_transacao"
MARCADORES_ROTA = frozenset({MARCADOR_PUBLICA, MARCADOR_SEM_TENANCY, MARCADOR_REGULARIZACAO_ASSINATURA, MARCADOR_IO_EXTERNO_SEM_TRANSACAO})


def _marcar(view, marcador):
    if marcador not in MARCADORES_ROTA:
        raise ValueError(f"Marcador de rota não registrado: {marcador}")
    setattr(view, marcador, True)
    return view


def public(view):
    """Dispensa autenticação: a rota responde sem token."""
    target = getattr(view, "cls", None) or view
    target.permission_classes = [AllowAny]
    _marcar(target, MARCADOR_PUBLICA)
    return _marcar(view, MARCADOR_PUBLICA)


def no_tenancy(view):
    """Dispensa organização: exige token, mas não um `X-Organization`."""
    return _marcar(view, MARCADOR_SEM_TENANCY)


def regularizacao_assinatura(view):
    """Declara uma view/action apta a regularizar acesso comercial restrito."""
    return _marcar(view, MARCADOR_REGULARIZACAO_ASSINATURA)


def io_externo_sem_transacao(view):
    """Encerra a transação tenant antes de uma view que fará I/O externo."""
    return _marcar(view, MARCADOR_IO_EXTERNO_SEM_TRANSACAO)


def view_do_path(path):
    """Resolve um caminho para a view que vai atendê-lo.

    Usado pelo middleware de autenticação, que decide antes de o Django
    resolver a URL. A resolução é barata (o resolver do Django é memoizado).

    Args:
        path: O `request.path_info`.

    Returns:
        A classe da view (DRF/CBV) ou a própria função; `None` se não resolver.
    """
    try:
        match = resolve(path)
    except Resolver404:
        return None

    # DRF expõe `.cls` em as_view(); CBVs do Django expõem `.view_class`.
    return getattr(match.func, "cls", None) or getattr(match.func, "view_class", None) or match.func


def tem_marcador(view, marcador):
    """Informa se a view (classe, instância ou função) carrega o marcador."""
    return bool(getattr(view, marcador, False))


def rota_tem_marcador(path, method, marcador):
    """Resolve marcador na view, no método HTTP ou na action DRF da rota."""
    try:
        match = resolve(path)
    except Resolver404:
        return False

    callback = match.func
    view = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
    if tem_marcador(callback, marcador) or tem_marcador(view, marcador):
        return True

    actions = getattr(callback, "actions", {})
    handler_name = actions.get(method.lower()) if actions else method.lower()
    return bool(handler_name) and tem_marcador(getattr(view, handler_name or "", None), marcador)


@register(Tags.security)
def check_public_routes(app_configs, **kwargs):
    """Keep public middleware declarations consistent with DRF permissions."""
    from django.conf import settings

    from apps.api.autenticacao.middleware import DEBUG_PREFIXES
    from apps.api.base.permissions import ModelPermissionMixin
    from apps.api.core.routes_registry import routes_registry

    errors: list[Error] = []

    def visit(patterns, prefix=""):
        for pattern in patterns:
            route = prefix + str(pattern.pattern).removeprefix("^").removesuffix("$")
            if isinstance(pattern, URLResolver):
                visit(pattern.url_patterns, route)
                continue
            callback = pattern.callback
            view: Any = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
            if not isinstance(view, type) or not issubclass(view, APIView):
                continue
            initkwargs = getattr(callback, "initkwargs", {})
            permissions = initkwargs.get("permission_classes", getattr(view, "permission_classes", ())) or ()
            declared = tem_marcador(callback, MARCADOR_PUBLICA) or tem_marcador(view, MARCADOR_PUBLICA)
            route_path = "/" + route
            declared = declared or routes_registry.matches(route_path)
            declared = declared or (settings.DEBUG and route_path.startswith(DEBUG_PREFIXES))
            actions = getattr(callback, "actions", {})
            handlers = [getattr(view, name, None) for name in actions.values()]
            if issubclass(view, ModelPermissionMixin) and (declared or any(tem_marcador(h, MARCADOR_PUBLICA) for h in handlers)):
                errors.append(Error("Rota/action pública não pode usar ModelPermissionMixin.", obj=view, id="core.E012"))
            if AllowAny in permissions and not declared:
                errors.append(Error("AllowAny exige @public ou prefixo PUBLIC_ROUTES.", obj=view, id="core.E010"))
            if declared and any(permission is not AllowAny for permission in permissions):
                errors.append(Error("Rota pública preserva permissions restritivas.", obj=view, id="core.E011"))

    visit(get_resolver().url_patterns)
    return errors
