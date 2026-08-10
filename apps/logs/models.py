from django.utils.translation import gettext_lazy as _

from auditlog.models import AbstractLogEntry


class LogAlteracao(AbstractLogEntry):
    """Trilha de auditoria do projeto.

    Os campos vêm de `AbstractLogEntry` com os nomes do auditlog (`actor`,
    `content_type`, `timestamp`). As properties abaixo são açúcar sintático: dão
    a esses campos os nomes que a API publica (`user`, `model`, `created_at`),
    sem renomear coluna nem duplicar dado.
    """

    @property
    def metadata(self):
        return self.additional_data

    @property
    def user(self):
        """Usuário responsável pela alteração; `None` quando não houve ator autenticado."""
        return self.actor

    @property
    def model(self):
        """Rótulo do modelo auditado no formato `app_label.model`."""
        return f"{self.content_type.app_label}.{self.content_type.model}"

    @property
    def created_at(self):
        """Momento em que o registro foi gravado."""
        return self.timestamp

    class Meta:
        db_table = "log_alteracao"
        ordering = ["-timestamp"]
        get_latest_by = "timestamp"
        verbose_name = _("Log de alteração")
        verbose_name_plural = _("Logs de alteração")
