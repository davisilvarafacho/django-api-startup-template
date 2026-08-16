"""Protocolo transacional de exclusão de conta (`Usuario.delete`).

A exclusão lógica precisa interromper todo acesso autenticado no mesmo commit:
sem isso, uma sessão, uma API key ou um dispositivo confiável emitido antes
continua valendo depois de a conta deixar de existir para a API.
"""

import threading

from django.db import connections, transaction

import pytest

from apps.api.autenticacao import mfa, services
from apps.api.autenticacao.models import AuthToken, TokenMetaData, TokenType
from apps.api.autenticacao.passwords import issue_password_reset
from apps.organizacoes.models import Organizacao, Vinculo
from apps.usuarios.models import Usuario, UsuarioQuerySet
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db(transaction=True)


def criar_credenciais(usuario, organizacao):
    """Uma credencial de cada tipo, mais um dispositivo confiável real."""
    tokens = []
    for token_type in (TokenType.TOKEN, TokenType.PRE_AUTH, TokenType.RESET_PASSWORD):
        token, _ = AuthToken.objects.create(responsavel=usuario, type=token_type)
        TokenMetaData.objects.create(token=token)
        tokens.append(token)

    api_key, _ = AuthToken.objects.create(
        responsavel=usuario,
        type=TokenType.API_KEY,
        created_by=usuario,
        organization=organizacao,
        name="Integração",
        scopes=["teams:read"],
    )
    TokenMetaData.objects.create(token=api_key)
    tokens.append(api_key)

    dispositivo = mfa.create_trusted_device(usuario, {"device_name": "Notebook"})
    return tokens, dispositivo.instance


def test_delete_desativa_a_conta_e_revoga_todas_as_credenciais():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Org", slug="org-exclusao")
    Vinculo.objects.create(usuario=usuario, organizacao=organizacao)
    tokens, dispositivo = criar_credenciais(usuario, organizacao)

    resultado = usuario.delete()

    usuario.refresh_from_db(from_queryset=Usuario.all_objects.all())
    dispositivo.refresh_from_db()
    assert resultado == (1, {Usuario._meta.label: 1})
    assert usuario.is_deleted is True
    assert usuario.is_active is False
    assert not AuthToken.objects.filter(pk__in=[token.pk for token in tokens], revoked_at__isnull=True).exists()
    assert dispositivo.revoked_at is not None
    # Nada é apagado: a auditoria continua enxergando as credenciais revogadas.
    assert AuthToken.objects.filter(responsavel=usuario).count() == len(tokens)


def test_delete_repetido_nao_altera_revogacao_ja_persistida():
    usuario = criar_usuario()
    token, _ = AuthToken.objects.create(responsavel=usuario, type=TokenType.TOKEN)

    usuario.delete()
    token.refresh_from_db()
    primeira_revogacao = token.revoked_at

    assert usuario.delete() == (0, {})
    token.refresh_from_db()
    assert token.revoked_at == primeira_revogacao


def test_delete_repetido_atualiza_uma_instancia_obsoleta():
    usuario = criar_usuario()
    obsoleto = Usuario.all_objects.get(pk=usuario.pk)
    usuario.delete()

    assert obsoleto.delete() == (0, {})
    assert obsoleto.is_deleted is True
    assert obsoleto.is_active is False


def test_todos_os_managers_usam_o_queryset_de_exclusao_segura():
    assert isinstance(Usuario.objects.all(), UsuarioQuerySet)
    assert isinstance(Usuario.all_objects.all(), UsuarioQuerySet)
    assert isinstance(Usuario.ativos.all(), UsuarioQuerySet)


def test_rollback_preserva_a_conta_e_as_credenciais():
    usuario = criar_usuario()
    token, _ = AuthToken.objects.create(responsavel=usuario, type=TokenType.TOKEN)
    dispositivo = mfa.create_trusted_device(usuario, {"device_name": "Notebook"}).instance

    def excluir_e_falhar():
        with transaction.atomic():
            usuario.delete()
            raise RuntimeError("rollback")

    with pytest.raises(RuntimeError, match="rollback"):
        excluir_e_falhar()

    usuario.refresh_from_db(from_queryset=Usuario.all_objects.all())
    token.refresh_from_db()
    dispositivo.refresh_from_db()
    assert usuario.is_deleted is False
    assert usuario.is_active is True
    assert token.revoked_at is None
    assert dispositivo.revoked_at is None


def test_queryset_delete_usa_o_mesmo_protocolo():
    usuario = criar_usuario()
    token, _ = AuthToken.objects.create(responsavel=usuario, type=TokenType.TOKEN)

    resultado = Usuario.objects.filter(pk=usuario.pk).delete()

    usuario.refresh_from_db(from_queryset=Usuario.all_objects.all())
    token.refresh_from_db()
    assert resultado == (1, {Usuario._meta.label: 1})
    assert usuario.is_deleted is True
    assert usuario.is_active is False
    assert token.revoked_at is not None


def test_queryset_update_is_deleted_nao_contorna_o_protocolo():
    usuario = criar_usuario()
    token, _ = AuthToken.objects.create(responsavel=usuario, type=TokenType.TOKEN)

    alterados = Usuario.all_objects.filter(pk=usuario.pk).update(is_deleted=True)

    usuario.refresh_from_db(from_queryset=Usuario.all_objects.all())
    token.refresh_from_db()
    assert alterados == 1
    assert usuario.is_deleted is True
    assert usuario.is_active is False
    assert token.revoked_at is not None


