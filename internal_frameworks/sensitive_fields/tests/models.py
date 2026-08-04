"""Modelo efêmero compartilhado pelos testes de campos sensíveis."""

from django.db import models

from apps.api.base.models import BaseGlobal
from internal_frameworks.sensitive_fields.fields import encrypt


class RegistroSensivel(BaseGlobal):
    """Modelo efêmero para testar o contrato de campos cifrados."""

    documento = encrypt(models.CharField(max_length=14))
    anotacoes = encrypt(models.TextField(null=True))
    dados = encrypt(models.JSONField(default=dict))

    class Meta:
        app_label = "sensitive_fields"
