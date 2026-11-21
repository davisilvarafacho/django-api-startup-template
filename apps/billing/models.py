"""Modelos do app de cobrança: plano, assinatura espelhada do gateway e caixa de eventos."""

from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.api.base.models import Base, BaseTenantless


class SubscriptionStatus(models.TextChoices):
    """Estado normalizado da assinatura, espelho de `Subscription.status`.

    Os valores são os mesmos de `django_checkouts.enums.SubscriptionStatus`, então a
    conversão é direta (`SubscriptionStatus(remote.status.value)`), sem tabela de
    de-para. Por isso é `TextChoices` e não `IntegerChoices`: exceção à preferência
    do projeto, justificada por essa conversão.
    """

    INCOMPLETE = "incomplete", _("Incompleta")
    TRIALING = "trialing", _("Em teste")
    ACTIVE = "active", _("Ativa")
    PAST_DUE = "past_due", _("Em atraso")
    PAUSED = "paused", _("Pausada")
    UNPAID = "unpaid", _("Não paga")
    CANCELED = "canceled", _("Cancelada")
    EXPIRED = "expired", _("Expirada")


class BillingCycle(models.TextChoices):
    """Periodicidade da cobrança, com os mesmos valores de `django_checkouts.enums.BillingCycle`.

    Também `TextChoices` pelo mesmo motivo de `SubscriptionStatus`.
    """

    WEEKLY = "weekly", _("Semanal")
    BIWEEKLY = "biweekly", _("Quinzenal")
    MONTHLY = "monthly", _("Mensal")
    BIMONTHLY = "bimonthly", _("Bimestral")
    QUARTERLY = "quarterly", _("Trimestral")
    SEMIANNUALLY = "semiannually", _("Semestral")
    YEARLY = "yearly", _("Anual")


class Plan(BaseTenantless):
    """Plano comercial. Exatamente um é o gratuito (`is_free`), garantido no `post_migrate`.

    `gateway_price_id` é imutável depois de salvo: preço novo vira plano novo, para que o
    espelho sempre mapeie `price_id -> Plan`.
    """

    Cycle = BillingCycle

    name = models.CharField(
        _("nome"),
        max_length=100,
        help_text=_("Nome do plano exibido ao cliente."),
        db_comment="Nome do plano exibido ao cliente.",
    )
    slug = models.SlugField(
        _("slug"),
        max_length=60,
        unique=True,
        help_text=_("Identificador estável do plano."),
        db_comment="Identificador estável do plano.",
    )
    description = models.TextField(
        _("descrição"),
        blank=True,
        default="",
        help_text=_("Descrição comercial do plano."),
        db_comment="Descrição comercial do plano.",
    )
    rank = models.PositiveSmallIntegerField(
        _("ordem"),
        help_text=_("Posição do plano na escala; o rank maior é o plano superior, e decide upgrade × downgrade."),
        db_comment="Posição do plano na escala; o rank maior é o plano superior, e decide upgrade × downgrade.",
    )
    price_amount = models.PositiveIntegerField(
        _("valor"),
        blank=True,
        null=True,
        help_text=_("Valor em centavos, apenas para exibição."),
        db_comment="Valor em centavos, apenas para exibição.",
    )
    currency = models.CharField(
        _("moeda"),
        max_length=3,
        default="BRL",
        help_text=_("Código ISO 4217 da moeda do valor."),
        db_comment="Código ISO 4217 da moeda do valor.",
    )
    billing_cycle = models.CharField(
        _("ciclo de cobrança"),
        max_length=20,
        choices=BillingCycle.choices,
        blank=True,
        null=True,
        help_text=_("Periodicidade da cobrança; vazio no plano gratuito."),
        db_comment="Periodicidade da cobrança; vazio no plano gratuito.",
    )
    gateway_price_id = models.CharField(
        _("ID do preço no gateway"),
        max_length=255,
        unique=True,
        blank=True,
        null=True,
        help_text=_("Preço correspondente no gateway; imutável após salvo, vazio no plano gratuito."),
        db_comment="Preço correspondente no gateway; imutável após salvo, vazio no plano gratuito.",
    )
    member_limit = models.PositiveSmallIntegerField(
        _("limite de membros"),
        help_text=_("Máximo de vínculos ativos que a organização pode ter neste plano."),
        db_comment="Máximo de vínculos ativos que a organização pode ter neste plano.",
    )
    is_free = models.BooleanField(
        _("gratuito"),
        default=False,
        help_text=_("Marca o plano gratuito; só pode existir um."),
        db_comment="Marca o plano gratuito; só pode existir um.",
    )

    @classmethod
    def get_free(cls):
        """Retorna o plano gratuito, garantido pelo handler de `post_migrate`.

        Returns:
            Plan: O plano com `is_free=True`.
        """
        return cls.objects.get(is_free=True)

    def __str__(self):
        return self.name

    class Meta:
        db_table = "billing_plan"
        ordering = ("-id",)
        constraints = [
            models.UniqueConstraint(
                fields=["is_free"],
                condition=Q(is_free=True),
                name="billing_plan_gratuito_unico",
            ),
        ]
        verbose_name = _("Plano")
        verbose_name_plural = _("Planos")
        permissions = []


