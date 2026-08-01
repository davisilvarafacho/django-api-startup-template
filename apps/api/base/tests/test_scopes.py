"""Testes da resolução automática de scopes (`resource:action`) em ViewSets."""

from rest_framework.permissions import AllowAny
from rest_framework.viewsets import GenericViewSet

import pytest

from apps.api.autenticacao.models import TokenMetaData
from apps.api.autenticacao.permissions import require_token_scopes
from apps.api.base.views import UtilsViewSetMixin
from apps.usuarios.models import Usuario


class _RecursoViewSet(UtilsViewSetMixin, GenericViewSet):
    queryset = Usuario.objects.all()
    scope_resource = "users"
    permission_classes = [AllowAny]

    @require_token_scopes("users:reset_password")
    def reset_password(self, request, *args, **kwargs):
        raise NotImplementedError


class _RecursoDoModelViewSet(UtilsViewSetMixin, GenericViewSet):
    queryset = Usuario.objects.all()
    permission_classes = [AllowAny]


class _SemRecursoViewSet(UtilsViewSetMixin, GenericViewSet):
    # `TokenMetaData` não herda de `BaseGlobal`: não tem `api_scope_resource`.
    queryset = TokenMetaData.objects.all()
    permission_classes = [AllowAny]


@pytest.mark.parametrize(
    ("action", "scope"),
    [
        ("list", "users:read"),
        ("retrieve", "users:read"),
        ("create", "users:create"),
        ("update", "users:update"),
        ("partial_update", "users:update"),
        ("destroy", "users:delete"),
    ],
)
def test_viewset_deriva_scope_crud(action, scope):
    view = _RecursoViewSet()
    view.action = action

    assert view.get_required_token_scopes() == [scope]


def test_override_scope_resource_tem_prioridade_sobre_o_model():
    view = _RecursoViewSet()

    assert view.get_scope_resource() == "users"


def test_scope_resource_cai_para_o_default_do_model():
    view = _RecursoDoModelViewSet()

    assert view.get_scope_resource() == "users"


def test_model_sem_recurso_nao_exige_scope():
    view = _SemRecursoViewSet()
    view.action = "list"

    assert view.get_scope_resource() is None
    assert view.get_required_token_scopes() == []


def test_action_customizada_usa_o_decorator():
    view = _RecursoViewSet()
    view.action = "reset_password"

    assert view.get_required_token_scopes() == ["users:reset_password"]


def test_action_sem_mapeamento_crud_e_sem_decorator_nao_exige_scope():
    view = _RecursoViewSet()
    view.action = "algo_sem_decorator"

    assert view.get_required_token_scopes() == []
