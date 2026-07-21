from django.utils import timezone

import serpy

from apps.api.base.serializers import BaseModelSerpySerializer


class AuthTokenSerializer(BaseModelSerpySerializer):
    digest = serpy.StrField()
    token_key = serpy.StrField()
    created = serpy.Field()
    expiry = serpy.Field()
    # is_current = serpy.MethodField()
    days_until_expiry = serpy.MethodField()
    device_info = serpy.MethodField()

    def get_is_current(self, obj):
        """Verifica se é o token da requisição atual."""
        request = self.context.get("request")
        if not request or not hasattr(request, "auth"):
            return False
        return request.auth.digest == obj.digest

    def get_days_until_expiry(self, obj):
        """Calcula dias restantes até expirar."""
        if not obj.expiry:
            return None
        delta = obj.expiry - timezone.now()
        return max(delta.days, 0)

    def get_device_info(self, obj):
        """Você pode estender para guardar info do dispositivo."""
        # Por padrão, Knox não guarda isso
        # Veja implementação alternativa abaixo
        return