def test_queryset_delete_faz_rollback_do_lote_inteiro(monkeypatch):
    primeiro = criar_usuario()
    segundo = criar_usuario()
    token, _ = AuthToken.objects.create(responsavel=primeiro, type=TokenType.TOKEN)
    original = services.revoke_all_user_credentials

    def revogar_e_falhar(user, *, actor=None, using=None):
        resultado = original(user, actor=actor, using=using)
        if user.pk == segundo.pk:
            raise RuntimeError("falha na segunda conta")
        return resultado

    monkeypatch.setattr(services, "revoke_all_user_credentials", revogar_e_falhar)

    with pytest.raises(RuntimeError, match="segunda conta"):
        Usuario.objects.filter(pk__in=[primeiro.pk, segundo.pk]).delete()

    assert Usuario.objects.filter(pk__in=[primeiro.pk, segundo.pk]).count() == 2
    token.refresh_from_db()
    assert token.revoked_at is None


def test_ativos_delete_revoga_e_ignora_conta_ja_excluida():
    ativo = criar_usuario()
    excluido = criar_usuario()
    excluido.delete()
    token, _ = AuthToken.objects.create(responsavel=ativo, type=TokenType.TOKEN)

    resultado = Usuario.ativos.filter(pk__in=[ativo.pk, excluido.pk]).delete()

    ativo.refresh_from_db(from_queryset=Usuario.all_objects.all())
    token.refresh_from_db()
    assert resultado == (1, {Usuario._meta.label: 1})
    assert ativo.is_deleted is True
    assert ativo.is_active is False
    assert token.revoked_at is not None


def test_delete_propaga_o_alias_do_banco_para_tokens_e_dispositivos(monkeypatch):
    aliases = {}

    def revogar_credenciais(user, *, actor=None, using=None):
        aliases["credenciais"] = using
        return 0

    def revogar_dispositivos(user, *, using=None):
        aliases["dispositivos"] = using
        return 0

    monkeypatch.setattr(services, "revoke_all_user_credentials", revogar_credenciais)
    monkeypatch.setattr(mfa, "revoke_trusted_devices", revogar_dispositivos)

    Usuario.objects.filter(pk=criar_usuario().pk).delete()
    assert aliases == {"credenciais": "default", "dispositivos": "default"}

    aliases.clear()
    criar_usuario().delete(using="default")
    assert aliases == {"credenciais": "default", "dispositivos": "default"}


def test_emissao_concorrente_espera_o_lock_da_conta():
    """Emissão e exclusão disputam o mesmo lock de `Usuario`, nessa ordem.

    Sem a coordenação pelo lock da conta, um reset emitido no intervalo entre a
    checagem e o commit da exclusão nasceria válido para uma conta que já não
    existe.
    """
    usuario = criar_usuario()
    exclusao_iniciada = threading.Event()
    liberar_commit = threading.Event()
    resultado = {}

    def excluir():
        try:
            with transaction.atomic():
                usuario.delete()
                exclusao_iniciada.set()
                liberar_commit.wait(timeout=10)
        finally:
            connections.close_all()

    def emitir():
        try:
            resultado["emitido"] = issue_password_reset(usuario)
        except Exception as erro:
            resultado["erro"] = erro
        finally:
            connections.close_all()

    excluidor = threading.Thread(target=excluir)
    emissor = threading.Thread(target=emitir)
    excluidor.start()
    assert exclusao_iniciada.wait(timeout=10)
    emissor.start()
    emissor.join(timeout=1)
    bloqueado_durante_a_exclusao = emissor.is_alive()
    liberar_commit.set()
    excluidor.join(timeout=10)
    emissor.join(timeout=10)

    assert bloqueado_durante_a_exclusao is True
    assert resultado.get("erro") is None
    assert resultado["emitido"] is None
    assert not AuthToken.objects.filter(responsavel=usuario, type=TokenType.RESET_PASSWORD, revoked_at__isnull=True).exists()


def test_dispositivo_confiavel_concorrente_nao_sobrevive_a_exclusao():
    usuario = criar_usuario()
    exclusao_iniciada = threading.Event()
    liberar_commit = threading.Event()
    resultado = {}

    def excluir():
        try:
            with transaction.atomic():
                usuario.delete()
                exclusao_iniciada.set()
                liberar_commit.wait(timeout=10)
        finally:
            connections.close_all()

    def criar_dispositivo():
        try:
            resultado["dispositivo"] = mfa.create_trusted_device(usuario, {"device_name": "Concorrente"})
        except Exception as erro:
            resultado["erro"] = erro
        finally:
            connections.close_all()

    excluidor = threading.Thread(target=excluir)
    criador = threading.Thread(target=criar_dispositivo)
    excluidor.start()
    assert exclusao_iniciada.wait(timeout=10)
    criador.start()
    criador.join(timeout=1)
    bloqueado_durante_a_exclusao = criador.is_alive()
    liberar_commit.set()
    excluidor.join(timeout=10)
    criador.join(timeout=10)

    assert bloqueado_durante_a_exclusao is True
    assert resultado.get("erro") is None
    assert resultado["dispositivo"] is None
    assert not mfa.TrustedDevice.objects.filter(user=usuario, revoked_at__isnull=True).exists()
