"""Contrato do modelo de token próprio, compatível com o Knox."""

from datetime import timedelta

from django.core.exceptions import ValidationError

import pytest

from apps.api.autenticacao.models import AuthToken, TokenType
from apps.organizacoes.models import Organizacao, Vinculo
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
    instance, _ = AuthToken.objects.create(user=usuario, expiry=timedelta(hours=1))

    assert instance.expiry is not None


@pytest.mark.django_db
def test_expiry_none_significa_sem_expiracao(usuario):
    instance, _ = AuthToken.objects.create(user=usuario, expiry=None)

    assert instance.expiry is None


@pytest.mark.django_db
def test_api_key_exige_organizacao(usuario):
    with pytest.raises(ValidationError, match="organization"):
        AuthToken.objects.create(
            responsavel=usuario,
            type=TokenType.API_KEY,
            created_by=usuario,
            name="Integração",
            organization=None,
        )


@pytest.mark.django_db
def test_sessao_nao_pode_ter_organizacao(usuario):
    organizacao = Organizacao.objects.create(nome="Org", slug="org-token-model")

    with pytest.raises(ValidationError):
        AuthToken.objects.create(responsavel=usuario, type=TokenType.TOKEN, organization=organizacao)


@pytest.mark.django_db
def test_api_key_com_organizacao_e_valida(usuario):
    organizacao = Organizacao.objects.create(nome="Org", slug="org-token-model-2")
    Vinculo.objects.create(usuario=usuario, organizacao=organizacao)

    instance, plain = AuthToken.objects.create(
        responsavel=usuario,
        type=TokenType.API_KEY,
        created_by=usuario,
        name="Integração",
        organization=organizacao,
        scopes=["teams:read"],
    )

    assert instance.organization == organizacao
    assert instance.scopes == ["teams:read"]
    assert plain


@pytest.mark.django_db
def test_api_key_exige_nome_nao_vazio(usuario):
    organizacao = Organizacao.objects.create(nome="Org", slug="org-token-model-nome")
    Vinculo.objects.create(usuario=usuario, organizacao=organizacao)

    with pytest.raises(ValidationError, match="nome"):
        AuthToken.objects.create(
            responsavel=usuario,
            type=TokenType.API_KEY,
            created_by=usuario,
            name="  ",
            organization=organizacao,
        )


@pytest.mark.django_db
def test_api_key_exige_criador(usuario):
    organizacao = Organizacao.objects.create(nome="Org", slug="org-token-model-criador")
    Vinculo.objects.create(usuario=usuario, organizacao=organizacao)

    with pytest.raises(ValidationError, match="created_by"):
        AuthToken.objects.create(
            responsavel=usuario,
            type=TokenType.API_KEY,
            name="Integração",
            organization=organizacao,
        )


@pytest.mark.django_db
def test_api_key_exige_responsavel_ativo_e_vinculado(usuario):
    organizacao = Organizacao.objects.create(nome="Org", slug="org-token-model-vinculo")

    with pytest.raises(ValidationError, match="responsavel"):
        AuthToken.objects.create(
            responsavel=usuario,
            type=TokenType.API_KEY,
            created_by=usuario,
            name="Integração",
            organization=organizacao,
        )

    Vinculo.objects.create(usuario=usuario, organizacao=organizacao)
    usuario.is_active = False
    usuario.save(update_fields=["is_active"])

    with pytest.raises(ValidationError, match="responsavel"):
        AuthToken.objects.create(
            responsavel=usuario,
            type=TokenType.API_KEY,
            created_by=usuario,
            name="Integração",
            organization=organizacao,
        )


@pytest.mark.django_db
def test_api_key_rejeita_scope_nao_registrado(usuario):
    organizacao = Organizacao.objects.create(nome="Org", slug="org-token-model-scope")
    Vinculo.objects.create(usuario=usuario, organizacao=organizacao)

    with pytest.raises(ValidationError, match="scopes"):
        AuthToken.objects.create(
            responsavel=usuario,
            type=TokenType.API_KEY,
            created_by=usuario,
            name="Integração",
            organization=organizacao,
            scopes=["unknown:read"],
        )


@pytest.mark.django_db
def test_sessao_rejeita_campos_exclusivos_de_api_key(usuario):
    with pytest.raises(ValidationError):
        AuthToken.objects.create(
            responsavel=usuario,
            type=TokenType.TOKEN,
            name="Sessão nomeada",
            scopes=["teams:read"],
        )


@pytest.mark.django_db
def test_str_do_token_nao_expoe_digest_nem_token_key(usuario):
    instance, _plain = AuthToken.objects.create(user=usuario)

    representation = str(instance)

    assert instance.digest not in representation
    assert instance.token_key not in representation


@pytest.mark.django_db
def test_created_by_vem_do_mixin_de_auditoria(usuario):
    criador = UsuarioFactory()
    instance, _ = AuthToken.objects.create(user=usuario, created_by=criador)

    assert instance.created_by == criador
