import pytest

from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db


def test_criar_usuario_gera_email_unico_e_senha_com_hash():
    primeiro = criar_usuario(password="Senha123!")
    segundo = criar_usuario(password="Senha123!")

    assert primeiro.email != segundo.email
    assert primeiro.check_password("Senha123!")


def test_criar_usuario_aceita_campos_sobrescritos():
    usuario = criar_usuario(email="ada@example.com", first_name="Ada", is_active=False)

    assert usuario.email == "ada@example.com"
    assert usuario.first_name == "Ada"
    assert usuario.is_active is False
