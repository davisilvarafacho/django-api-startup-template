# MFA OTP Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar OTP de e-mail/SMS de forma assíncrona, com cooldown e estados corretos.

**Architecture:** Services criam somente desafios pendentes e agendam uma task por ID após o commit. A task gera o OTP, persiste apenas HMAC, entrega pelo backend apropriado e atualiza o estado; a confirmação só aceita desafios enviados.

**Tech Stack:** Django 5.2, Celery, pytest-django, Django email backend e `MFA_SMS_BACKEND`.

## Global Constraints

- OTP tem seis dígitos e validade de cinco minutos.
- A task recebe apenas `MFAChallenge.pk`, nunca o OTP.
- Cooldown entre envios do mesmo fator/finalidade é exatamente 60 segundos.
- Logs, respostas e persistência não contêm OTP nem destino em texto puro além dos campos já protegidos.

---

### Task 1: Desafio e entrega assíncrona

**Files:**
- Modify: `apps/api/autenticacao/mfa.py`
- Modify: `apps/api/autenticacao/tasks.py`
- Modify: `apps/api/autenticacao/tests/test_mfa_enrollment.py`

**Interfaces:**
- Produces: `schedule_otp_delivery(challenge: MFAChallenge) -> None`.
- Produces: `deliver_mfa_otp(challenge_pk: int) -> None`.

- [x] **Step 1: Escrever testes falhando para task e estado de entrega**

```python
@pytest.mark.django_db
def test_task_entrega_otp_e_persiste_apenas_hmac(usuario, monkeypatch):
    factor = MFAFactor.objects.create(user=usuario, type=MFAFactorType.EMAIL)
    challenge = MFAChallenge.objects.create(
        user=usuario, factor=factor, purpose=MFAChallengePurpose.ENROLLMENT,
        expires_at=timezone.now() + timedelta(minutes=5),
    )
    monkeypatch.setattr("apps.api.autenticacao.tasks.send_mail", lambda *args, **kwargs: 1)

    deliver_mfa_otp(challenge.pk)

    challenge.refresh_from_db()
    assert challenge.delivery_status == MFAChallengeDeliveryStatus.SENT
    assert challenge.otp_digest
    assert challenge.delivered_at is not None
```

- [x] **Step 2: Confirmar RED**

Run: `uv run pytest apps/api/autenticacao/tests/test_mfa_enrollment.py -q`
Expected: FAIL porque o desafio não exige estado `sent` na confirmação nem a task trata falha de entrega como indisponível.

- [x] **Step 3: Implementar task e agendamento mínimo**

```python
def schedule_otp_delivery(challenge):
    from .tasks import deliver_mfa_otp
    transaction.on_commit(lambda: deliver_mfa_otp.delay(challenge.pk))

@shared_task(name="autenticacao.deliver_mfa_otp", ignore_result=True)
def deliver_mfa_otp(challenge_pk):
    challenge = MFAChallenge.objects.select_for_update().get(pk=challenge_pk)
    # gera OTP, armazena HMAC e muda pending para sent/failed
```

- [x] **Step 4: Confirmar GREEN**

Run: `uv run pytest apps/api/autenticacao/tests/test_mfa_enrollment.py -q`
Expected: PASS.

### Task 2: Cooldown e confirmação segura

**Files:**
- Modify: `apps/api/autenticacao/mfa.py`
- Modify: `apps/api/autenticacao/tests/test_mfa_enrollment.py`

**Interfaces:**
- Produces: `start_enrollment(user, factor_type) -> EnrollmentResult` que recusa reenvio dentro de 60 segundos.

- [x] **Step 1: Escrever testes falhando para cooldown e entrega falha**

```python
@pytest.mark.django_db
def test_enrollment_recusa_reenvio_antes_do_cooldown(usuario):
    start_enrollment(usuario, MFAFactorType.EMAIL)
    with pytest.raises(ValueError, match="cooldown"):
        start_enrollment(usuario, MFAFactorType.EMAIL)

@pytest.mark.django_db
def test_confirmacao_recusa_desafio_com_entrega_falha(usuario):
    factor = MFAFactor.objects.create(user=usuario, type=MFAFactorType.EMAIL)
    MFAChallenge.objects.create(
        user=usuario, factor=factor, purpose=MFAChallengePurpose.ENROLLMENT,
        otp_digest=_otp_digest("123456"), delivery_status="failed",
        expires_at=timezone.now() + timedelta(minutes=5),
    )
    with pytest.raises(ValueError):
        confirm_enrollment(usuario, MFAFactorType.EMAIL, "123456")
```

- [x] **Step 2: Confirmar RED**

Run: `uv run pytest apps/api/autenticacao/tests/test_mfa_enrollment.py -q`
Expected: FAIL porque ainda permite reenvio imediato e não restringe por estado de entrega.

- [x] **Step 3: Implementar bloqueio transacional**

```python
previous = MFAChallenge.objects.select_for_update().filter(
    user=user, factor=factor, purpose=MFAChallengePurpose.ENROLLMENT,
    consumed_at__isnull=True,
).order_by("-created_at").first()
if previous and previous.created_at > timezone.now() - timedelta(seconds=60):
    raise ValueError("OTP em cooldown.")
if previous:
    previous.consumed_at = timezone.now()
    previous.save(update_fields=["consumed_at"])
```

Exigir `delivery_status == MFAChallengeDeliveryStatus.SENT` ao confirmar fatores de e-mail/SMS.

- [x] **Step 4: Confirmar GREEN e regressão**

Run: `uv run pytest apps/api/autenticacao/tests/test_mfa_enrollment.py apps/api/autenticacao/tests/test_mfa_login.py apps/api/autenticacao/tests/test_mfa_reauthentication.py -q`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add apps/api/autenticacao/mfa.py apps/api/autenticacao/tasks.py apps/api/autenticacao/tests/test_mfa_enrollment.py
git commit -m "fix(auth): secure mfa otp delivery"
```
