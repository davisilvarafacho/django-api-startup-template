# Task 2 — relatório

## Implementação

- Criados `WorkspaceErrorCode`, `Workspaces` e `AcessosWorkspace` com transações, revalidação de estado, reativação de pares soft deleted, current persistido e limpeza atômica ao revogar/inativar.
- Promoção para Administrador/Proprietário garante acesso a todos os Workspaces ativos; rebaixamento preserva os vínculos.
- Onboarding cria o Workspace `Principal`, concede acesso selecionado ao proprietário e persiste `current_workspace` antes da assinatura.
- Adicionados testes de criação, onboarding, promoção/rebaixamento, revogação obrigatória e seleção atômica.

## TDD (RED/GREEN)

Foi feita uma tentativa de execução focada após adicionar os testes, mas ela não alcançou a fase de asserções porque o PostgreSQL do container `postgres` não publica a porta 5432 para o host. A execução teve 31 erros de setup pela mesma indisponibilidade (`connection refused localhost:5432`), sem falhas de asserção observáveis; portanto não há evidência RED/GREEN local para registrar.

## Verificação

- `uv run ruff check ...`: passou.
- `uv run python manage.py check`: passou, sem problemas do system check (4 silenciados).
- `uv run python -m compileall -q apps/workspaces apps/organizacoes`: passou.
- `git diff --check`: passou.
- `uv run pytest apps/workspaces/tests/test_workspaces.py apps/workspaces/tests/test_accesses.py apps/organizacoes/tests/test_onboarding.py apps/organizacoes/tests/test_memberships.py -q --reuse-db`: bloqueado no setup por PostgreSQL inacessível no host.

## Self-review e preocupações

- As APIs públicas seguem as assinaturas do brief.
- O fluxo de locks usa Organização antes de vínculos, Workspaces e acessos nas operações principais; a corrida concorrente requer validação em ambiente PostgreSQL publicado.
- Não foi possível validar comportamento transacional, constraints ou testes de concorrência enquanto o container estiver sem porta publicada.

## Commit

`feat: gerenciar ciclo de vida de workspaces` (hash disponível no histórico do worktree)

## Fix report — rodada de revisão

- A ordem de locks foi explicitada nas operações de Workspace: Organização, todos os Vínculos afetados ordenados por PK, Workspaces ordenados por PK e, por fim, acessos. `inativar()` agora bloqueia também vínculos que só possuem acessos no Workspace; `atualizar_vinculo()` bloqueia vínculo alvo e vínculo do ator em conjunto antes da validação.
- `selecionar_visualizacao()` bloqueia os Workspaces solicitados e relacionados, rejeita relações cross-tenant com `workspaces.organization_mismatch` e mantém a validação atômica da seleção.
- Incluídos testes PostgreSQL concorrentes com duas threads/conexões para criação + promoção e resolução + inativação, além de testes de limpeza de `current_workspace` e rejeição cross-tenant.

### Verificação da rodada

- `uv run ruff check apps/workspaces apps/organizacoes/memberships.py`: **passou** (`All checks passed!`).
- `uv run python manage.py check`: **passou** (`System check identified no issues (4 silenced).`).
- `uv run python -m compileall -q apps/workspaces apps/organizacoes`: **passou**.
- `git diff --check`: **passou**.
- `uv run pytest apps/workspaces/tests/test_workspaces.py apps/workspaces/tests/test_accesses.py apps/organizacoes/tests/test_onboarding.py apps/organizacoes/tests/test_memberships.py -q --reuse-db`: **bloqueado no setup**: PostgreSQL em `localhost:5432` recusou conexão (`33 errors`, nenhum teste chegou às asserções).
- Nova tentativa após a publicação da porta: **bloqueada no setup** por autenticação PostgreSQL (`fe_sendauth: no password supplied`, `35 errors`, nenhum teste chegou às asserções).
- Com as credenciais temporárias do container (`DATABASE_USER=postgres`, `DATABASE_PASSWORD=postgres`), o mesmo comando passou: **35 passed, 1 warning in 38.58s**.

### Commit da rodada

`fix: ordenar locks e cobrir corridas de workspaces`
