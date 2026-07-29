"""Eventos auditáveis do ciclo de vida de API keys (`audit.emit_api_key_event`)."""
from unittest.mock import patch

from django.db import IntegrityError

import pytest
from knox.models import get_token_model
from threadlocals.threadlocals import set_current_user, set_thread_variable

from apps.api.autenticacao.models import TokenMetaData, TokenType
from apps.api.autenticacao.services import (
    create_api_key,
    resume_api_key,
    revoke_api_key,
    rotate_api_key,
    suspend_api_key,
    update_api_key,
)
from apps.organizacoes.models import Organizacao, Papel, Vinculo
from apps.usuarios.factories import UsuarioFactory

pytestmark = pytest.mark.django_db

AuthToken = get_token_model()


@pytest.fixture(autouse=True)
def _limpar_thread_locals():
    set_current_user(None)
    set_thread_variable("request", None)
    yield
    set_current_user(None)
    set_thread_variable("request", None)


@pytest.fixture
def organizacao():
    return Organizacao.objects.create(nome="Org", slug="org-audit")


@pytest.fixture
def ator(organizacao):
    usuario = UsuarioFactory()
    Vinculo.objects.create(usuario=usuario, organizacao=organizacao, papel=Papel.ADMINISTRADOR)
    return usuario


@pytest.fixture
def responsavel(organizacao):
    usuario = UsuarioFactory()
    Vinculo.objects.create(usuario=usuario, organizacao=organizacao, papel=Papel.MEMBRO)
    return usuario


def _api_key(responsavel, organizacao, **campos):
    instance, token = AuthToken.objects.create(
        responsavel=responsavel,
        type=TokenType.API_KEY,
        created_by=responsavel,
        organization=organizacao,
        name="Integração",
        **campos,
    )
    TokenMetaData.objects.create(token=instance)
    return instance, token


def _campos_obrigatorios(payload, *, instance, actor):
    assert payload["token_uuid"] == str(instance.uuid)
    assert payload["responsavel_id"] == instance.responsavel_id
    assert payload["created_by_id"] == instance.created_by_id
    assert payload["organization_id"] == instance.organization_id
    assert payload["actor_id"] == (actor.pk if actor is not None else None)
    assert "request_id" in payload


def test_create_emite_evento_com_metadados_e_sem_segredo(ator, organizacao, responsavel):
    with patch("apps.api.autenticacao.audit.capture") as capture_mock:
        issued = create_api_key(
            responsavel=responsavel,
            created_by=ator,
            name="Integração",
            scopes=["teams:read"],
            organization=organizacao,
        )

    capture_mock.assert_called_once()
    nome_evento, kwargs = capture_mock.call_args
    assert nome_evento[0] == "api_key_create"
    payload = kwargs["properties"]
    assert payload["event"] == "create"
    _campos_obrigatorios(payload, instance=issued.instance, actor=ator)
    assert issued.plain_token not in str(payload)


def test_rotate_emite_evento_referenciando_a_key_anterior(ator, organizacao, responsavel):
    instance, _token = _api_key(responsavel, organizacao)

    with patch("apps.api.autenticacao.audit.capture") as capture_mock:
        issued = rotate_api_key(instance, actor=ator)

    capture_mock.assert_called_once()
    nome_evento, kwargs = capture_mock.call_args
    assert nome_evento[0] == "api_key_rotate"
    payload = kwargs["properties"]
    _campos_obrigatorios(payload, instance=issued.instance, actor=ator)
    assert payload["replaces_uuid"] == str(instance.uuid)
    assert issued.plain_token not in str(payload)


