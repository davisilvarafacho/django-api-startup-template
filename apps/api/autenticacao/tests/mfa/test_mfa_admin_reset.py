from unittest.mock import patch

from django.contrib.auth.models import Permission
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

import pytest

from apps.api.autenticacao.mfa import create_trusted_device, reset_user_mfa, start_enrollment
from apps.api.autenticacao.models import AuthToken, MFAFactorType, MFAResetAudit, TokenType, TrustedDevice
from apps.api.autenticacao.services import issue_token
from tests.support.usuarios import criar_usuario


@pytest.mark.django_db
def test_admin_reset_exige_permissao_reauth_e_justificativa(api_client, usuario, django_capture_on_commit_callbacks):
    target = criar_usuario()
    start_enrollment(target, MFAFactorType.TOTP)
    issued = issue_token(responsavel=usuario, token_type=TokenType.TOKEN, created_by=usuario, expiry=None, metadata_input={})
    issued.instance.metadata.reauthenticated_at = timezone.now()
    issued.instance.metadata.save(update_fields=["reauthenticated_at"])
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {issued.plain_token}")

    assert api_client.post("/auth/mfa/admin-reset/", {"user_id": target.pk, "reason": ""}).status_code == 403
    # O cache de autorização só invalida no commit, que nunca acontece dentro da
    # transação do teste. Sem executar os callbacks pendentes, o 403 anterior
    # deixa o snapshot vazio grudado e a permission recém concedida não é vista.
    with django_capture_on_commit_callbacks(execute=True):
        usuario.user_permissions.add(Permission.objects.get(codename="can_reset_mfa_usuario"))
    assert api_client.post("/auth/mfa/admin-reset/", {"user_id": target.pk, "reason": ""}).status_code == 400
    assert api_client.post("/auth/mfa/admin-reset/", {"user_id": target.pk, "reason": "Suporte verificado"}).status_code == 204
    assert not target.mfa_factors.exists()
    assert MFAResetAudit.objects.filter(actor=usuario, target=target, reason="Suporte verificado").exists()


@pytest.mark.django_db
def test_reset_user_mfa_bloqueia_usuario_antes_de_token_e_dispositivo(usuario):
    target = criar_usuario()
    AuthToken.objects.create(responsavel=target, type=TokenType.TOKEN)
    AuthToken.objects.create(responsavel=target, type=TokenType.PRE_AUTH)
    create_trusted_device(target, {})

    with CaptureQueriesContext(connection) as queries:
        reset_user_mfa(target=target, actor=usuario, reason="Suporte verificado")

    user_lock = next(index for index, query in enumerate(queries) if 'FROM "usuario"' in query["sql"] and "FOR UPDATE" in query["sql"])
    token_write = next(index for index, query in enumerate(queries) if query["sql"].startswith(('UPDATE "auth_token"', 'DELETE FROM "auth_token"')))
    device_write = next(index for index, query in enumerate(queries) if query["sql"].startswith('UPDATE "trusted_device"'))
    assert user_lock < token_write < device_write


@pytest.mark.django_db
def test_reset_user_mfa_propaga_alias_do_usuario(usuario):
    usuario._state.db = "mfa_alias"

    with (
        patch("apps.api.autenticacao.mfa.transaction.atomic") as atomic,
        patch("apps.api.autenticacao.mfa.lock_eligible_responsible", return_value=usuario),
        patch.object(AuthToken.objects, "using") as auth_tokens_using,
        patch.object(TrustedDevice.objects, "using") as trusted_devices_using,
        patch("apps.api.autenticacao.mfa.MFAFactor.objects.using") as factors_using,
        patch("apps.api.autenticacao.mfa.MFARecoveryCode.objects.using") as recovery_codes_using,
        patch("apps.api.autenticacao.mfa.MFAResetAudit.objects.using") as reset_audit_using,
    ):
        reset_user_mfa(target=usuario, actor=usuario, reason="Suporte verificado")

    atomic.assert_called_once_with(using="mfa_alias")
    assert auth_tokens_using.call_count == 2
    trusted_devices_using.assert_called_once_with("mfa_alias")
    factors_using.assert_called_once_with("mfa_alias")
    recovery_codes_using.assert_called_once_with("mfa_alias")
    reset_audit_using.assert_called_once_with("mfa_alias")
