from datetime import timedelta

from django.db import IntegrityError, connection, transaction
from django.utils import timezone

import pytest
from cryptography.fernet import Fernet

from apps.api.autenticacao.models import (
    MFAChallenge,
    MFAChallengePurpose,
    MFAFactor,
    MFAFactorType,
    MFARecoveryCode,
    TrustedDevice,
)


@pytest.fixture(autouse=True)
def sensitive_field_key(monkeypatch):
    monkeypatch.setenv("SENSITIVE_FIELD_KEYS", Fernet.generate_key().decode())


@pytest.mark.django_db
def test_um_fator_por_tipo(usuario):
    MFAFactor.objects.create(user=usuario, type=MFAFactorType.EMAIL)

    with pytest.raises(IntegrityError), transaction.atomic():
        MFAFactor.objects.create(user=usuario, type=MFAFactorType.EMAIL)


@pytest.mark.django_db
def test_segredo_totp_e_telefone_nao_ficam_em_plaintext(usuario):
    usuario.phone_number = "+5511999999999"
    usuario.save(update_fields=["phone_number"])
    factor = MFAFactor.objects.create(
        user=usuario,
        type=MFAFactorType.TOTP,
        secret="JBSWY3DPEHPK3PXP",
    )

    with connection.cursor() as cursor:
        cursor.execute("SELECT phone_number FROM usuario WHERE id = %s", [usuario.pk])
        raw_phone = cursor.fetchone()[0]
        cursor.execute("SELECT secret FROM mfa_factor WHERE id = %s", [factor.pk])
        raw_secret = cursor.fetchone()[0]

    assert "+5511999999999" not in raw_phone
    assert "JBSWY3DPEHPK3PXP" not in raw_secret


@pytest.mark.django_db
def test_apenas_fator_totp_pode_armazenar_segredo(usuario):
    with pytest.raises(IntegrityError), transaction.atomic():
        MFAFactor.objects.create(user=usuario, type=MFAFactorType.EMAIL, secret="nao-permitido")

    with pytest.raises(IntegrityError), transaction.atomic():
        MFAFactor.objects.create(user=usuario, type=MFAFactorType.TOTP)


@pytest.mark.django_db
def test_desafio_identifica_expiracao_e_consumo(usuario):
    factor = MFAFactor.objects.create(user=usuario, type=MFAFactorType.EMAIL)
    expired = MFAChallenge.objects.create(
        user=usuario,
        factor=factor,
        purpose=MFAChallengePurpose.LOGIN,
        expires_at=timezone.now() - timedelta(seconds=1),
    )
    consumed = MFAChallenge.objects.create(
        user=usuario,
        factor=factor,
        purpose=MFAChallengePurpose.ENROLLMENT,
        expires_at=timezone.now() + timedelta(minutes=5),
        consumed_at=timezone.now(),
    )

    assert expired.is_expired is True
    assert expired.is_consumed is False
    assert consumed.is_expired is False
    assert consumed.is_consumed is True


@pytest.mark.django_db
def test_recovery_e_dispositivo_confiavel_exigem_digests(usuario):
    with pytest.raises(IntegrityError), transaction.atomic():
        MFARecoveryCode.objects.create(user=usuario, digest="")

    with pytest.raises(IntegrityError), transaction.atomic():
        TrustedDevice.objects.create(user=usuario, digest="")
