from rest_framework.test import APIClient

import pytest
from cryptography.fernet import Fernet
from threadlocals.threadlocals import set_current_user, set_thread_variable

from apps.usuarios.factories import UsuarioFactory


@pytest.fixture(autouse=True)
def _isolar_usuario_da_thread():
    """Impede que o usuário de um teste vaze para o seguinte.

    `Base.save()` preenche `created_by` com `get_current_user()`, que vive num
    threadlocal populado pelo middleware de autenticação. Sem limpar entre
    testes, um teste que fez request autenticada deixa o usuário lá; o rollback
    apaga a linha e o teste seguinte grava um `created_by` órfão, estourando a
    FK na hora do commit/teardown.
    """
    set_current_user(None)
    set_thread_variable("request", None)
    yield
    set_current_user(None)
    set_thread_variable("request", None)


@pytest.fixture
def usuario(db):
    return UsuarioFactory(password="Senha123!")


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture(autouse=True)
def sensitive_field_key(monkeypatch):
    monkeypatch.setenv("SENSITIVE_FIELD_KEYS", Fernet.generate_key().decode())
