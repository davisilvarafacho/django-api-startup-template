# Trusted Device Revocation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Revogar trusted devices em mudanças de contato e de fatores MFA.

**Architecture:** `Usuario.save()` identifica mudanças persistidas de e-mail/telefone e agenda a revogação após commit. O service de confirmação MFA chama a mesma operação na transação.

### Task 1: Revogação por contato

- [ ] Escrever teste que cria trusted device, altera `phone_number`, salva e verifica `revoked_at`.
- [ ] Confirmar RED com `uv run pytest apps/api/autenticacao/tests/test_trusted_devices.py -q`.
- [ ] Em `Usuario.save()`, consultar os valores persistidos quando `pk` existe; se `email` ou `phone_number` mudou, usar `transaction.on_commit()` para chamar `revoke_trusted_devices(self)`.
- [ ] Confirmar GREEN no mesmo teste.

### Task 2: Revogação por novo fator

- [ ] Escrever teste que confirma um segundo fator e verifica trusted device revogado.
- [ ] Confirmar RED.
- [ ] Chamar `revoke_trusted_devices(user)` em `confirm_enrollment()` depois de ativar o fator.
- [ ] Rodar `uv run pytest apps/api/autenticacao/tests/test_trusted_devices.py apps/api/autenticacao/tests/test_mfa_enrollment.py -q`.
- [ ] Commit: `git commit -m "fix(auth): revoke trusted devices on security changes"`.
