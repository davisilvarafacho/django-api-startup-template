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

from django.urls import Resolver404, resolve

__all__ = [
    "MARCADOR_PUBLICA",
    "MARCADOR_REGULARIZACAO_ASSINATURA",
    "MARCADOR_SEM_TENANCY",
    "MARCADORES_ROTA",
    "no_tenancy",
    "public",
    "regularizacao_assinatura",
    "rota_tem_marcador",
    "tem_marcador",
    "view_do_path",
]

MARCADOR_PUBLICA = "_rota_publica"
MARCADOR_SEM_TENANCY = "_rota_sem_tenancy"
MARCADOR_REGULARIZACAO_ASSINATURA = "_rota_regularizacao_assinatura"
MARCADORES_ROTA = frozenset({MARCADOR_PUBLICA, MARCADOR_SEM_TENANCY, MARCADOR_REGULARIZACAO_ASSINATURA})


def _marcar(view, marcador):
    if marcador not in MARCADORES_ROTA:
        raise ValueError(f"Marcador de rota não registrado: {marcador}")
    setattr(view, marcador, True)
    return view


def public(view):
    """Dispensa autenticação: a rota responde sem token."""
    return _marcar(view, MARCADOR_PUBLICA)


def no_tenancy(view):
    """Dispensa organização: exige token, mas não um `X-Organization`."""
    return _marcar(view, MARCADOR_SEM_TENANCY)


def regularizacao_assinatura(view):
    """Declara uma view/action apta a regularizar acesso comercial restrito."""
    return _marcar(view, MARCADOR_REGULARIZACAO_ASSINATURA)


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
    return tem_marcador(getattr(view, handler_name, None), marcador)
