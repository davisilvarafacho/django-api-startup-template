from django.db import models
from django.utils.translation import gettext_lazy as _


class ErrosFaturamento(models.TextChoices):
    GATEWAY_INDISPONIVEL = "billing.gateway_unavailable", _("O serviço de pagamento está temporariamente indisponível.")
    CHECKOUT_INDISPONIVEL = "billing.checkout_unavailable", _("Este plano não pode ser contratado com o gateway configurado.")
    CHECKOUT_CONFLITO = "billing.checkout_conflict", _("O checkout não corresponde mais ao estado atual da assinatura.")
    CHECKOUT_INCERTO = "billing.checkout_uncertain", _("O pagamento precisa ser conciliado antes de uma nova tentativa.")
    CHECKOUT_PENDENTE = "billing.checkout_pending", _("Já existe um checkout pendente para esta operação.")
    WEBHOOK_SIGNATURE_INVALID = "billing.webhook_signature_invalid", _("Assinatura do webhook inválida.")
    WEBHOOK_COLLISION = "billing.webhook_collision", _("O identificador do evento já foi recebido com conteúdo diferente.")
    WEBHOOK_PROTOCOL_INVALID = "billing.webhook_protocol_invalid", _("O evento do gateway não segue o protocolo esperado.")
    WEBHOOK_VARIANT_INVALID = "billing.webhook_variant_invalid", _("A variante de faturamento informada não está disponível.")
