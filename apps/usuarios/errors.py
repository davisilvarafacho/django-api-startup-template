from django.db import models
from django.utils.translation import gettext_lazy as _


class AccountErrorCode(models.TextChoices):
    EMAIL_NOT_VERIFIED = "account.email_not_verified", _("Verifique seu e-mail para continuar.")
    EMAIL_ALREADY_VERIFIED = "account.email_already_verified", _("Este e-mail já foi verificado.")
    EMAIL_VERIFICATION_INVALID = "account.email_verification_invalid", _("O link de verificação é inválido ou expirou.")
    EMAIL_ALREADY_IN_USE = "account.email_already_in_use", _("Este e-mail já está em uso.")
    EXTERNAL_IDENTITY_CONFLICT = "account.external_identity_conflict", _("Esta identidade externa já está vinculada a outra conta.")
    EXTERNAL_IDENTITY_LAST_LOGIN = "account.external_identity_last_login", _("Defina uma senha antes de desvincular seu único meio de acesso.")
    OWNER_TRANSFER_REQUIRED = (
        "account.owner_transfer_required",
        _("Transfira a propriedade ou encerre suas organizações antes de continuar."),
    )
    DELETION_ALREADY_SCHEDULED = "account.deletion_already_scheduled", _("A exclusão desta conta já está agendada.")
    REACTIVATION_INVALID = "account.reactivation_invalid", _("O link de reativação é inválido ou expirou.")
