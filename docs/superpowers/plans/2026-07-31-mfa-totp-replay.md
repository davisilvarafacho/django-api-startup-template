# MFA TOTP Replay Prevention Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Impedir o consumo repetido de um mesmo contador TOTP em todos os fluxos MFA.

**Architecture:** Um helper recebe o fator bloqueado e o código, procura o contador correspondente na janela de tolerância e o aceita somente se superar `totp_last_counter`. Os fluxos existentes chamam o helper dentro de suas transações.

**Tech Stack:** Django 5.2, PyOTP e pytest-django.

## Global Constraints

- A janela TOTP permanece em `-1`, `0`, `+1` períodos.
- Nenhum contador menor ou igual a `totp_last_counter` pode ser aceito.
- O contador é atualizado no mesmo lock/transação do fluxo consumidor.

---

### Task 1: Helper transacional TOTP

**Files:**
- Modify: `apps/api/autenticacao/mfa.py`
- Modify: `apps/api/autenticacao/tests/test_mfa_enrollment.py`
- Modify: `apps/api/autenticacao/tests/test_mfa_login.py`

**Interfaces:**
- Produces: `consume_totp(factor: MFAFactor, code: str, now: datetime | None = None) -> bool`.

- [ ] **Step 1: Escrever testes falhando**

```python
def test_totp_rejeita_reuso_do_mesmo_contador(usuario):
    now = timezone.now().replace(microsecond=0)
    factor = MFAFactor.objects.create(user=usuario, type=MFAFactorType.TOTP, secret=pyotp.random_base32())
    code = pyotp.TOTP(factor.secret).at(now)

    assert consume_totp(factor, code, now=now) is True
    assert consume_totp(factor, code, now=now) is False
```

Também cobrir um código do próximo período, que deve ser aceito e avançar o
contador.

- [ ] **Step 2: Confirmar RED**

Run: `uv run pytest apps/api/autenticacao/tests/test_mfa_enrollment.py -q`
Expected: FAIL porque `consume_totp` ainda não existe.

- [ ] **Step 3: Implementar o helper**

```python
def consume_totp(factor, code, now=None):
    now = now or timezone.now()
    totp = pyotp.TOTP(factor.secret, digits=factor.totp_digits, interval=factor.totp_period)
    for counter in range(totp.timecode(now) - 1, totp.timecode(now) + 2):
        if counter > (factor.totp_last_counter or -1) and secrets.compare_digest(totp.generate_otp(counter), code):
            factor.totp_last_counter = counter
            factor.last_used_at = now
            factor.save(update_fields=["totp_last_counter", "last_used_at"])
            return True
    return False
```

- [ ] **Step 4: Confirmar GREEN**

Run: `uv run pytest apps/api/autenticacao/tests/test_mfa_enrollment.py -q`
Expected: PASS.

### Task 2: Aplicar a todos os consumidores

**Files:**
- Modify: `apps/api/autenticacao/mfa.py`
- Modify: `apps/api/autenticacao/tests/test_mfa_login.py`
- Modify: `apps/api/autenticacao/tests/test_mfa_reauthentication.py`

- [ ] **Step 1: Escrever testes falhando de integração**

```python
def test_login_mfa_rejeita_codigo_totp_ja_consumido(api_client, usuario):
    enrollment = start_enrollment(usuario, MFAFactorType.TOTP)
    confirm_enrollment(usuario, MFAFactorType.TOTP, pyotp.TOTP(enrollment.plain_secret).now())
    first_login = api_client.post("/auth/login/", {"email": usuario.email, "password": "Senha123!"})
    api_client.credentials(HTTP_AUTHORIZATION=f"PreAuth {first_login.data['pre_auth_token']}")
    api_client.post("/auth/mfa/challenge/start/", {"type": "totp"})
    code = pyotp.TOTP(enrollment.plain_secret).now()
    assert api_client.post("/auth/mfa/challenge/verify/", {"type": "totp", "code": code}).status_code == 200
```

- [ ] **Step 2: Confirmar RED**

Run: `uv run pytest apps/api/autenticacao/tests/test_mfa_login.py apps/api/autenticacao/tests/test_mfa_reauthentication.py -q`
Expected: FAIL porque os fluxos chamam `pyotp.TOTP.verify()` diretamente.

- [ ] **Step 3: Substituir verificações diretas**

Usar `consume_totp()` em `confirm_enrollment`, `verify_login_challenge` e
`verify_reauthentication`; manter `select_for_update()` nos fatores antes da
chamada.

- [ ] **Step 4: Confirmar regressão**

Run: `uv run pytest apps/api/autenticacao/tests/test_mfa_enrollment.py apps/api/autenticacao/tests/test_mfa_login.py apps/api/autenticacao/tests/test_mfa_reauthentication.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/autenticacao/mfa.py apps/api/autenticacao/tests/test_mfa_enrollment.py apps/api/autenticacao/tests/test_mfa_login.py apps/api/autenticacao/tests/test_mfa_reauthentication.py
git commit -m "fix(auth): prevent totp replay"
```
