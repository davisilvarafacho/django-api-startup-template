from django.conf import settings
from django.core.exceptions import FieldDoesNotExist
from django.db import models

import pytest
from auditlog.registry import auditlog
from threadlocals.threadlocals import set_current_user

from apps.api.autenticacao.models import TokenMetaData
from apps.api.base.models import BaseGlobal
from apps.organizacoes.models import Convite, Organizacao, Time, Vinculo
from apps.usuarios.factories import UsuarioFactory
from apps.usuarios.models import Usuario


def test_registra_todos_os_modelos_concretos_dos_apps():
    modelos_esperados = {Usuario, Organizacao, Time, Vinculo, Convite, TokenMetaData}
    modelos_internos_registrados = {model for model in auditlog.get_models() if model.__module__.startswith("apps.")}

    assert modelos_internos_registrados == modelos_esperados


def test_exclui_campos_tecnicos_e_credenciais_sem_mutar_a_configuracao_global():
    campos_base = settings.BASE_AUDITLOG_EXCLUDE_FIELDS

    assert campos_base == ["created_at", "last_modified_at"]
    assert set(auditlog.get_model_fields(Usuario)["exclude_fields"]) == {
        *campos_base,
        "is_deleted",
        "password",
        "last_login",
    }
    for model in (Organizacao, Time, Vinculo, Convite):
        assert auditlog.get_model_fields(model)["exclude_fields"] == [*campos_base, "is_deleted"]
    assert auditlog.get_model_fields(TokenMetaData)["exclude_fields"] == campos_base


def test_base_global_define_campos_de_auditoria():
    assert BaseGlobal._meta.get_field("created_by").remote_field.on_delete is models.PROTECT
    assert BaseGlobal._meta.get_field("created_at").auto_now_add is True
    assert BaseGlobal._meta.get_field("last_modified_at").auto_now is True

    for antigo in ("owner", "data_criacao", "hora_criacao", "data_ultima_alteracao", "hora_ultima_alteracao"):
        with pytest.raises(FieldDoesNotExist):
            BaseGlobal._meta.get_field(antigo)


def test_modelos_de_control_plane_mantem_created_by_padrao():
    for model in (Usuario, Organizacao, Vinculo, Convite):
        field = model._meta.get_field("created_by")
        assert field.remote_field.on_delete is models.PROTECT


def test_internal_e_read_only_fields_usam_os_novos_nomes():
    assert BaseGlobal.get_internal_fields() == ["last_modified_at", "is_deleted"]
    assert BaseGlobal.get_read_only_fields() == ["is_active", "is_deleted", "created_at", "created_by"]


@pytest.fixture(autouse=True)
def _limpar_usuario_atual():
    yield
    set_current_user(None)


@pytest.mark.django_db
def test_created_by_e_preenchido_automaticamente_pelo_usuario_atual():
    autor = UsuarioFactory()
    set_current_user(autor)
    organizacao = Organizacao.objects.create(nome="Org", slug="org-audit-autor")
    time = Time.objects.create(organizacao=organizacao, nome="Produto")

    assert organizacao.created_by == autor
    assert time.created_by == autor


@pytest.mark.django_db
def test_created_by_fica_none_quando_criado_pelo_sistema():
    set_current_user(None)
    organizacao = Organizacao.objects.create(nome="Org", slug="org-audit-sistema")
    time = Time.objects.create(organizacao=organizacao, nome="Produto")

    assert time.created_by is None


@pytest.mark.django_db
def test_clonar_atribui_created_by_do_usuario_atual_e_reseta_timestamps():
    criador = UsuarioFactory()
    clonador = UsuarioFactory()
    organizacao = Organizacao.objects.create(nome="Org", slug="org-audit-clone")

    set_current_user(criador)
    time = Time.objects.create(organizacao=organizacao, nome="Produto")

    set_current_user(clonador)
    clone = time.clonar(nome="Produto (cópia)")

    assert clone.pk != time.pk
    assert clone.created_by == clonador
