"""Feature flags operacionais sobre o `django-waffle`.

Divisão de responsabilidade (ver `docs/adr/0003-feature-flags.md`):

- **waffle** — kill-switch e rollout interno. Estado no banco, editável pelo
  admin, avaliado localmente (sem chamada de rede no caminho da request).
- **PostHog** — rollout de produto por segmento/experimento.

Os três tipos do waffle:

- `Switch` — liga/desliga global. É o que se usa para desativar uma integração
  em incidente.
- `Flag` — condicional por usuário/grupo/porcentagem.
- `Sample` — porcentagem pura, sorteada a cada consulta.
"""
from rest_framework import permissions
from rest_framework.exceptions import NotFound

import waffle


def switch_ativo(nome):
    """Informa se um switch global está ligado."""
    return waffle.switch_is_active(nome)


def flag_ativa(request, nome):
    """Informa se uma flag está ativa para a request (usuário/percentual)."""
    return waffle.flag_is_active(request, nome)


class SwitchAtivo(permissions.BasePermission):
    """Bloqueia a view enquanto o switch estiver desligado.

    Uso::

        class PedidoViewSet(BaseModelViewSet):
            permission_classes = [SwitchAtivo.para("pedidos")]

    Responde 404, não 403: uma funcionalidade desligada por flag não deve
    revelar que existe.
    """

    switch = None

    @classmethod
    def para(cls, switch):
        """Cria a permission class amarrada ao switch informado."""
        return type(f"SwitchAtivo{switch.title().replace('_', '')}", (cls,), {"switch": switch})

    def has_permission(self, request, view):
        if self.switch is None:
            raise NotImplementedError("Use `SwitchAtivo.para('nome-do-switch')`.")

        if not switch_ativo(self.switch):
            raise NotFound()

        return True


class FlagAtiva(permissions.BasePermission):
    """Versão da `SwitchAtivo` para flags (avaliadas por usuário)."""

    flag = None

    @classmethod
    def para(cls, flag):
        """Cria a permission class amarrada à flag informada."""
        return type(f"FlagAtiva{flag.title().replace('_', '')}", (cls,), {"flag": flag})

    def has_permission(self, request, view):
        if self.flag is None:
            raise NotImplementedError("Use `FlagAtiva.para('nome-da-flag')`.")

        if not flag_ativa(request, self.flag):
            raise NotFound()

        return True
