from rest_framework.test import APIClient

import pytest
from cryptography.fernet import Fernet

from apps.usuarios.factories import UsuarioFactory


@pytest.fixture
def usuario(db):
    return UsuarioFactory(password="Senha123!")


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture(autouse=True)
def sensitive_field_key(monkeypatch):
    monkeypatch.setenv("SENSITIVE_FIELD_KEYS", Fernet.generate_key().decode())
