"""Contratos persistidos das identidades externas."""

from django.db import IntegrityError, transaction

import pytest
from auditlog.registry import auditlog

from apps.api.autenticacao import models as autenticacao_models
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db


def _obter_modelos_de_identidade():
    assert hasattr(autenticacao_models, "IdentidadeExterna")
    assert hasattr(autenticacao_models, "ProvedorIdentidade")
    return autenticacao_models.IdentidadeExterna, autenticacao_models.ProvedorIdentidade


def test_google_e_o_provedor_inicial_de_identidade():
    _, ProvedorIdentidade = _obter_modelos_de_identidade()

    assert ProvedorIdentidade.GOOGLE == 10


def test_identificador_de_provedor_e_unico_entre_identidades_vivas():
    IdentidadeExterna, ProvedorIdentidade = _obter_modelos_de_identidade()
    primeira_pessoa = criar_usuario()
    segunda_pessoa = criar_usuario()
    identidade = IdentidadeExterna.objects.create(
        usuario=primeira_pessoa,
        provedor=ProvedorIdentidade.GOOGLE,
        identificador="sub-google-imutavel",
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        IdentidadeExterna.objects.create(
            usuario=segunda_pessoa,
            provedor=ProvedorIdentidade.GOOGLE,
            identificador="sub-google-imutavel",
        )

    identidade.delete()
    recriada = IdentidadeExterna.objects.create(
        usuario=segunda_pessoa,
        provedor=ProvedorIdentidade.GOOGLE,
        identificador="sub-google-imutavel",
    )

    assert recriada.is_deleted is False


def test_usuario_tem_no_maximo_uma_identidade_viva_por_provedor():
    IdentidadeExterna, ProvedorIdentidade = _obter_modelos_de_identidade()
    usuario = criar_usuario()
    identidade = IdentidadeExterna.objects.create(
        usuario=usuario,
        provedor=ProvedorIdentidade.GOOGLE,
        identificador="primeiro-sub-google",
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        IdentidadeExterna.objects.create(
            usuario=usuario,
            provedor=ProvedorIdentidade.GOOGLE,
            identificador="segundo-sub-google",
        )

    identidade.delete()
    recriada = IdentidadeExterna.objects.create(
        usuario=usuario,
        provedor=ProvedorIdentidade.GOOGLE,
        identificador="segundo-sub-google",
    )

    assert recriada.is_deleted is False


def test_identificador_nao_e_serializado_pelo_auditlog():
    IdentidadeExterna, _ = _obter_modelos_de_identidade()

    assert "identificador" in auditlog.get_model_fields(IdentidadeExterna)["exclude_fields"]
