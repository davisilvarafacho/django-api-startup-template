from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.api.base.models import Base


class ParametrosManager:
    # TODO definir um padrão para guardar tipos
    def get_by_code(self, code: str):
        return self.get(code=code)


class Parametro(Base):
    code = models.CharField(
        _("código"),
        max_length=80,
        help_text=_("Código identificador do parâmetro."),
        db_comment="Código identificador do parâmetro.",
    )
    description = models.CharField(
        _("descrição"),
        max_length=80,
        help_text=_("Descrição auxiliar do parâmetro."),
        db_comment="Descrição auxiliar do parâmetro.",
    )
    value = models.TextChoices(
        _("valor"),
        help_text=_("Valor do parâmetro."),
        db_comment="Valor do parâmetro.",
    )

    class Meta:
        ordering = ["-id"]
        db_table = "parametro"
        indexes = [
            models.Index(fields=["organizacao", "workspace", "code"], name="parametro_code_idx"),
        ]
