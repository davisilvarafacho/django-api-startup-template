"""Testes do ponto único de definição de senha.

Toda senha que entra no sistema passa por `apps.usuarios.passwords`; a única
exceção deliberada é `create_superuser()`, que precisa funcionar em bootstrap e
CI sem depender de rede nem de política de senha.
"""

from django.core.exceptions import ValidationError

import pytest

from apps.usuarios.models import Usuario
from apps.usuarios.passwords import set_validated_password


@pytest.mark.django_db
def test_create_user_executa_validadores(monkeypatch):
    chamados = []
    monkeypatch.setattr(
        "apps.usuarios.passwords.validate_password",
        lambda password, user: chamados.append(user),
    )

    user = Usuario.objects.create_user(email="u@example.com", password="Senha123!")

    assert chamados == [user]


@pytest.mark.django_db
def test_create_user_recusa_senha_fraca():
    with pytest.raises(ValidationError):
        Usuario.objects.create_user(email="fraca@example.com", password="123")

    assert not Usuario.objects.filter(email="fraca@example.com").exists()


@pytest.mark.django_db
def test_create_superuser_ignora_validadores(monkeypatch):
    monkeypatch.setattr(
        "apps.usuarios.passwords.validate_password",
        lambda password, user: pytest.fail("não deve validar"),
    )

    user = Usuario.objects.create_superuser(
        email="root@example.com",
        password="qualquer",
        first_name="Root",
        last_name="User",
    )

    assert user.check_password("qualquer")
    assert user.is_superuser is True


@pytest.mark.django_db
def test_create_user_guarda_hash_e_nao_texto_puro():
    user = Usuario.objects.create_user(email="hash@example.com", password="Senha123!")

    assert user.password != "Senha123!"
    assert user.check_password("Senha123!")


@pytest.mark.django_db
def test_create_user_sem_senha_fica_inutilizavel():
    """Conta sem senha (ex.: criada por convite) não pode virar senha vazia."""
    user = Usuario.objects.create_user(email="sem-senha@example.com")

    assert user.has_usable_password() is False


@pytest.mark.django_db
def test_set_validated_password_valida_e_persiste(usuario):
    set_validated_password(usuario, "OutraSenha456!")

    usuario.refresh_from_db()
    assert usuario.check_password("OutraSenha456!")


@pytest.mark.django_db
def test_set_validated_password_rejeita_sem_persistir(usuario):
    senha_anterior = usuario.password

    with pytest.raises(ValidationError):
        set_validated_password(usuario, "123")

    usuario.refresh_from_db()
    assert usuario.password == senha_anterior


@pytest.mark.django_db
def test_set_validated_password_sem_save_nao_toca_no_banco(usuario):
    senha_anterior = usuario.password

    set_validated_password(usuario, "OutraSenha456!", save=False)

    assert usuario.check_password("OutraSenha456!")
    usuario.refresh_from_db()
    assert usuario.password == senha_anterior


@pytest.mark.django_db
def test_set_validated_password_recebe_o_usuario_no_validator(monkeypatch, usuario):
    """O validator precisa do usuário para comparar senha com e-mail/nome."""
    chamados = []
    monkeypatch.setattr(
        "apps.usuarios.passwords.validate_password",
        lambda password, user: chamados.append((password, user)),
    )

    set_validated_password(usuario, "OutraSenha456!")

    assert chamados == [("OutraSenha456!", usuario)]
