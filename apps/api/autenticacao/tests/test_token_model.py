"""Contrato do modelo de token swappable compatível com Knox."""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.utils import timezone

import pytest
from knox.models import get_token_model

from apps.api.autenticacao.authentications import TypedTokenAuthentication
from apps.api.autenticacao.models import AuthToken, TokenType


def test_settings_aponta_para_token_proprio(settings):
    assert settings.KNOX_TOKEN_MODEL == "autenticacao.AuthToken"


def test_tipos_efemeros_sao_explicitos():
    assert AuthToken.EPHEMERAL_TYPES == frozenset({TokenType.PRE_AUTH, TokenType.RESET_PASSWORD})


@pytest.mark.parametrize("field_name", ["digest", "token_key", "responsavel", "created_at", "expiry", "type", "scopes"])
def test_campos_de_auth_token_documentam_o_schema(field_name):
    field = AuthToken._meta.get_field(field_name)

    assert field.help_text
    assert field.help_text == field.db_comment


@pytest.mark.django_db
def test_typed_authentication_autentica_token_do_modelo_configurado(usuario):
    instance, plain_token = AuthToken.objects.create(user=usuario, type=TokenType.TOKEN)

    authenticated_user, authenticated_token = TypedTokenAuthentication().authenticate_credentials(plain_token.encode())

    assert get_token_model() is AuthToken
    assert authenticated_user == usuario
    assert authenticated_token == instance


@pytest.mark.django_db
def test_manager_retorna_token_puro_uma_vez(usuario):
    instance, plain = AuthToken.objects.create(user=usuario, type=TokenType.TOKEN)

    assert instance.responsavel == usuario
    assert instance.user == usuario
    assert instance.created == instance.created_at
    assert plain.startswith(instance.token_key)
    assert instance.digest != plain


@pytest.mark.django_db
def test_manager_converte_ttl_relativo_em_data_absoluta(usuario):
    before = timezone.now()
    instance, _ = AuthToken.objects.create(user=usuario, expiry=timedelta(minutes=5))

    assert instance.expiry is not None
    assert instance.expiry >= before + timedelta(minutes=5)


def test_is_expired():
    token = AuthToken(expiry=timezone.now())

    assert token.is_expired is True
    assert AuthToken(expiry=None).is_expired is False


@pytest.mark.django_db
@pytest.mark.parametrize("token_type", [TokenType.PRE_AUTH, TokenType.RESET_PASSWORD])
def test_token_efemero_sem_expiry_e_recusado(usuario, token_type):
    with pytest.raises(ValidationError, match="expiry"):
        AuthToken.objects.create(user=usuario, type=token_type, expiry=None)
