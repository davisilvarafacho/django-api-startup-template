"""Filtros da API de cobrança."""

from apps.api.base.filtersets import BaseFilterSet
from apps.billing.models import Plan


class PlanFilter(BaseFilterSet):
    """Filtra o catálogo de planos pelo tipo e pela periodicidade da cobrança."""

    class Meta:
        model = Plan
        fields = ["is_free", "billing_cycle"]