class Subscription(Base):
    """Espelho local da assinatura da organização no gateway.

    Uma por organização. Nenhum campo "tem acesso" é gravado: o acesso é derivado do status.
    A escrita é feita só pelo `sync` (ver o fluxo na spec).
    """

    Status = SubscriptionStatus

    ACCESS_STATUSES = frozenset({Status.ACTIVE, Status.TRIALING, Status.PAST_DUE})
    TERMINAL_STATUSES = frozenset({Status.CANCELED, Status.EXPIRED})
    ABANDONED_STATUSES = frozenset({Status.UNPAID})

    plan = models.ForeignKey(
        Plan,
        verbose_name=_("plano"),
        on_delete=models.PROTECT,
        related_name="+",
        help_text=_("Plano contratado; o gratuito quando não há assinatura paga."),
        db_comment="Plano contratado; o gratuito quando não há assinatura paga.",
    )
    pending_plan = models.ForeignKey(
        Plan,
        verbose_name=_("plano agendado"),
        on_delete=models.PROTECT,
        related_name="+",
        null=True,
        blank=True,
        help_text=_("Plano para o qual a assinatura fará downgrade; limpo quando o espelho mostrar o preço novo."),
        db_comment="Plano para o qual a assinatura fará downgrade; limpo quando o espelho mostrar o preço novo.",
    )
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=SubscriptionStatus.choices,
        help_text=_("Estado da assinatura, espelhado do gateway."),
        db_comment="Estado da assinatura, espelhado do gateway.",
    )
    gateway_variant = models.CharField(
        _("variante do gateway"),
        max_length=50,
        blank=True,
        null=True,
        help_text=_("Variante do gateway que emitiu a assinatura; vazio no plano gratuito."),
        db_comment="Variante do gateway que emitiu a assinatura; vazio no plano gratuito.",
    )
    external_id = models.CharField(
        _("ID da assinatura no gateway"),
        max_length=255,
        blank=True,
        null=True,
        help_text=_("Identificador da assinatura no gateway; vazio no plano gratuito."),
        db_comment="Identificador da assinatura no gateway; vazio no plano gratuito.",
    )
    customer_id = models.CharField(
        _("ID do cliente no gateway"),
        max_length=255,
        blank=True,
        null=True,
        help_text=_("Identificador do cliente no gateway; vazio no plano gratuito."),
        db_comment="Identificador do cliente no gateway; vazio no plano gratuito.",
    )
    current_period_end = models.DateTimeField(
        _("fim do período atual"),
        blank=True,
        null=True,
        help_text=_("Fim do período de cobrança corrente."),
        db_comment="Fim do período de cobrança corrente.",
    )
    cancel_at = models.DateTimeField(
        _("cancelamento agendado para"),
        blank=True,
        null=True,
        help_text=_("Momento em que a assinatura será cancelada, quando há cancelamento agendado."),
        db_comment="Momento em que a assinatura será cancelada, quando há cancelamento agendado.",
    )
    synced_at = models.DateTimeField(
        _("sincronizado em"),
        blank=True,
        null=True,
        help_text=_("Último momento em que o espelho foi atualizado a partir do gateway."),
        db_comment="Último momento em que o espelho foi atualizado a partir do gateway.",
    )

    @property
    def is_free(self) -> bool:
        return self.plan.is_free

    @property
    def has_access(self) -> bool:
        return self.status in self.ACCESS_STATUSES

    @property
    def is_cancel_scheduled(self) -> bool:
        return self.cancel_at is not None and self.has_access

    @property
    def effective_plan(self) -> Plan:
        return self.plan if self.has_access else Plan.get_free()

    @property
    def member_limit(self) -> int:
        return self.effective_plan.member_limit

    def __str__(self):
        return f"{self.plan} @ {self.organizacao}"

    class Meta:
        db_table = "billing_subscription"
        ordering = ("-id",)
        constraints = [
            models.UniqueConstraint(
                fields=["organizacao"],
                name="billing_subscription_unica_por_organizacao",
            ),
        ]
        verbose_name = _("Assinatura")
        verbose_name_plural = _("Assinaturas")
        permissions = []


