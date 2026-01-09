from typing import Literal

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.utils.translation import gettext_lazy as _

from auditlog.registry import auditlog

from apps.api.base.models import Base

# TODO pesquisar nomeclaturas de mercado para isso
TCodigoConfiguracao = Literal[]


class CodigosConfiguracao(models.TextChoices):
    pass


primitive_types_codes_map = {}


class Configuracao(Base):
    owner = None

    codigo = models.CharField(_("código"), max_length=40, editable=False, choices=CodigosConfiguracao.choices)
    descricao = models.CharField(_("descrição"), max_length=500)
    valor = models.CharField(_("valor"), max_length=150)

    @property
    def valor_real(self):
        return self.normalize_value(self.codigo, self.valor)

    @classmethod
    def normalize_value(cls, codigo, valor):
        tipo_valor_parametro = primitive_types_codes_map[codigo]

        if tipo_valor_parametro is str:
            return valor

        if tipo_valor_parametro is int:
            return int(valor)

        if tipo_valor_parametro is float:
            return float(valor)

        if tipo_valor_parametro is bool:
            return True if valor == "True" else False

        raise ImproperlyConfigured(f"Tipo de valor não suportado para o código de configuração {codigo}")

    class Meta:
        db_table = "configuracao"
        ordering = ["-id"]
        verbose_name = _("Configuração")
        verbose_name_plural = _("Configurações")

    def __str__(self):
        return self.codigo


auditlog.register(Configuracao, exclude_fields=settings.BASE_AUDITLOG_EXCLUDE_FIELDS)