def test_suspend_e_resume_emitem_eventos(ator, organizacao, responsavel):
    instance, _token = _api_key(responsavel, organizacao)

    with patch("apps.api.autenticacao.audit.capture") as capture_mock:
        suspend_api_key(instance, actor=ator, reason="Investigação")

    nome_evento, kwargs = capture_mock.call_args
    assert nome_evento[0] == "api_key_suspend"
    _campos_obrigatorios(kwargs["properties"], instance=instance, actor=ator)

    with patch("apps.api.autenticacao.audit.capture") as capture_mock:
        resume_api_key(instance, actor=ator)

    nome_evento, kwargs = capture_mock.call_args
    assert nome_evento[0] == "api_key_resume"
    _campos_obrigatorios(kwargs["properties"], instance=instance, actor=ator)


def test_revoke_emite_evento(ator, organizacao, responsavel):
    instance, _token = _api_key(responsavel, organizacao)

    with patch("apps.api.autenticacao.audit.capture") as capture_mock:
        revoke_api_key(instance, actor=ator)

    nome_evento, kwargs = capture_mock.call_args
    assert nome_evento[0] == "api_key_revoke"
    _campos_obrigatorios(kwargs["properties"], instance=instance, actor=ator)


def test_update_responsavel_emite_responsible_changed(ator, organizacao, responsavel):
    instance, _token = _api_key(responsavel, organizacao)
    outro_responsavel = UsuarioFactory()
    Vinculo.objects.create(usuario=outro_responsavel, organizacao=organizacao, papel=Papel.MEMBRO)

    with patch("apps.api.autenticacao.audit.capture") as capture_mock:
        update_api_key(instance, actor=ator, responsavel=outro_responsavel)

    nome_evento, kwargs = capture_mock.call_args
    assert nome_evento[0] == "api_key_responsible_changed"
    payload = kwargs["properties"]
    assert payload["previous_responsavel_id"] == responsavel.pk
    assert payload["new_responsavel_id"] == outro_responsavel.pk


def test_update_scopes_emite_scopes_changed(ator, organizacao, responsavel):
    instance, _token = _api_key(responsavel, organizacao, scopes=["teams:read"])

    with patch("apps.api.autenticacao.audit.capture") as capture_mock:
        update_api_key(instance, actor=ator, scopes=["teams:read", "organizations:read"])

    nome_evento, kwargs = capture_mock.call_args
    assert nome_evento[0] == "api_key_scopes_changed"
    payload = kwargs["properties"]
    assert payload["previous_scopes"] == ["teams:read"]
    assert payload["new_scopes"] == ["teams:read", "organizations:read"]


def test_update_sem_mudancas_nao_emite_evento(ator, organizacao, responsavel):
    instance, _token = _api_key(responsavel, organizacao, scopes=["teams:read"])

    with patch("apps.api.autenticacao.audit.capture") as capture_mock:
        update_api_key(instance, actor=ator, name=instance.name, scopes=["teams:read"])

    capture_mock.assert_not_called()


def test_update_nao_emite_evento_quando_persistencia_falha(
    ator,
    organizacao,
    responsavel,
):
    instance, _token = _api_key(responsavel, organizacao, scopes=["teams:read"])

    with (
        patch.object(instance, "save", side_effect=IntegrityError("boom")),
        patch("apps.api.autenticacao.audit.capture") as capture_mock,
        pytest.raises(IntegrityError),
    ):
        update_api_key(instance, actor=ator, scopes=["organizations:read"])

    capture_mock.assert_not_called()


def test_suspensao_automatica_emite_evento_sem_ator(organizacao):
    from apps.api.autenticacao.services import ensure_api_key_still_valid

    responsavel_local = UsuarioFactory()
    vinculo = Vinculo.objects.create(usuario=responsavel_local, organizacao=organizacao, papel=Papel.MEMBRO)
    instance, _token = _api_key(responsavel_local, organizacao)

    vinculo.ativo = False
    vinculo.save(update_fields=["ativo"])

    with patch("apps.api.autenticacao.audit.capture") as capture_mock:
        ensure_api_key_still_valid(instance)

    nome_evento, kwargs = capture_mock.call_args
    assert nome_evento[0] == "api_key_suspend"
    payload = kwargs["properties"]
    assert payload["actor_id"] is None
    assert payload["automatic"] is True
