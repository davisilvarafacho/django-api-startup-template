from django.db import models
from django.utils.translation import gettext_lazy as _


class ErrosFaturamento(models.TextChoices):
    GATEWAY_INDISPONIVEL = "billing.gateway_unavailable", _("O serviço de pagamento está temporariamente indisponível.")
    CHECKOUT_INDISPONIVEL = "billing.checkout_unavailable", _("Este plano não pode ser contratado com o gateway configurado.")
    CHECKOUT_CONFLITO = "billing.checkout_conflict", _("O checkout não corresponde mais ao estado atual da assinatura.")
    CHECKOUT_INCERTO = "billing.checkout_uncertain", _("O pagamento precisa ser conciliado antes de uma nova tentativa.")
    CHECKOUT_PENDENTE = "billing.checkout_pending", _("Já existe um checkout pendente para esta operação.")
