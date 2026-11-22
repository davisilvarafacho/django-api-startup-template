"""Códigos de erro do app de cobrança, descobertos pelo registry de erros da API."""

from django.db import models
from django.utils.translation import gettext_lazy as _


class BillingErrorCode(models.TextChoices):
    MEMBER_LIMIT_REACHED = "billing.member_limit_reached", _("A organização atingiu o limite de membros do plano.")
    DOWNGRADE_BLOCKED = "billing.downgrade_blocked", _("O downgrade foi bloqueado: a organização excede o limite do plano de destino.")
    ALREADY_SUBSCRIBED = "billing.already_subscribed", _("A organização já possui uma assinatura ativa.")
    NOT_SUBSCRIBED = "billing.not_subscribed", _("A organização não possui assinatura ativa.")
    SAME_PLAN = "billing.same_plan", _("A organização já está neste plano.")
    PLAN_UNAVAILABLE = "billing.plan_unavailable", _("Este plano não está disponível para assinatura.")
    IDEMPOTENCY_KEY_REQUIRED = "billing.idempotency_key_required", _("Informe o cabeçalho Idempotency-Key.")
    GATEWAY_UNCERTAIN = "billing.gateway_uncertain", _("O gateway de pagamento não confirmou a operação. Tente novamente em instantes.")
    GATEWAY_ERROR = "billing.gateway_error", _("O gateway de pagamento recusou a operação.")
    WEBHOOK_INVALID = "billing.webhook_invalid", _("A assinatura do webhook não confere.")
