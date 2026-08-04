"""Tests for the encrypted model fields built by ``encrypt``."""

from django.apps import apps
from django.db import models
from django.db.migrations.state import ProjectState

from cryptography.fernet import Fernet

from apps.api.base.serializers import BaseModelSerializer
from internal_frameworks.sensitive_fields.tests.models import RegistroSensivel


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
    assert path == "internal_frameworks.sensitive_fields.fields.encrypt"
    assert isinstance(args[0], models.CharField)
    assert args[0].null is True
    assert args[0].blank is True
    assert kwargs == {}
