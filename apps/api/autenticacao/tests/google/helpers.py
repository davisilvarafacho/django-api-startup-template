"""Construtores que espelham a fronteira real do Google Identity Services."""

from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import patch

from django.utils import timezone

from rest_framework.test import APIClient

from apps.api.autenticacao.models import TokenType
from apps.api.autenticacao.services import issue_token

CLIENT_ID = "web-client.apps.googleusercontent.com"
HD_AUSENTE = object()


def claims_google(
    *,
    sub="google-sub-123",
    email="pessoa@gmail.com",
    email_verified=True,
    audience=CLIENT_ID,
    hd=HD_AUSENTE,
    **extras,
):
    agora = timezone.now()
    claims = {
        "iss": "https://accounts.google.com",
        "azp": audience,
        "aud": audience,
        "sub": sub,
        "email": email,
        "email_verified": email_verified,
        "at_hash": "hash-do-access-token",
        "name": "Ada Lovelace",
        "picture": "https://lh3.googleusercontent.com/avatar",
        "given_name": "Ada",
        "family_name": "Lovelace",
        "iat": int(agora.timestamp()),
        "exp": int((agora + timedelta(minutes=10)).timestamp()),
        "jti": "google-token-id-123",
    }
    if hd is not HD_AUSENTE:
        claims["hd"] = hd
    claims.update(extras)
    return claims


@contextmanager
def substituir_verificador(*, claims=None, side_effect=None):
    """Substitui somente a chamada de rede/criptografia mantida por google-auth."""
    with patch(
        "google.oauth2.id_token.verify_oauth2_token",
        return_value=claims,
        side_effect=side_effect,
    ):
        yield


def client_com_sessao(usuario, *, recente=True):
    issued = issue_token(
        responsavel=usuario,
        token_type=TokenType.TOKEN,
        created_by=usuario,
        expiry=timedelta(hours=1),
        metadata_input={},
    )
    if recente:
        issued.instance.metadata.reauthenticated_at = timezone.now()
        issued.instance.metadata.save(update_fields=["reauthenticated_at"])

    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {issued.plain_token}")
    return client
