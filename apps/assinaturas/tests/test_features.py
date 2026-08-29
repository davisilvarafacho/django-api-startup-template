from django.core.checks import Error

import pytest

from apps.assinaturas import checks
from apps.assinaturas.features import (
    CATALOGO_RECURSOS,
    PAPEIS_ISENTOS_SEAT,
    QUANTIDADE_PROJETOS,
    CatalogoRecursos,
    RecursoPlano,
    ValoresRecursos,
)
from apps.organizacoes.models import Papel


def test_catalogo_rejeita_chaves_duplicadas():
    recurso = RecursoPlano(
        chave="limite",
        titulo="Limite",
        descricao="Limite de exemplo.",
        tipo=int,
        padrao=1,
        unidade="itens",
        exemplo=2,
    )

    with pytest.raises(ValueError, match="chave duplicada.*limite"):
        CatalogoRecursos((recurso, recurso))


@pytest.mark.parametrize("valor", [True, "3", 3.5, None])
def test_valores_recursos_rejeitam_tipo_invalido_sem_coercao(valor):
    with pytest.raises(ValueError, match="quantidade_projetos.*inteiro"):
        ValoresRecursos(CATALOGO_RECURSOS, {"quantidade_projetos": valor})


def test_lookup_tipado_usa_valor_persistido_e_default_em_snapshot_antigo():
    snapshot_atual = ValoresRecursos(
        CATALOGO_RECURSOS,
        {
            "quantidade_projetos": 7,
            "papeis_isentos_seat": [Papel.GESTOR],
        },
    )
    snapshot_antigo = ValoresRecursos(CATALOGO_RECURSOS, {"quantidade_projetos": 2})

    assert snapshot_atual.obter(QUANTIDADE_PROJETOS) == 7
    assert snapshot_atual.obter(PAPEIS_ISENTOS_SEAT) == frozenset({Papel.GESTOR})
    assert snapshot_antigo.obter(PAPEIS_ISENTOS_SEAT) == frozenset()


def test_materializacao_produz_snapshot_json_completo_e_deterministico():
    valores = ValoresRecursos(CATALOGO_RECURSOS, {"quantidade_projetos": 4})

    assert valores.materializar() == {
        "papeis_isentos_seat": [],
        "quantidade_projetos": 4,
    }


def test_catalogo_rejeita_chave_desconhecida_no_snapshot():
    with pytest.raises(ValueError, match="recurso desconhecido.*nao_existe"):
        ValoresRecursos(CATALOGO_RECURSOS, {"nao_existe": 1})


def test_catalogo_gera_json_schema_literal_deterministico():
    assert CATALOGO_RECURSOS.json_schema() == {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "papeis_isentos_seat": {
                "title": "Papéis isentos de seat",
                "description": "Papéis que não consomem seat no contrato.",
                "type": "array",
                "items": {
                    "type": "integer",
                    "enum": [10, 20, 30, 40, 50],
                },
                "default": [],
                "examples": [[20]],
            },
            "quantidade_projetos": {
                "title": "Quantidade de projetos",
                "description": "Quantidade máxima de projetos ativos.",
                "type": "integer",
                "minimum": 0,
                "default": 1,
                "examples": [10],
                "x-unit": "projetos",
            },
        },
    }


def test_catalogo_gera_exemplos_literais_deterministicos():
    assert CATALOGO_RECURSOS.exemplos() == {
        "papeis_isentos_seat": [20],
        "quantidade_projetos": 10,
    }


def test_system_check_reporta_default_com_tipo_invalido(monkeypatch):
    catalogo_invalido = CatalogoRecursos(
        (
            RecursoPlano(
                chave="limite_invalido",
                titulo="Limite inválido",
                descricao="Declaração deliberadamente inválida.",
                tipo=int,
                padrao="um",
                unidade=None,
                exemplo=2,
            ),
        )
    )
    monkeypatch.setattr("apps.assinaturas.features.CATALOGO_RECURSOS", catalogo_invalido)

    assert checks.recursos_planos_check(None) == [
        Error(
            "Recurso 'limite_invalido': o valor padrão deve ser inteiro.",
            id="assinaturas.E001",
        )
    ]
