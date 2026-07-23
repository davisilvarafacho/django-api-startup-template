from django.conf import settings

from auditlog.registry import auditlog

from apps.api.autenticacao.models import TokenMetaData
from apps.organizacoes.models import Convite, Organizacao, Time, Vinculo
from apps.usuarios.models import Usuario


def test_registra_todos_os_modelos_concretos_dos_apps():
    modelos_esperados = {Usuario, Organizacao, Time, Vinculo, Convite, TokenMetaData}
    modelos_internos_registrados = {
        model for model in auditlog.get_models() if model.__module__.startswith("apps.")
    }

    assert modelos_internos_registrados == modelos_esperados


def test_exclui_campos_tecnicos_e_credenciais_sem_mutar_a_configuracao_global():
    campos_base = settings.BASE_AUDITLOG_EXCLUDE_FIELDS

    assert campos_base == [
        "data_ultima_alteracao",
        "hora_ultima_alteracao",
        "data_criacao",
        "hora_criacao",
    ]
    assert set(auditlog.get_model_fields(Usuario)["exclude_fields"]) == {
        *campos_base,
        "password",
        "last_login",
    }
    for model in (Organizacao, Time, Vinculo, Convite, TokenMetaData):
        assert auditlog.get_model_fields(model)["exclude_fields"] == campos_base
