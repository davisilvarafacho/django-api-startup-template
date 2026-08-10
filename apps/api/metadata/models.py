from django.contrib.contenttypes.fields import GenericForeignKey
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.api.base.models import Base


class Metadata(Base):
    """Modelo para metadados genérico para qualquer modelo do sistema."""

    content_type = models.ForeignKey(
        verbose_name=_("content type"),
        to="contenttypes.ContentType",
        on_delete=models.CASCADE,
        related_name="metadata",
    )
    object_id = models.PositiveIntegerField(verbose_name=_("object ID"))
    object = GenericForeignKey(ct_field="content_type", fk_field="object_id")
    dados = models.JSONField(verbose_name=_("data"), default=dict, blank=True)

    class Meta:
        db_table = "metadata"
        ordering = ["-id"]
        verbose_name = _("Metadata")
        verbose_name_plural = _("Metadata")
        constraints = [
            models.UniqueConstraint(
                fields=["content_type", "object_id"],
                name="metadata_content_type_object_id_unique",
            ),
        ]
