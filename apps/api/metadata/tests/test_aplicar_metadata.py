"""Persistência do metadata genérico pelo service layer."""

from rest_framework.serializers import ValidationError

import pytest

from apps.api.metadata.handlers import aplicar_metadata
from apps.api.metadata.models import Metadata
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Organizacao
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db


@pytest.fixture
def organizacao():
    return Organizacao.objects.create(nome="Acme", slug="acme")


def test_cria_o_registro_na_primeira_escrita(organizacao):
    usuario = criar_usuario()

    with organizacao_atual_privilegiada(organizacao.pk):
        registro = aplicar_metadata(usuario, {"erp_id": "X-1"})

        assert registro.dados == {"erp_id": "X-1"}
        assert registro.organizacao_id == organizacao.pk
        assert Metadata.objects.count() == 1


def test_reaproveita_o_registro_existente(organizacao):
    usuario = criar_usuario()

    with organizacao_atual_privilegiada(organizacao.pk):
        aplicar_metadata(usuario, {"erp_id": "X-1"})
        registro = aplicar_metadata(usuario, {"nota": "urgente"})

        assert registro.dados == {"erp_id": "X-1", "nota": "urgente"}
        assert Metadata.objects.count() == 1


def test_valor_nulo_remove_a_chave(organizacao):
    usuario = criar_usuario()

    with organizacao_atual_privilegiada(organizacao.pk):
        aplicar_metadata(usuario, {"erp_id": "X-1", "nota": "urgente"})
        registro = aplicar_metadata(usuario, {"nota": None})

        assert registro.dados == {"erp_id": "X-1"}


def test_nao_cria_registro_quando_a_validacao_falha(organizacao):
    usuario = criar_usuario()

    with organizacao_atual_privilegiada(organizacao.pk):
        with pytest.raises(ValidationError):
            aplicar_metadata(usuario, {"": "sem chave"})

        assert Metadata.all_objects.count() == 0
