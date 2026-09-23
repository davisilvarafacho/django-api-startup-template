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
