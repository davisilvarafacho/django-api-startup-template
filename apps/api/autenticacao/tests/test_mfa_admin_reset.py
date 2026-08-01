from django.contrib.auth.models import Permission
from django.utils import timezone

import pytest

from apps.api.autenticacao.mfa import start_enrollment
from apps.api.autenticacao.models import MFAFactorType, MFAResetAudit, TokenType
from apps.api.autenticacao.services import issue_token
from apps.usuarios.factories import UsuarioFactory


@pytest.mark.django_db
def test_admin_reset_exige_permissao_reauth_e_justificativa(api_client, usuario):
    target = UsuarioFactory()
    start_enrollment(target, MFAFactorType.TOTP)
    issued = issue_token(responsavel=usuario, token_type=TokenType.TOKEN, created_by=usuario, expiry=None, metadata_input={})
    issued.instance.metadata.reauthenticated_at = timezone.now()
    issued.instance.metadata.save(update_fields=["reauthenticated_at"])
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {issued.plain_token}")

    assert api_client.post("/auth/mfa/admin-reset/", {"user_id": target.pk, "reason": ""}).status_code == 403
    usuario.user_permissions.add(Permission.objects.get(codename="can_reset_mfa_usuario"))
    assert api_client.post("/auth/mfa/admin-reset/", {"user_id": target.pk, "reason": ""}).status_code == 400
    assert api_client.post("/auth/mfa/admin-reset/", {"user_id": target.pk, "reason": "Suporte verificado"}).status_code == 204
    assert not target.mfa_factors.exists()
    assert MFAResetAudit.objects.filter(actor=usuario, target=target, reason="Suporte verificado").exists()
