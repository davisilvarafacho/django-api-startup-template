"""Ponto único de definição de senha.

Qualquer caminho que grave uma senha — criação de usuário, alteração
autenticada, redefinição por token — passa por aqui, para que a política de
senha (incluindo a checagem de vazamento) não dependa de cada chamador
lembrar de aplicá-la.

A única exceção deliberada é `create_superuser()`: o bootstrap de um ambiente
não pode depender de uma API externa nem ser barrado pela política, e o
`manage.py createsuperuser` já valida a senha no próprio fluxo interativo.
"""

from django.contrib.auth.hashers import make_password
from django.contrib.auth.password_validation import validate_password


def build_password(user, raw_password, *, validate=True):
    """Devolve o hash a ser gravado em `user.password`.

    Usado na criação, quando o usuário ainda não existe no banco e portanto não
    pode ser salvo campo a campo.

    Args:
        user: Instância (ainda não persistida) usada pelos validators que
            comparam a senha com e-mail e nome.
        raw_password: Senha em texto puro. `None` gera uma senha inutilizável.
        validate: Quando `False`, pula os validators. Reservado ao superusuário.

    Returns:
        O hash da senha.

    Raises:
        django.core.exceptions.ValidationError: Se a senha violar a política.
    """
    if raw_password is None:
        return make_password(None)
    if validate:
        validate_password(raw_password, user)
    return make_password(raw_password)


def set_validated_password(user, raw_password, *, save=True):
    """Valida e aplica a senha a um usuário já existente.

    Persiste apenas a coluna `password`: um `save()` completo sobrescreveria
    campos que outra transação possa ter alterado enquanto o formulário estava
    aberto.

    Args:
        user: Usuário persistido.
        raw_password: Nova senha em texto puro.
        save: Quando `False`, apenas aplica o hash na instância em memória.

    Returns:
        O próprio usuário, com a nova senha aplicada.

    Raises:
        django.core.exceptions.ValidationError: Se a senha violar a política.
    """
    validate_password(raw_password, user)
    user.set_password(raw_password)
    if save:
        user.save(update_fields=["password"])
    return user
