"""Contrato de leitura do metadata genérico exposto pelo `MetadataMixin`."""

from django.contrib.contenttypes.models import ContentType

import pytest

from apps.api.metadata.models import Metadata
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Organizacao
from apps.usuarios.models import Usuario
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db


def test_get_content_type_devolve_o_content_type_do_model():
    assert Usuario.get_content_type() == ContentType.objects.get_for_model(Usuario)


def test_raw_metadata_devolve_dicionario_vazio_sem_registro():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme")

    with organizacao_atual_privilegiada(organizacao.pk):
        assert usuario.raw_metadata == {}


def test_raw_metadata_nao_cria_registro():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme")

    with organizacao_atual_privilegiada(organizacao.pk):
        assert usuario.raw_metadata == {}
        assert Metadata.all_objects.count() == 0


def test_raw_metadata_devolve_os_dados_persistidos():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme")

    with organizacao_atual_privilegiada(organizacao.pk):
        Metadata.objects.create(
            content_type=Usuario.get_content_type(),
            object_id=usuario.pk,
            dados={"erp_id": "X-1"},
        )

        assert usuario.raw_metadata == {"erp_id": "X-1"}
