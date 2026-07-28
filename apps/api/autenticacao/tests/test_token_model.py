"""Contrato do modelo de token próprio, compatível com o Knox."""
from django.db import IntegrityError, transaction

import pytest

from apps.api.autenticacao.models import AuthToken, TokenType
from apps.organizacoes.models import Organizacao
from apps.usuarios.factories import UsuarioFactory


@pytest.fixture
def usuario():
    return UsuarioFactory()


def test_settings_aponta_para_token_proprio(settings):
    assert settings.KNOX_TOKEN_MODEL == "autenticacao.AuthToken"


def test_meta_e_swappable():
    assert AuthToken._meta.swappable == "KNOX_TOKEN_MODEL"


@pytest.mark.django_db
def test_manager_retorna_instancia_e_plain_token(usuario):
    instance, plain = AuthToken.objects.create(user=usuario)

    assert instance.responsavel == usuario
    assert instance.user == usuario
    assert instance.created == instance.created_at
    assert plain.startswith(instance.token_key)
    assert instance.digest != plain


@pytest.mark.django_db
def test_manager_aceita_responsavel_diretamente(usuario):
    instance, _plain = AuthToken.objects.create(responsavel=usuario)

    assert instance.responsavel == usuario


@pytest.mark.django_db
def test_uuid_e_unico_e_gerado_automaticamente(usuario):
    instance1, _ = AuthToken.objects.create(user=usuario)
    instance2, _ = AuthToken.objects.create(user=usuario)

    assert instance1.uuid is not None
    assert instance1.uuid != instance2.uuid


@pytest.mark.django_db
def test_related_name_auth_token_set(usuario):
    instance, _ = AuthToken.objects.create(user=usuario)

    assert instance in usuario.auth_token_set.all()


def test_type_default_e_token_de_sessao():
    field = AuthToken._meta.get_field("type")

    assert field.default == TokenType.TOKEN


@pytest.mark.django_db
def test_expiry_e_calculado_a_partir_do_ttl(usuario):
    from datetime import timedelta

    instance, _ = AuthToken.objects.create(user=usuario, expiry=timedelta(hours=1))

    assert instance.expiry is not None


@pytest.mark.django_db
def test_expiry_none_significa_sem_expiracao(usuario):
    instance, _ = AuthToken.objects.create(user=usuario, expiry=None)

    assert instance.expiry is None


@pytest.mark.django_db
def test_api_key_exige_organizacao(usuario):
    with pytest.raises(IntegrityError), transaction.atomic():
        AuthToken.objects.create(
            responsavel=usuario,
            type=TokenType.API_KEY,
            name="Integração",
            organization=None,
        )


@pytest.mark.django_db
def test_sessao_nao_pode_ter_organizacao(usuario):
    organizacao = Organizacao.objects.create(nome="Org", slug="org-token-model")

    with pytest.raises(IntegrityError), transaction.atomic():
        AuthToken.objects.create(responsavel=usuario, type=TokenType.TOKEN, organization=organizacao)


@pytest.mark.django_db
def test_api_key_com_organizacao_e_valida(usuario):
    organizacao = Organizacao.objects.create(nome="Org", slug="org-token-model-2")

    instance, plain = AuthToken.objects.create(
        responsavel=usuario,
        type=TokenType.API_KEY,
        name="Integração",
        organization=organizacao,
        scopes=["teams:read"],
    )

    assert instance.organization == organizacao
    assert instance.scopes == ["teams:read"]
    assert plain


@pytest.mark.django_db
def test_created_by_vem_do_mixin_de_auditoria(usuario):
    criador = UsuarioFactory()
    instance, _ = AuthToken.objects.create(user=usuario, created_by=criador)

    assert instance.created_by == criador
