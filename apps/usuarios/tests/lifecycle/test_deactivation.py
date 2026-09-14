"""Desativação reversível de contas."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.db import close_old_connections, connections
from django.utils import timezone

import pytest

from apps.api.autenticacao.mfa import create_trusted_device
from apps.api.autenticacao.models import AuthToken, TokenMetaData, TokenType
from apps.api.core.errors import APIError
from apps.organizacoes.models import Organizacao, Papel, Vinculo
from apps.usuarios.accounts import Contas
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db


def test_desativar_revoga_acesso_e_suspende_vinculos_sem_remove_los():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Organização", slug="desativacao")
    vinculo = Vinculo.objects.create(usuario=usuario, organizacao=organizacao, papel=Papel.MEMBRO)
    token, _ = AuthToken.objects.create(responsavel=usuario, type=TokenType.TOKEN)
    TokenMetaData.objects.create(token=token, reauthenticated_at=timezone.now())
    dispositivo = create_trusted_device(usuario, {"device_name": "Notebook"}).instance

    Contas.desativar(usuario)

    usuario.refresh_from_db()
    vinculo.refresh_from_db()
    token.refresh_from_db()
    dispositivo.refresh_from_db()
    assert usuario.is_active is False
    assert token.revoked_at is not None
    assert dispositivo.revoked_at is not None
    assert vinculo.is_active is False
    assert vinculo.is_deleted is False


def test_unico_proprietario_ativo_nao_pode_desativar_a_conta():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Organização", slug="ultimo-proprietario")
    vinculo = Vinculo.objects.create(usuario=usuario, organizacao=organizacao, papel=Papel.PROPRIETARIO)

    with pytest.raises(APIError) as excinfo:
        Contas.desativar(usuario)

    usuario.refresh_from_db()
    vinculo.refresh_from_db()
    assert excinfo.value.status_code == 409
    assert excinfo.value.code == "account.owner_transfer_required"
    assert usuario.is_active is True
    assert vinculo.is_active is True


@pytest.mark.django_db(transaction=True)
def test_desativacoes_concorrentes_preservam_um_proprietario_ativo():
    primeiro = criar_usuario(email="primeiro@example.com")
    segundo = criar_usuario(email="segundo@example.com")
    organizacao = Organizacao.objects.create(nome="Organização", slug="proprietarios-concorrentes")
    Vinculo.objects.create(usuario=primeiro, organizacao=organizacao, papel=Papel.PROPRIETARIO)
    Vinculo.objects.create(usuario=segundo, organizacao=organizacao, papel=Papel.PROPRIETARIO)
    barreira = Barrier(2)

    def desativar(usuario_id):
        close_old_connections()
        try:
            barreira.wait(timeout=10)
            Contas.desativar(type(primeiro).objects.get(pk=usuario_id))
            return "desativada"
        except APIError as exc:
            return exc.code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        resultados = list(executor.map(desativar, (primeiro.pk, segundo.pk)))

    assert sorted(resultados) == ["account.owner_transfer_required", "desativada"]
    assert type(primeiro).objects.filter(pk__in=[primeiro.pk, segundo.pk], is_active=True).count() == 1
    assert Vinculo.objects.filter(organizacao=organizacao, papel=Papel.PROPRIETARIO, is_active=True).count() == 1
