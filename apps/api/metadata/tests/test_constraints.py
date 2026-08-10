"""Unicidade do documento de metadata, por organização e por soft delete."""

import pytest

from apps.api.metadata.models import Metadata
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Organizacao
from apps.usuarios.models import Usuario
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db


def test_duas_organizacoes_anotam_o_mesmo_objeto():
    usuario = criar_usuario()
    acme = Organizacao.objects.create(nome="Acme", slug="acme")
    globex = Organizacao.objects.create(nome="Globex", slug="globex")
    content_type = Usuario.get_content_type()

    with organizacao_atual_privilegiada(acme.pk):
        Metadata.objects.create(content_type=content_type, object_id=usuario.pk, dados={"dono": "acme"})

    with organizacao_atual_privilegiada(globex.pk):
        Metadata.objects.create(content_type=content_type, object_id=usuario.pk, dados={"dono": "globex"})

        # A contagem precisa de contexto: `Metadata` está sob RLS e o
        # django-rls recusa query sem tenant, mesmo em `all_objects`.
        assert Metadata.all_objects.count() == 2


def test_documento_pode_ser_recriado_apos_exclusoes_sucessivas():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme")
    content_type = Usuario.get_content_type()

    with organizacao_atual_privilegiada(organizacao.pk):
        for _ in range(3):
            registro = Metadata.objects.create(content_type=content_type, object_id=usuario.pk, dados={})
            registro.delete()

        assert Metadata.objects.count() == 0
        assert Metadata.all_objects.count() == 3
