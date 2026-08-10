"""Contratos de exclusão lógica dos modelos de organizações."""

import pytest

from apps.organizacoes.models import Convite, Organizacao, Papel, Time, Vinculo
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db


def test_delete_marca_registro_sem_alterar_o_estado_ativo():
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme")
    organizacao.is_active = False
    organizacao.save(update_fields=["is_active"])

    organizacao.delete()

    assert not Organizacao.objects.filter(pk=organizacao.pk).exists()
    excluida = Organizacao.all_objects.get(pk=organizacao.pk)
    assert excluida.is_deleted is True
    assert excluida.is_active is False


def test_delete_do_queryset_marca_registros_em_lote():
    primeira = Organizacao.objects.create(nome="Acme", slug="acme")
    segunda = Organizacao.objects.create(nome="Beta", slug="beta")

    count, details = Organizacao.objects.filter(pk__in=[primeira.pk, segunda.pk]).delete()

    assert count == 2
    assert details == {"organizacoes.Organizacao": 2}
    assert Organizacao.objects.count() == 0
    assert Organizacao.all_objects.filter(is_deleted=True).count() == 2


def test_chaves_unicas_podem_ser_reutilizadas_apos_exclusao_logica():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme")
    time = Time.objects.create(organizacao=organizacao, nome="Produto")
    vinculo = Vinculo.objects.create(organizacao=organizacao, usuario=usuario, papel=Papel.MEMBRO)
    convite = Convite.objects.create(organizacao=organizacao, email="pessoa@example.com", token="token-reutilizavel")

    time.delete()
    vinculo.delete()
    convite.delete()

    Time.objects.create(organizacao=organizacao, nome="Produto")
    Vinculo.objects.create(organizacao=organizacao, usuario=usuario, papel=Papel.MEMBRO)
    Convite.objects.create(organizacao=organizacao, email="outra@example.com", token="token-reutilizavel")

    organizacao.delete()
    nova_organizacao = Organizacao.objects.create(nome="Nova Acme", slug="acme")
    assert nova_organizacao.slug == "acme"


def test_usuario_excluido_fica_fora_do_manager_padrao_e_email_pode_ser_reutilizado():
    usuario = criar_usuario(email="pessoa@example.com")

    usuario.delete()

    assert not usuario.__class__.objects.filter(pk=usuario.pk).exists()
    assert usuario.__class__.all_objects.get(pk=usuario.pk).is_deleted is True
    novo_usuario = criar_usuario(email="pessoa@example.com")
    assert novo_usuario.pk != usuario.pk
