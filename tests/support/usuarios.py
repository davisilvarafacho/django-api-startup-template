"""Construtores de usuário exclusivos da infraestrutura de teste."""

from itertools import count
from typing import Any

from apps.usuarios.models import Usuario

_sequencia = count()


def criar_usuario(**campos: Any) -> Usuario:
    """Cria um usuário persistido sem acoplar outros testes à política de senha."""
    indice = next(_sequencia)
    password = campos.pop("password", "senha-de-teste")
    defaults = {
        "first_name": "Usuário",
        "last_name": str(indice),
        "email": f"usuario{indice}@exemplo.com",
    }
    defaults.update(campos)
    return Usuario.objects._create_user(password=password, validate=False, **defaults)
