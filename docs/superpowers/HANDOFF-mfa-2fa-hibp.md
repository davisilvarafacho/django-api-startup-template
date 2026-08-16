# Handoff: MFA, senhas e HIBP

- Data do checkpoint: 2026-07-31
- Branch: `feat/mfa-2fa-hibp`
- Worktree: `.worktrees/mfa-2fa-hibp`

## Estado atual

A fundacao de autenticacao foi implementada. As tarefas 1 a 4 passaram por
implementacao, correcoes e revisao independente sem pendencias. A tarefa 5 foi
implementada e validada no commit `d076cb5`, mas sua revisao independente foi
interrompida antes do parecer final.

Ultimo commit funcional antes deste handoff:

```text
d076cb5 docs(auth): document token foundation
```

## Fundacao concluida

- modelo de token Knox substituivel com tipos `TOKEN`, `RESET_PASSWORD`,
  `PRE_AUTH` e `API_KEY`;
- `AuthToken.EPHEMERAL_TYPES`, contendo `PRE_AUTH` e `RESET_PASSWORD`;
- propriedade documentada `is_expired`;
- emissao centralizada e atomica de token e metadados;
- autenticacao tipada que impede tokens efemeros de acessarem a API normal;
- login centralizado, erros publicos tipados e throttles;
- reautenticacao recente por senha, com decorator e permissao globais;
- suporte do step-up em actions, handlers HTTP e classes;
- command `cleanup_expired_auth_tokens`, inclusive `--dry-run`;
- limpeza em lotes: efemeros imediatamente, sessoes apos retencao e API keys
  nunca;
- task Celery `autenticacao.cleanup_expired_tokens`, sem resultado persistido e
  com retry para falha operacional;
- Celery Beat diario as 00:00 em `America/Sao_Paulo`;
- documentacao da fundacao em `docs/explanation/autenticacao.md`.

## Verificacoes mais recentes

- `147 passed` para `apps/api/autenticacao/tests` e `apps/api/core/tests`, em
  PostgreSQL isolado com `--create-db`;
- `manage.py check`: sem problemas;
- `makemigrations --check --dry-run`: nenhuma migration pendente;
- MkDocs em modo estrito: passou;
- Ruff do escopo alterado: passou;
- `git diff --check`: passou.

O Ruff global ainda encontra dois `UP017` preexistentes, sem diff contra
`main`, em `apps/api/core/b2_storage.py` e
`apps/api/core/tests/test_b2_storage.py`.

## Proximo passo exato

1. Repetir a revisao independente da tarefa 5 da fundacao, usando:
   - brief: `.superpowers/sdd/2026-07-29-auth-token-foundation/task-5-brief.md`;
   - report: `.superpowers/sdd/task-5-report.md`;
   - pacote: `.superpowers/sdd/2026-07-29-auth-token-foundation/review-93ac23b..d076cb5.diff`.
2. Se o gate estiver limpo, registrar a tarefa 5 em
   `.superpowers/sdd/progress.md`.
3. Iniciar a tarefa 1 de `.ai/brainstorming/plan/2026-07-29-mfa-api.md`.

Os arquivos em `.superpowers/sdd/` sao deliberadamente ignorados pelo Git e
existem apenas nesta worktree.

## Trabalho restante

Plano MFA, 7 tarefas:

1. dependencia e modelos MFA;
2. backends de e-mail/SMS/TOTP, enrollment e recovery codes;
3. `PRE_AUTH` no login;
4. trusted devices;
5. MFA na reautenticacao;
6. reset administrativo e checks;
7. documentacao, roadmap e verificacao integral.

Plano de senha/HIBP, 5 tarefas:

1. cliente e validator HaveIBeenPwned, com fail-open;
2. definicao centralizada de senha e bypass explicito em `create_superuser()`;
3. redefinicao de senha para usuario deslogado;
4. alteracao autenticada, validacoes e revogacoes;
5. documentacao e verificacao integral.

## Decisoes aprovadas que devem ser preservadas

- arquitetura MFA propria;
- e-mail, SMS, TOTP e recovery codes, com mais de um fator ativo;
- segredo TOTP criptografado pela implementacao interna existente;
- SMS por backend configuravel;
- cliente escolhe o fator; nenhum OTP e enviado automaticamente no login;
- trusted device por 30 dias, com rotacao e revogacao;
- senha vazada sempre bloqueada na definicao, mas HIBP opera em fail-open;
- redefinicao deslogada nao exige MFA;
- `create_superuser()` nao executa validadores de senha/HIBP;
- API keys e tokens efemeros nunca satisfazem reautenticacao recente;
- reset administrativo de MFA exige permissao, reautenticacao, justificativa e
  auditoria.

## Referencias

- Design aprovado: `.ai/brainstorming/spec/2026-07-29-mfa-password-security-design.md`
- Fundacao: `.ai/brainstorming/plan/2026-07-29-auth-token-foundation.md`
- MFA: `.ai/brainstorming/plan/2026-07-29-mfa-api.md`
- Senhas/HIBP: `.ai/brainstorming/plan/2026-07-29-password-security-hibp.md`
