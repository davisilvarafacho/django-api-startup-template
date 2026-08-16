from rest_framework.test import APIClient

import pytest
from cryptography.fernet import Fernet

from internal_frameworks.context import ContextVariable
from tests.support.usuarios import criar_usuario


@pytest.fixture(autouse=True)
def _isolar_contexto():
    """Impede que valores contextuais vazem entre testes."""
    ContextVariable.clear_context()
    yield
    ContextVariable.clear_context()


@pytest.fixture
def usuario(db):
    return criar_usuario(password="Senha123!")


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture(autouse=True)
def sensitive_field_key(monkeypatch):
    monkeypatch.setenv("SENSITIVE_FIELD_KEYS", Fernet.generate_key().decode())
