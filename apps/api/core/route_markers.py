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

__all__ = ["MARCADOR_PUBLICA", "MARCADOR_SEM_TENANCY", "no_tenancy", "public", "tem_marcador", "view_do_path"]

MARCADOR_PUBLICA = "_rota_publica"
MARCADOR_SEM_TENANCY = "_rota_sem_tenancy"


def public(view):
    """Dispensa autenticação: a rota responde sem token."""
    setattr(view, MARCADOR_PUBLICA, True)
    return view


def no_tenancy(view):
    """Dispensa organização: exige token, mas não um `X-Organization`."""
    setattr(view, MARCADOR_SEM_TENANCY, True)
    return view


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
