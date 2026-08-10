"""Permissions reutilizáveis do template."""

from django.conf import settings

from rest_framework.permissions import BasePermission

from .ip_utils import ip_in_networks, peer_ip


class IsInternalIP(BasePermission):
    """Restringe o endpoint a IPs listados em `settings.INTERNAL_IPS`.

    Uso::

        class MinhaView(APIView):
            permission_classes = [IsInternalIP]

    Ou em uma action específica de ViewSet::

        class MeuViewSet(BaseModelViewSet):
            @action(detail=False, methods=["post"], permission_classes=[IsInternalIP])
            def manutencao(self, request):
                ...

    Quando o IP da conexão (`REMOTE_ADDR`) não pertence à lista configurada,
    devolve `False` e deixa o DRF responder a negativa padrão — 403 com
    `auth.permission_denied`, igual a qualquer outra permissão do projeto. Um
    código ou mensagem próprios contariam a quem chamou que existe uma rede
    interna com acesso privilegiado a esta rota, então de propósito não há nada
    aqui que distinga esta negativa das demais.
    """

    def has_permission(self, request, view):
        return ip_in_networks(peer_ip(request), settings.INTERNAL_IPS)
