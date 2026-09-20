from django.contrib.auth.models import Permission

import pytest

from apps.api.autenticacao.permissions import IsSuperUser
from apps.api.autenticacao.scope_delegation import validate_scope_delegation
from apps.api.core.errors import APIError
from apps.api.core.scope_registry import ScopeRegistry
from apps.organizacoes.models import Convite, Time
from apps.usuarios.models import Usuario
from tests.support.usuarios import criar_usuario


@pytest.fixture
def registro_isolado(monkeypatch):
    registry = ScopeRegistry()
    registry.register("teams", model=Time)
    registry.register("users", model=Usuario)
    registry.register(
        "invitations",
        model=Convite,
        custom_actions={"accept": "can_accept_convite"},
    )
    monkeypatch.setattr("apps.api.core.scope_registry.scope_registry", registry)
    return registry


def _com_permissao(usuario, app_label, codename, capturar_on_commit=None):
    permission = Permission.objects.get(content_type__app_label=app_label, codename=codename)
    if capturar_on_commit is None:
        usuario.user_permissions.add(permission)
    else:
        # O cache de autorização só invalida no commit, que nunca acontece dentro
        # da transação do teste. Sem executar os callbacks pendentes, uma checagem
        # negativa anterior deixa o snapshot vazio grudado e a permission recém
        # concedida não é enxergada.
        with capturar_on_commit(execute=True):
            usuario.user_permissions.add(permission)
    # `has_perm` cacheia por instância; recarrega para refletir a nova permission.
    return Usuario.objects.get(pk=usuario.pk)


@pytest.mark.django_db
def test_usuario_nao_delega_scope_sem_permission(registro_isolado):
    usuario = criar_usuario()

    with pytest.raises(APIError) as exc:
        validate_scope_delegation(usuario, ["users:delete"])

    assert exc.value.code == "auth.scope_not_delegable"


@pytest.mark.django_db
def test_usuario_delega_scope_que_possui(registro_isolado):
    usuario = criar_usuario()
    usuario = _com_permissao(usuario, "organizacoes", "view_time")

    assert validate_scope_delegation(usuario, ["teams:read"]) == ("teams:read",)


@pytest.mark.django_db
def test_usuario_delega_action_customizada_que_possui(registro_isolado):
    usuario = criar_usuario()
    usuario = _com_permissao(usuario, "organizacoes", "can_accept_convite")

    assert validate_scope_delegation(usuario, ["invitations:accept"]) == ("invitations:accept",)


@pytest.mark.django_db
def test_resource_wildcard_exige_todas_as_permissions(registro_isolado, django_capture_on_commit_callbacks):
    usuario = criar_usuario()
    usuario = _com_permissao(usuario, "organizacoes", "view_time")

    with pytest.raises(APIError):
        validate_scope_delegation(usuario, ["teams:*"])

    for codename in ("add_time", "change_time", "delete_time"):
        usuario = _com_permissao(usuario, "organizacoes", codename, django_capture_on_commit_callbacks)

    assert validate_scope_delegation(usuario, ["teams:*"]) == ("teams:*",)


@pytest.mark.django_db
def test_global_wildcard_exige_permission_especial_ou_superuser(registro_isolado, django_capture_on_commit_callbacks):
    usuario = criar_usuario()

    with pytest.raises(APIError) as exc:
        validate_scope_delegation(usuario, ["*"])
    assert exc.value.code == "auth.scope_not_delegable"

    usuario = _com_permissao(usuario, "autenticacao", "grant_unrestricted_apikey", django_capture_on_commit_callbacks)

    assert validate_scope_delegation(usuario, ["*"]) == ("*",)


@pytest.mark.django_db
@pytest.mark.parametrize("is_superuser", [False, True])
def test_wildcard_de_recurso_ambiguo_nao_e_delegavel_nem_por_superuser(registro_isolado, is_superuser):
    usuario = criar_usuario(is_superuser=is_superuser, is_staff=is_superuser)

    with pytest.raises(APIError) as exc:
        validate_scope_delegation(usuario, ["*:delete"])

    assert exc.value.code == "auth.invalid_scope"
    assert exc.value.status_code == 422
    assert exc.value.field == "scopes"
    assert exc.value.path == ("scopes", 0)


@pytest.mark.django_db
@pytest.mark.parametrize("is_superuser", [False, True])
def test_wildcard_global_qualificado_nao_e_delegavel_nem_por_superuser(registro_isolado, is_superuser):
    usuario = criar_usuario(is_superuser=is_superuser, is_staff=is_superuser)

    with pytest.raises(APIError) as exc:
        validate_scope_delegation(usuario, ["*:*"])

    assert exc.value.code == "auth.invalid_scope"
    assert exc.value.status_code == 422
    assert exc.value.field == "scopes"
    assert exc.value.path == ("scopes", 0)


@pytest.mark.django_db
def test_superuser_sempre_pode_delegar_qualquer_scope(registro_isolado):
    usuario = criar_usuario(is_superuser=True, is_staff=True)

    assert validate_scope_delegation(usuario, ["*", "teams:delete", "users:read"]) == (
        "*",
        "teams:delete",
        "users:read",
    )


@pytest.mark.django_db
def test_superuser_excluido_nao_atende_permission_de_superuser():
    usuario = criar_usuario(is_superuser=True, is_staff=True)
    usuario.is_deleted = True
    request = type("Request", (), {"user": usuario})()

    assert IsSuperUser().has_permission(request, view=None) is False


@pytest.mark.django_db
def test_superuser_excluido_nao_delega_scopes(registro_isolado):
    usuario = criar_usuario(is_superuser=True, is_staff=True)
    usuario.is_deleted = True

    with pytest.raises(APIError) as exc:
        validate_scope_delegation(usuario, ["*"])

    assert exc.value.code == "auth.scope_not_delegable"


@pytest.mark.django_db
def test_recurso_desconhecido_nao_e_delegavel(registro_isolado):
    usuario = criar_usuario(is_superuser=True, is_staff=True)

    with pytest.raises(APIError) as exc:
        validate_scope_delegation(usuario, ["desconhecido:read"])

    assert exc.value.code == "auth.scope_not_delegable"
