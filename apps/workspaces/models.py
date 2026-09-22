from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.api.base.models import BaseTenantless
from utils.logs import register


class Workspace(BaseTenantless):
    api_scope_resource = "workspaces"

    organizacao = models.ForeignKey(
        "organizacoes.Organizacao",
        verbose_name=_("organização"),
        on_delete=models.CASCADE,
        related_name="workspaces",
        help_text=_("Organização proprietária do Workspace."),
        db_comment="Organização proprietária do Workspace.",
    )
    nome = models.CharField(_("nome"), max_length=150, help_text=_("Nome do Workspace."), db_comment="Nome do Workspace.")
    slug = models.SlugField(
        _("slug"),
        max_length=60,
        help_text=_("Identificador público dentro da Organização."),
        db_comment="Identificador público dentro da Organização.",
    )

    def __str__(self):
        return f"{self.nome} @ {self.organizacao}"

    class Meta:
        db_table = "workspace"
        ordering = ["nome"]
        constraints = [
            models.UniqueConstraint(
                fields=["organizacao", "slug"],
                condition=models.Q(is_deleted=False),
                name="workspace_organizacao_slug_unico_nao_excluido",
            ),
        ]


class VinculoWorkspace(BaseTenantless):
    api_scope_resource = "workspace_memberships"

    vinculo = models.ForeignKey(
        "organizacoes.Vinculo",
        on_delete=models.CASCADE,
        related_name="workspaces",
        help_text=_("Vínculo organizacional que recebe acesso."),
        db_comment="Vínculo organizacional que recebe acesso.",
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="vinculos",
        help_text=_("Workspace acessível pelo vínculo."),
        db_comment="Workspace acessível pelo vínculo.",
    )
    selected_for_view = models.BooleanField(
        _("selecionado para visualização"),
        default=True,
        help_text=_("Inclui o Workspace nas consultas automáticas."),
        db_comment="Inclui o Workspace nas consultas automáticas.",
    )

    def __str__(self):
        return f"{self.vinculo} @ {self.workspace}"

    class Meta:
        db_table = "workspace_vinculo"
        constraints = [
            models.UniqueConstraint(
                fields=["vinculo", "workspace"],
                condition=models.Q(is_deleted=False),
                name="workspace_vinculo_unico_nao_excluido",
            ),
        ]
        indexes = [
            models.Index(fields=["vinculo", "is_active", "selected_for_view"], name="wv_vinculo_ativo_sel_idx"),
            models.Index(fields=["workspace", "is_active"], name="wv_workspace_ativo_idx"),
        ]


register(Workspace)
register(VinculoWorkspace)
