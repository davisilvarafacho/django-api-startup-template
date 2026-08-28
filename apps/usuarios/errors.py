from django.db import models
from django.utils.translation import gettext_lazy as _


class AccountErrorCode(models.TextChoices):
    EMAIL_NOT_VERIFIED = "account.email_not_verified", _("Verifique seu e-mail para continuar.")
    EMAIL_ALREADY_VERIFIED = "account.email_already_verified", _("Este e-mail já foi verificado.")
    EMAIL_VERIFICATION_INVALID = "account.email_verification_invalid", _("O link de verificação é inválido ou expirou.")
    EMAIL_ALREADY_IN_USE = "account.email_already_in_use", _("Este e-mail já está em uso.")
