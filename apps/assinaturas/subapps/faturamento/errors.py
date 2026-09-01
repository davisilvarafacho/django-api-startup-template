from django.db import models
from django.utils.translation import gettext_lazy as _


class ErrosFaturamento(models.TextChoices):
    GATEWAY_INDISPONIVEL = "billing.gateway_unavailable", _("O serviço de pagamento está temporariamente indisponível.")
    CHECKOUT_INDISPONIVEL = "billing.checkout_unavailable", _("Este plano não pode ser contratado com o gateway configurado.")
