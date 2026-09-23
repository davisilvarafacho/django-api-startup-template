from django.db import models
from django.utils.translation import gettext_lazy as _


class WorkspaceErrorCode(models.TextChoices):
    CURRENT_REQUIRED = "workspaces.current_required", _("Selecione um Workspace atual.")
    ACCESS_REQUIRED = "workspaces.access_required", _("Acesso ativo ao Workspace obrigatório.")
    WORKSPACE_INACTIVE = "workspaces.inactive", _("Este Workspace está inativo.")
    ORGANIZATION_MISMATCH = "workspaces.organization_mismatch", _("Workspace pertence a outra Organização.")
    MANDATORY_ACCESS = "workspaces.mandatory_access", _("Este acesso é obrigatório para o Papel atual.")
    INVALID_SELECTION = "workspaces.invalid_selection", _("Seleção de Workspaces inválida.")
