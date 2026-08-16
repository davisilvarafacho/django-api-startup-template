from django.db import connection
from django.test.utils import CaptureQueriesContext

import pyotp
import pytest

from apps.api.autenticacao.mfa import (
    confirm_enrollment,
    create_trusted_device,
    remove_factor,
    start_enrollment,
    start_login_challenge,
    verify_login_challenge,
)
from apps.api.autenticacao.models import AuthToken, MFAFactor, MFAFactorType, TokenType
from apps.api.autenticacao.services import issue_token


def _credential_lock_order(queries):
    order = []
    for query in queries:
        sql = query["sql"]
        if 'FROM "usuario"' in sql and "FOR UPDATE" in sql:
            order.append("usuario")
        elif 'FROM "auth_token"' in sql and "FOR UPDATE" in sql:
            order.append("auth_token")
        elif sql.startswith('UPDATE "trusted_device"'):
            order.append("trusted_device")
        elif 'FROM "mfa_factor"' in sql and "FOR UPDATE" in sql:
            order.append("mfa_factor")
    return order


@pytest.mark.django_db
def test_confirm_enrollment_bloqueia_usuario_dispositivo_e_fator_nessa_ordem(usuario):
    trusted = create_trusted_device(usuario, {})
    enrollment = start_enrollment(usuario, MFAFactorType.TOTP)

    with CaptureQueriesContext(connection) as queries:
        confirm_enrollment(usuario, MFAFactorType.TOTP, pyotp.TOTP(enrollment.plain_secret).now())

    assert _credential_lock_order(queries)[:3] == ["usuario", "trusted_device", "mfa_factor"]
    trusted.instance.refresh_from_db()
    assert trusted.instance.revoked_at is not None


@pytest.mark.django_db
def test_remove_factor_bloqueia_usuario_dispositivo_e_fator_nessa_ordem(usuario):
    enrollment = start_enrollment(usuario, MFAFactorType.TOTP)
    confirm_enrollment(usuario, MFAFactorType.TOTP, pyotp.TOTP(enrollment.plain_secret).now())
    create_trusted_device(usuario, {})

    with CaptureQueriesContext(connection) as queries:
        remove_factor(usuario, MFAFactorType.TOTP)

    assert _credential_lock_order(queries)[:3] == ["usuario", "trusted_device", "mfa_factor"]


@pytest.mark.django_db
def test_verify_login_bloqueia_usuario_token_e_fator_nessa_ordem(usuario):
    enrollment = start_enrollment(usuario, MFAFactorType.TOTP)
    confirm_enrollment(usuario, MFAFactorType.TOTP, pyotp.TOTP(enrollment.plain_secret).now())
    MFAFactor.objects.filter(pk=enrollment.factor.pk).update(totp_last_counter=None)
    pre_auth = issue_token(
        responsavel=usuario,
        token_type=TokenType.PRE_AUTH,
        created_by=usuario,
        expiry=None,
        metadata_input={},
    )
    start_login_challenge(pre_auth.instance, MFAFactorType.TOTP)

    with CaptureQueriesContext(connection) as queries:
        verify_login_challenge(
            pre_auth.instance,
            pyotp.TOTP(enrollment.plain_secret).now(),
            MFAFactorType.TOTP,
            trust_device=False,
            metadata={},
        )

    assert _credential_lock_order(queries)[:3] == ["usuario", "auth_token", "mfa_factor"]
    assert AuthToken.objects.filter(responsavel=usuario, type=TokenType.TOKEN).exists()
