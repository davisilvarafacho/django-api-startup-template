"""Permissions reutilizáveis do template."""
from django.conf import settings

from rest_framework.permissions import BasePermission

from apps.api.core.errors import APIError, CoreErrorCode

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

    Responde 403 com código `core.internal_ip_required` quando o IP da conexão
    (`REMOTE_ADDR`) não pertence à lista configurada.
    """

    message = "Este endpoint só pode ser acessado a partir da rede interna."

    def has_permission(self, request, view):
        if ip_in_networks(peer_ip(request), settings.INTERNAL_IPS):
            return True

        raise APIError(CoreErrorCode.INTERNAL_IP_REQUIRED, status_code=403, message=self.message)