class BillingEvent(BaseTenantless):
    """Caixa de entrada dos eventos recebidos do gateway, para idempotência e reconciliação.

    Não guarda payload bruto, headers nem dados pessoais.
    """

    gateway_variant = models.CharField(
        _("variante do gateway"),
        max_length=50,
        help_text=_("Variante do gateway que enviou o evento."),
        db_comment="Variante do gateway que enviou o evento.",
    )
    event_id = models.CharField(
        _("ID do evento"),
        max_length=255,
        help_text=_("Identificador do evento no gateway."),
        db_comment="Identificador do evento no gateway.",
    )
    event_type = models.CharField(
        _("tipo do evento"),
        max_length=100,
        help_text=_("Tipo do evento conforme o gateway."),
        db_comment="Tipo do evento conforme o gateway.",
    )
    resource_kind = models.CharField(
        _("tipo do recurso"),
        max_length=50,
        help_text=_("Tipo do recurso ao qual o evento se refere."),
        db_comment="Tipo do recurso ao qual o evento se refere.",
    )
    resource_id = models.CharField(
        _("ID do recurso"),
        max_length=255,
        help_text=_("Identificador do recurso no gateway."),
        db_comment="Identificador do recurso no gateway.",
    )
    subscription_external_id = models.CharField(
        _("ID da assinatura no gateway"),
        max_length=255,
        blank=True,
        null=True,
        help_text=_("Identificador da assinatura no gateway extraído do recurso no momento do registro."),
        db_comment="Identificador da assinatura no gateway extraído do recurso no momento do registro.",
    )
    occurred_at = models.DateTimeField(
        _("ocorrido em"),
        help_text=_("Momento em que o gateway diz que o evento ocorreu."),
        db_comment="Momento em que o gateway diz que o evento ocorreu.",
    )
    processed_at = models.DateTimeField(
        _("processado em"),
        blank=True,
        null=True,
        help_text=_("Momento em que o evento foi aplicado ao espelho; vazio enquanto pendente."),
        db_comment="Momento em que o evento foi aplicado ao espelho; vazio enquanto pendente.",
    )
    attempts = models.PositiveSmallIntegerField(
        _("tentativas"),
        default=0,
        help_text=_("Número de tentativas de processamento."),
        db_comment="Número de tentativas de processamento.",
    )
    last_error = models.TextField(
        _("último erro"),
        blank=True,
        null=True,
        help_text=_("Descrição do último erro de processamento."),
        db_comment="Descrição do último erro de processamento.",
    )

    def __str__(self):
        return f"{self.gateway_variant}:{self.event_id} ({self.event_type})"

    class Meta:
        db_table = "billing_event"
        ordering = ("-id",)
        constraints = [
            models.UniqueConstraint(
                fields=["gateway_variant", "event_id"],
                name="billing_event_unico_por_variante",
            ),
        ]
        indexes = [
            models.Index(
                fields=["occurred_at"],
                condition=Q(processed_at__isnull=True),
                name="billing_event_pendente_idx",
            ),
        ]
        verbose_name = _("Evento de cobrança")
        verbose_name_plural = _("Eventos de cobrança")
        permissions = []
