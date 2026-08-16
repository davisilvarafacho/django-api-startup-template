# MFA Final Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Completar a entrega assíncrona em todos os fluxos, auditar resets MFA e cobrir os limites de segurança.

**Architecture:** Centralizar a criação de desafios OTP em um service que agenda `deliver_mfa_otp` após commit. Persistir uma entrada auditável e não sensível para reset administrativo. Usar testes transacionais para expiração, cinco tentativas e consumo concorrente.

## Task 1 — OTP assíncrono para login e reautenticação

- Testes: criar desafios `LOGIN` e `REAUTHENTICATION` de e-mail/SMS; verificar `pending`, chamada de task por ID e confirmação apenas após `sent`.
- RED: os services atuais geram OTP e marcam `sent` sincronicamente.
- Implementação: extrair `create_otp_challenge(user, factor, token, purpose)`; criar `pending`, expirar em cinco minutos e chamar `schedule_otp_delivery()` em `transaction.on_commit()`.
- Atualizar verificações de login/reauth para exigir `delivery_status == sent` nos fatores não-TOTP.
- Verificar: `test_mfa_enrollment.py`, `test_mfa_login.py`, `test_mfa_reauthentication.py`.

## Task 2 — Auditoria de reset administrativo

- Testes: reset cria evento contendo ator, alvo, justificativa e timestamp; não serializa segredo, token, telefone ou recovery code.
- RED: `reset_user_mfa()` só revoga recursos.
- Implementação: criar modelo `MFAResetAudit` com FKs para actor/target, `reason` e `created_at`; migration; registrar no auditlog excluindo campos sensíveis. Criar registro na mesma transação do reset.
- Verificar: `test_mfa_admin_reset.py` e check de migrations.

## Task 3 — Matriz de limites e concorrência

- Testes: desafio expirado é recusado; sexto erro é recusado e não incrementa além do limite; duas transações para o mesmo desafio produzem uma única sessão/reauth; entrega falha é recusada em login e reauth.
- RED: adicionar os cenários nos testes de login e reautenticação.
- Implementação: consolidar `attempts < 5`, marcar desafio bloqueado/consumido ao alcançar o limite e manter locks `select_for_update()`.
- Verificar: testes MFA completos e suíte do projeto no banco isolado.

## Gate final

Run: `uv run pytest -q --reuse-db`, `uv run ruff check api apps`, `uv run python manage.py makemigrations --check --dry-run`, `uv run python manage.py spectacular --validate --file /tmp/mfa-api.yml`, `uv run mkdocs build --strict`.
