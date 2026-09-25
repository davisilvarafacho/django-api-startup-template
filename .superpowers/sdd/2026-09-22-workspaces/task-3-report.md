# Relatório — Task 3

## Implementação

- Criada `WorkspacePolicy`, com `permissive=False`, expressão fechada para os modos `system`, `control`, `api_key` e `membership`, incluindo registros compartilhados e `selected_for_view`.
- Criado `WorkspaceMixin` e incorporado `Base.workspace` (`PROTECT`, anulável) a todas as tabelas concretas herdadas por `Base`, preservando a `TenantPolicy` da Organização.
- Adicionado o system check `base.W003` para models com `workspace_required=True` sem `CheckConstraint` que rejeite workspace nulo.
- `aplicar_metadata()` agora copia `workspace_id` do objeto de origem quando cria a linha de metadata.
- Geradas migrations reversíveis para `Metadata`, `PropostaComercial`, `AssinaturaOrganizacao`, `AlteracaoAssinatura`, `CheckoutCobranca`, `FaturaAssinatura`, `ReaberturaEventoCobranca` e `SolicitacaoReconciliacaoCobranca`; todas dependem de `workspaces.0002_backfill_workspaces_iniciais`.

## Verificação

- `pytest apps/workspaces/tests/test_policy_sql.py apps/api/base/tests/test_model_checks.py -q --no-cov`: **9 passed**.
- `python manage.py check`: **sem problemas** (4 checks silenciados por configuração existente).
- `python manage.py makemigrations --check --dry-run`: **No changes detected**.
- Ruff nos arquivos alterados: **All checks passed**.
- Os testes de metadata marcados com `django_db` não puderam abrir o PostgreSQL local: `localhost:5432`, usuário sem senha (`fe_sendauth: no password supplied`). Não houve incompatibilidade observada com `django-rls`.

