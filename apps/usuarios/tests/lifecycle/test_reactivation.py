"""Reativação pública por confirmação assinada."""

from datetime import timedelta

from django.test import override_settings
from django.utils import timezone

import pytest

from apps.api.autenticacao.models import AuthToken, TokenMetaData, TokenType
from apps.api.core.errors import APIError
from apps.organizacoes.models import Organizacao, Papel, Vinculo
from apps.usuarios.accounts import Contas
from apps.usuarios.emails import emitir_token_reativacao, emitir_token_verificacao
from apps.usuarios.models import Usuario
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db


@override_settings(ACCOUNT_DELETION_GRACE_DAYS=7, ACCOUNT_REACTIVATION_TOKEN_MAX_AGE_SECONDS=3600)
def test_confirmar_reativacao_cancela_exclusao_sem_restaurar_sessoes_ou_vinculos():
    usuario = criar_usuario(email="reativar@example.com")
    organizacao = Organizacao.objects.create(nome="Organização", slug="reativacao")
    vinculo = Vinculo.objects.create(usuario=usuario, organizacao=organizacao, papel=Papel.MEMBRO)
    sessao, _ = AuthToken.objects.create(responsavel=usuario, type=TokenType.TOKEN)
    TokenMetaData.objects.create(token=sessao)
    Contas.agendar_exclusao(usuario)

    token = emitir_token_reativacao(usuario)
    reativado = Contas.confirmar_reativacao(token)

    reativado.refresh_from_db()
    vinculo.refresh_from_db()
    sessao.refresh_from_db()
    assert reativado.is_active is True
    assert reativado.is_deleted is False
    assert reativado.exclusao_solicitada_em is None
    assert reativado.exclusao_agendada_para is None
    assert sessao.revoked_at is not None
    assert vinculo.is_active is False
    assert vinculo.is_deleted is False


@override_settings(ACCOUNT_DELETION_GRACE_DAYS=7, ACCOUNT_REACTIVATION_TOKEN_MAX_AGE_SECONDS=3600)
def test_confirmacao_depois_da_carencia_e_irreversivel_mesmo_antes_da_task():
    usuario = criar_usuario(email="prazo-vencido@example.com")
    Contas.agendar_exclusao(usuario)
    token = emitir_token_reativacao(usuario)
    Usuario.all_objects.filter(pk=usuario.pk).update(exclusao_agendada_para=timezone.now() - timedelta(seconds=1))

    with pytest.raises(APIError) as excinfo:
        Contas.confirmar_reativacao(token)

    usuario.refresh_from_db()
    assert excinfo.value.code == "account.reactivation_invalid"
    assert usuario.is_active is False
    assert usuario.exclusao_agendada_para is not None


@override_settings(ACCOUNT_REACTIVATION_TOKEN_MAX_AGE_SECONDS=3600)
def test_token_de_outro_proposito_nao_serve_para_reativacao():
    usuario = criar_usuario(email="salt-proprio@example.com")
    token = emitir_token_verificacao(usuario)
    Contas.desativar(usuario)

    with pytest.raises(APIError) as excinfo:
        Contas.confirmar_reativacao(token)

    assert excinfo.value.code == "account.reactivation_invalid"
