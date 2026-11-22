"""Serializers da API de cobrança: leitura em serpy e a escolha de plano nas rotas de escrita."""

from rest_framework import serializers

import serpy

from apps.api.base.serializers import BaseModelSerpySerializer


class PlanSerpySerializer(BaseModelSerpySerializer):
    """Plano do catálogo, como exibido ao cliente."""

    slug = serpy.StrField()
    name = serpy.StrField()
    description = serpy.StrField()
    rank = serpy.IntField()
    price_amount = serpy.Field()
    currency = serpy.StrField()
    billing_cycle = serpy.Field()
    member_limit = serpy.IntField()
    is_free = serpy.BoolField()


class SubscriptionSerpySerializer(serpy.Serializer):
    """Espelho da assinatura da organização. `members` é acrescentado pela view."""

    plan = serpy.MethodField()
    pending_plan = serpy.MethodField()
    status = serpy.StrField()
    has_access = serpy.BoolField()
    effective_plan = serpy.MethodField()
    current_period_end = serpy.MethodField()
    cancel_at = serpy.MethodField()
    is_cancel_scheduled = serpy.BoolField()

    def get_plan(self, obj):
        return PlanSerpySerializer(obj.plan).data

    def get_pending_plan(self, obj):
        return PlanSerpySerializer(obj.pending_plan).data if obj.pending_plan_id else None

    def get_effective_plan(self, obj):
        return PlanSerpySerializer(obj.effective_plan).data

    def get_current_period_end(self, obj):
        return obj.current_period_end.isoformat() if obj.current_period_end else None

    def get_cancel_at(self, obj):
        return obj.cancel_at.isoformat() if obj.cancel_at else None


class PlanSelectionSerializer(serializers.Serializer):
    """Corpo das rotas que escolhem um plano pelo slug."""

    plan = serializers.SlugField(max_length=60, help_text="Slug do plano escolhido, conforme o catálogo.")
