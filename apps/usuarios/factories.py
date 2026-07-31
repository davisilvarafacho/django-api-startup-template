"""Factories de teste do app de usuários (factory_boy)."""

import factory
from factory.django import DjangoModelFactory

from apps.usuarios.models import Usuario


class UsuarioFactory(DjangoModelFactory):
    class Meta:
        model = Usuario

    first_name = factory.Faker("first_name", locale="pt_BR")
    last_name = factory.Faker("last_name", locale="pt_BR")
    email = factory.Sequence(lambda n: f"usuario{n}@exemplo.com")

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        # Usa o manager para garantir o hash da senha.
        password = kwargs.pop("password", "senha-de-teste")
        return model_class.objects.create_user(*args, password=password, **kwargs)
