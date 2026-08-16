from django.contrib.auth.models import Permission
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

import pytest

from apps.api.autenticacao.mfa import create_trusted_device, reset_user_mfa, start_enrollment
from apps.api.autenticacao.models import MFAFactorType, MFAResetAudit, TokenType
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
def test_admin_reset_respeita_ordem_global_usuario_token_dispositivo(usuario):
    target = criar_usuario()
    start_enrollment(target, MFAFactorType.TOTP)
    issue_token(responsavel=target, token_type=TokenType.TOKEN, created_by=target, expiry=None, metadata_input={})
    create_trusted_device(target, {})

    with CaptureQueriesContext(connection) as queries:
        reset_user_mfa(target=target, actor=usuario, reason="Incidente")

    statements = [query["sql"].lower() for query in queries]
    user_lock = next(index for index, sql in enumerate(statements) if 'from "usuario"' in sql and "for update" in sql)
    prior_row_locks = [sql for sql in statements[:user_lock] if "for update" in sql]
    token_write = next(
        index for index, sql in enumerate(statements) if index > user_lock and '"auth_token"' in sql and sql.startswith(("update", "delete"))
    )
    device_write = next(index for index, sql in enumerate(statements) if 'update "trusted_device"' in sql)

    assert prior_row_locks == []
    assert user_lock < token_write < device_write
