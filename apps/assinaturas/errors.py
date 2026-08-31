from django.db import models
from django.utils.translation import gettext_lazy as _


class BillingErrorCode(models.TextChoices):
    SUBSCRIPTION_REQUIRED = (
        "billing.subscription_required",
        _("A assinatura desta organização ainda não foi inicializada."),
    )
    SUBSCRIPTION_CONFLICT = (
        "billing.subscription_conflict",
        _("A assinatura mudou. Atualize os dados e tente novamente."),
    )
    PROPOSAL_INVALID = (
        "billing.proposal_invalid",
        _("A proposta não está disponível para aceite."),
    )
    SEAT_LIMIT_REACHED = (
        "billing.seat_limit_reached",
        _("Não há seats disponíveis. Aumente a quantidade contratada ou libere um seat."),
    )
