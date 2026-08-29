from django.db import models
from django.utils.translation import gettext_lazy as _


class OrganizationErrorCode(models.TextChoices):
    HEADER_REQUIRED = "organizations.header_required", _("Header obrigatório.")
    MEMBERSHIP_REQUIRED = "organizations.membership_required", _("Vínculo ativo obrigatório.")
    MEMBERSHIP_INACTIVE = "organizations.membership_inactive", _("O vínculo com esta organização está inativo.")
    ORGANIZATION_INACTIVE = "organizations.organization_inactive", _("Esta organização está inativa.")
    ROLE_INSUFFICIENT = "organizations.role_insufficient", _("Papel insuficiente.")
    INVITATION_INVALID = "organizations.invitation_invalid", _("Convite inválido.")
    INVITATION_EXPIRED = "organizations.invitation_expired", _("Convite expirado ou já utilizado.")
    INVITATION_EMAIL_MISMATCH = (
        "organizations.invitation_email_mismatch",
        _("Este convite pertence a outro e-mail."),
    )
    TENANT_MISMATCH = (
        "organizations.tenant_mismatch",
        _("O header X-Organization não corresponde à organização desta credencial."),
    )
    CLOSURE_PENDING = (
        "organizations.closure_pending",
        _("Esta organização possui encerramento pendente."),
    )
    INACTIVE = "organizations.inactive", _("Esta organização está inativa.")
