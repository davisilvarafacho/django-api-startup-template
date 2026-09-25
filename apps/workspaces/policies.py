"""Policies RLS para o escopo de workspace."""

from django_rls.policies import BasePolicy


class WorkspacePolicy(BasePolicy):
    """Restringe registros ao workspace selecionado no contexto RLS."""

    def get_sql_expression(self) -> str:
        """Retorna a expressão SQL dos modos de acesso publicados pelo runtime."""
        return """
(
    (SELECT NULLIF(current_setting('rls.workspace_mode', true), '')) = 'system'
    OR (
        (SELECT NULLIF(current_setting('rls.workspace_mode', true), '')) = 'control'
        AND workspace_id IS NULL
    )
    OR (
        (SELECT NULLIF(current_setting('rls.workspace_mode', true), '')) = 'api_key'
        AND (
            workspace_id IS NULL
            OR workspace_id IN (
                SELECT w.id FROM workspace w
                WHERE w.organizacao_id = (SELECT NULLIF(current_setting('rls.tenant_id', true), '')::bigint)
                  AND w.is_active AND NOT w.is_deleted
            )
        )
        AND EXISTS (
            SELECT 1 FROM workspace w
            WHERE w.organizacao_id = (SELECT NULLIF(current_setting('rls.tenant_id', true), '')::bigint)
              AND w.is_active AND NOT w.is_deleted
        )
    )
    OR (
        (SELECT NULLIF(current_setting('rls.workspace_mode', true), '')) = 'membership'
        AND (
            (
                workspace_id IS NULL
                AND EXISTS (
                    SELECT 1
                    FROM workspace_vinculo vw
                    JOIN workspace w ON w.id = vw.workspace_id
                    JOIN vinculo v ON v.id = vw.vinculo_id
                    WHERE vw.vinculo_id = (SELECT NULLIF(current_setting('rls.membership_id', true), '')::bigint)
                      AND vw.is_active AND NOT vw.is_deleted
                      AND w.is_active AND NOT w.is_deleted
                      AND v.is_active AND NOT v.is_deleted
                      AND w.organizacao_id = (SELECT NULLIF(current_setting('rls.tenant_id', true), '')::bigint)
                      AND v.organizacao_id = w.organizacao_id
                )
            )
            OR workspace_id IN (
                SELECT vw.workspace_id
                FROM workspace_vinculo vw
                JOIN workspace w ON w.id = vw.workspace_id
                JOIN vinculo v ON v.id = vw.vinculo_id
                WHERE vw.vinculo_id = (SELECT NULLIF(current_setting('rls.membership_id', true), '')::bigint)
                  AND vw.is_active AND NOT vw.is_deleted AND vw.selected_for_view
                  AND w.is_active AND NOT w.is_deleted
                  AND v.is_active AND NOT v.is_deleted
                  AND w.organizacao_id = (SELECT NULLIF(current_setting('rls.tenant_id', true), '')::bigint)
                  AND v.organizacao_id = w.organizacao_id
            )
        )
    )
)
""".strip()
