from rest_framework.test import APIClient

import pytest

from apps.usuarios.factories import UsuarioFactory


@pytest.fixture
def usuario(db):
    return UsuarioFactory(password="Senha123!")


@pytest.fixture
def api_client():
    return APIClient()
