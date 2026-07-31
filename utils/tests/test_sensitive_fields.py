"""Tests for the sensitive-fields utility."""

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.db.migrations.state import ProjectState

import pytest
from cryptography.fernet import Fernet

from apps.api.base.models import BaseGlobal
from apps.api.base.serializers import BaseModelSerializer
from utils.sensitive_fields import encrypt, get_sensitive_field_keys


class RegistroSensivel(BaseGlobal):
    """Modelo efêmero para testar o contrato de campos cifrados."""

    documento = encrypt(models.CharField(max_length=14))
    anotacoes = encrypt(models.TextField(null=True))
    dados = encrypt(models.JSONField(default=dict))

    class Meta:
        app_label = "utils"


def test_keyring_remove_espacos_e_preserva_ordem(monkeypatch):
    monkeypatch.setenv("SENSITIVE_FIELD_KEYS", "new-key, old-key")

    assert get_sensitive_field_keys(require_configured=True) == [b"new-key", b"old-key"]


def test_keyring_ausente_falha_quando_obrigatorio(monkeypatch):
    monkeypatch.delenv("SENSITIVE_FIELD_KEYS", raising=False)

    with pytest.raises(ImproperlyConfigured, match="SENSITIVE_FIELD_KEYS"):
        get_sensitive_field_keys(require_configured=True)


def test_encrypt_preserva_plaintext_na_interface_e_cifra_para_o_banco(monkeypatch):
    monkeypatch.setenv("SENSITIVE_FIELD_KEYS", Fernet.generate_key().decode())
    field = RegistroSensivel._meta.get_field("documento")

    ciphertext = field.get_prep_value("123.456.789-00")

    assert ciphertext != "123.456.789-00"
    assert field.from_db_value(ciphertext, None, None) == "123.456.789-00"


def test_encrypt_adiciona_campos_a_write_only_e_serializer_os_omite():
    assert RegistroSensivel.extra_write_only_fields == ["documento", "anotacoes", "dados"]

    class RegistroSensivelSerializer(BaseModelSerializer):
        class Meta:
            model = RegistroSensivel
            fields = ["id", "documento", "anotacoes", "dados"]

    serializer = RegistroSensivelSerializer()

    assert all(serializer.fields[name].write_only for name in RegistroSensivel.extra_write_only_fields)


def test_campo_identifica_token_da_chave_ativa(monkeypatch):
    active_key = Fernet.generate_key()
    old_key = Fernet.generate_key()
    monkeypatch.setenv("SENSITIVE_FIELD_KEYS", b",".join([active_key, old_key]).decode())
    field = RegistroSensivel._meta.get_field("documento")

    assert field.is_encrypted_with_active_key(Fernet(active_key).encrypt(b"ativo").decode()) is True
    assert field.is_encrypted_with_active_key(Fernet(old_key).encrypt(b"antigo").decode()) is False


def test_campo_cifrado_no_estado_do_django_pode_ser_desconstruido_para_migrations():
    field = ProjectState.from_apps(apps).models[("usuarios", "usuario")].fields["phone_number"]

    name, path, args, kwargs = field.deconstruct()

    assert name is None
    assert path == "utils.sensitive_fields.encrypt"
    assert isinstance(args[0], models.CharField)
    assert args[0].null is True
    assert args[0].blank is True
    assert kwargs == {}
