"""Serviços transacionais de emissão de credenciais (`AuthToken`).

Views não devem chamar `AuthToken.objects.create()`/`TokenMetaData.objects.create()`
diretamente: `issue_token()` é o único ponto de entrada, garantindo que token e
metadata nascem juntos ou não nascem.
"""
from dataclasses import dataclass

from django.db import transaction

from knox.models import get_token_model

from .models import TokenMetaData


@dataclass(frozen=True)
class IssuedToken:
    instance: object
    plain_token: str


def issue_token(
    *,
    responsavel,
    token_type,
    created_by,
    expiry,
    metadata_input,
    organization=None,
    name="",
    scopes=(),
):
    """Cria o `AuthToken` e seu `TokenMetaData` na mesma transação.

    Se a criação do metadata falhar, o token também não persiste.
    """
    auth_token_model = get_token_model()

    with transaction.atomic():
        instance, plain_token = auth_token_model.objects.create(
            responsavel=responsavel,
            type=token_type,
            created_by=created_by,
            expiry=expiry,
            organization=organization,
            name=name,
            scopes=list(scopes),
        )
        TokenMetaData.objects.create(token=instance, **metadata_input)

    return IssuedToken(instance=instance, plain_token=plain_token)
