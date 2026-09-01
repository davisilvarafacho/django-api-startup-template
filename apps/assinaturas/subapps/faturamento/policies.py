from django_rls.policies import CustomPolicy


class IngressoUpdatePolicy(CustomPolicy):
    """Permite ao ingresso rotear uma linha nula exatamente uma vez."""

    def get_using_expression(self):
        return (
            "organizacao_id IS NULL AND "
            "(SELECT NULLIF(current_setting('rls.tenant_id', true), '')::integer) = 0 AND "
            "(SELECT NULLIF(current_setting('rls.billing_ingress', true), '')) = '1'"
        )

    def get_check_expression(self):
        return (
            "organizacao_id IS NOT NULL AND "
            "(SELECT NULLIF(current_setting('rls.tenant_id', true), '')::integer) = 0 AND "
            "(SELECT NULLIF(current_setting('rls.billing_ingress', true), '')) = '1'"
        )
