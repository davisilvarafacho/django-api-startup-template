from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from django_rls.models import RLSModel
from django_rls.policies import BasePolicy, CustomPolicy, TenantPolicy

from apps.api.base.models import Base, BaseTenantless
from apps.assinaturas.models import (
    AlteracaoAssinatura,
    AssinaturaOrganizacao,
    PrecoPlano,
    PropostaComercial,
)

from .payloads import normalizar_payload_evento, validar_payload_evento
from .policies import IngressoUpdatePolicy


class ComponentePreco(models.IntegerChoices):
    BASE = 10, _("Base")
    SEAT = 20, _("Seat")


class FinalidadeCheckout(models.IntegerChoices):
    CONTRATACAO = 10, _("Contratação")
    ALTERACAO = 20, _("Alteração")
    PROPOSTA = 30, _("Proposta")
    FORMA_PAGAMENTO = 40, _("Forma de pagamento")


class StatusCheckout(models.IntegerChoices):
    CRIADO = 10, _("Criado")
    AGUARDANDO_GATEWAY = 20, _("Aguardando gateway")
    ABERTO = 30, _("Aberto")
    CONCLUIDO = 40, _("Concluído")
    EXPIRADO = 50, _("Expirado")
    CANCELADO = 60, _("Cancelado")
    FALHOU = 70, _("Falhou")


class StatusFatura(models.IntegerChoices):
    ABERTA = 10, _("Aberta")
    PAGA = 20, _("Paga")
    VENCIDA = 30, _("Vencida")
    IRRECUPERAVEL = 40, _("Irrecuperável")
    ANULADA = 50, _("Anulada")


class StatusEventoCobranca(models.IntegerChoices):
    RECEBIDO = 10, _("Recebido")
    ROTEADO = 20, _("Roteado")
    PROCESSANDO = 30, _("Processando")
    PROCESSADO = 40, _("Processado")
    IGNORADO = 50, _("Ignorado")
    FALHOU = 60, _("Falhou")


MOEDA = RegexValidator(regex=r"^[A-Z]{3}$", message=_("Informe três letras maiúsculas."))


class AssinaturaGateway(BaseTenantless):
    organizacao = models.ForeignKey("organizacoes.Organizacao", on_delete=models.PROTECT, related_name="assinaturas_gateway")
    assinatura = models.OneToOneField(AssinaturaOrganizacao, on_delete=models.PROTECT, related_name="gateway")
    variante = models.CharField(max_length=50)
    identificador_externo = models.CharField(max_length=255)

    def clean(self):
        super().clean()
        if self.assinatura_id and self.organizacao_id and self.assinatura.organizacao_id != self.organizacao_id:
            raise ValidationError({"organizacao": _("A organização deve coincidir com a assinatura.")})

    class Meta:
        db_table = "assinatura_gateway"
        constraints = [models.UniqueConstraint(fields=("variante", "identificador_externo"), name="assinatura_gateway_id_externo_global")]


class ReferenciaPrecoGateway(BaseTenantless):
    preco_plano = models.ForeignKey(PrecoPlano, on_delete=models.PROTECT, related_name="referencias_gateway")
    variante = models.CharField(max_length=50)
    componente = models.PositiveSmallIntegerField(choices=ComponentePreco.choices)
    identificador_externo = models.CharField(max_length=255)

    class Meta:
        db_table = "referencia_preco_gateway"
        constraints = [
            models.UniqueConstraint(fields=("variante", "identificador_externo"), name="referencia_preco_id_externo_global"),
            models.UniqueConstraint(
                fields=("preco_plano", "variante", "componente"),
                condition=models.Q(is_active=True, is_deleted=False),
                name="referencia_preco_ativa_unica",
            ),
        ]


class CheckoutCobranca(Base):
    # O identificador do gateway é garantidamente global e resolve o tenant.
    global_unique_constraint_names = frozenset({"checkout_id_externo_global"})

    assinatura = models.ForeignKey(AssinaturaOrganizacao, on_delete=models.PROTECT, related_name="checkouts_cobranca")
    alteracao = models.ForeignKey(AlteracaoAssinatura, on_delete=models.PROTECT, null=True, blank=True, related_name="checkouts_cobranca")
    proposta = models.ForeignKey(PropostaComercial, on_delete=models.PROTECT, null=True, blank=True, related_name="checkouts_cobranca")
    finalidade = models.PositiveSmallIntegerField(choices=FinalidadeCheckout.choices)
    status = models.PositiveSmallIntegerField(choices=StatusCheckout.choices, default=StatusCheckout.CRIADO)
    chave_idempotencia = models.CharField(max_length=120)
    variante = models.CharField(max_length=50)
    identificador_externo = models.CharField(max_length=255, null=True, blank=True)
    url = models.URLField(max_length=2048, blank=True)
    valor_esperado_centavos = models.PositiveBigIntegerField()
    moeda_esperada = models.CharField(max_length=3, validators=[MOEDA])
    expira_em = models.DateTimeField(null=True, blank=True)
    concluido_em = models.DateTimeField(null=True, blank=True)

    def clean(self):
        super().clean()
        ids = [self.assinatura.organizacao_id]
        ids += [obj.organizacao_id for obj in (self.alteracao, self.proposta) if obj is not None]
        if self.organizacao_id and any(value != self.organizacao_id for value in ids):
            raise ValidationError({"organizacao": _("Todas as referências devem pertencer à mesma organização.")})

    class Meta(Base.Meta):
        db_table = "checkout_cobranca"
        constraints = [
            models.UniqueConstraint(fields=("organizacao", "chave_idempotencia"), name="checkout_chave_unica_por_org"),
            models.UniqueConstraint(
                fields=("variante", "identificador_externo"),
                condition=models.Q(identificador_externo__isnull=False),
                name="checkout_id_externo_global",
            ),
            models.CheckConstraint(condition=models.Q(moeda_esperada__regex=r"^[A-Z]{3}$"), name="checkout_moeda_iso"),
            models.CheckConstraint(
                condition=(
                    models.Q(finalidade=FinalidadeCheckout.ALTERACAO, alteracao__isnull=False, proposta__isnull=True)
                    | models.Q(finalidade=FinalidadeCheckout.PROPOSTA, alteracao__isnull=True, proposta__isnull=False)
                    | models.Q(
                        finalidade__in=(FinalidadeCheckout.CONTRATACAO, FinalidadeCheckout.FORMA_PAGAMENTO),
                        alteracao__isnull=True,
                        proposta__isnull=True,
                    )
                ),
                name="checkout_finalidade_referencias",
            ),
            models.CheckConstraint(
                condition=~models.Q(finalidade=FinalidadeCheckout.FORMA_PAGAMENTO) | models.Q(valor_esperado_centavos=0),
                name="checkout_forma_pagamento_zero",
            ),
        ]


class FaturaAssinatura(Base):
    assinatura = models.ForeignKey(AssinaturaOrganizacao, on_delete=models.PROTECT, related_name="faturas")
    variante = models.CharField(max_length=50)
    identificador_externo = models.CharField(max_length=255)
    status = models.PositiveSmallIntegerField(choices=StatusFatura.choices)
    motivo = models.CharField(max_length=255, blank=True)
    subtotal_centavos = models.PositiveBigIntegerField(default=0)
    desconto_centavos = models.PositiveBigIntegerField(default=0)
    imposto_centavos = models.PositiveBigIntegerField(default=0)
    total_centavos = models.PositiveBigIntegerField(default=0)
    moeda = models.CharField(max_length=3, validators=[MOEDA])
    periodo_iniciado_em = models.DateTimeField(null=True, blank=True)
    periodo_termina_em = models.DateTimeField(null=True, blank=True)
    vencimento_em = models.DateTimeField(null=True, blank=True)
    paga_em = models.DateTimeField(null=True, blank=True)
    proxima_tentativa_em = models.DateTimeField(null=True, blank=True)
    tentativas = models.PositiveSmallIntegerField(default=0)
    url_hospedada = models.URLField(max_length=2048, blank=True)

    def clean(self):
        super().clean()
        if self.assinatura_id and self.organizacao_id and self.assinatura.organizacao_id != self.organizacao_id:
            raise ValidationError({"organizacao": _("A organização deve coincidir com a assinatura.")})

    class Meta(Base.Meta):
        db_table = "fatura_assinatura"
        constraints = [
            models.UniqueConstraint(fields=("organizacao", "variante", "identificador_externo"), name="fatura_id_externo_unico_por_org"),
            models.CheckConstraint(condition=models.Q(moeda__regex=r"^[A-Z]{3}$"), name="fatura_moeda_iso"),
        ]


INGRESSO = (
    "organizacao_id IS NULL AND "
    "(SELECT NULLIF(current_setting('rls.tenant_id', true), '')::integer) = 0 AND "
    "(SELECT NULLIF(current_setting('rls.billing_ingress', true), '')) = '1'"
)


class EventoCobranca(BaseTenantless, RLSModel):
    organizacao = models.ForeignKey(
        "organizacoes.Organizacao", on_delete=models.PROTECT, null=True, blank=True, related_name="eventos_cobranca", db_index=True
    )
    variante = models.CharField(max_length=50)
    identificador_evento = models.CharField(max_length=255)
    tipo = models.CharField(max_length=100)
    identificador_assinatura = models.CharField(max_length=255, blank=True)
    identificador_checkout = models.CharField(max_length=255, blank=True)
    identificador_fatura = models.CharField(max_length=255, blank=True)
    status = models.PositiveSmallIntegerField(choices=StatusEventoCobranca.choices, default=StatusEventoCobranca.RECEBIDO)
    exige_tenant = models.BooleanField(default=True)
    tentativas_roteamento = models.PositiveSmallIntegerField(default=0)
    tentativas_processamento = models.PositiveSmallIntegerField(default=0)
    payload_normalizado = models.JSONField(default=dict, validators=[validar_payload_evento])
    hash_payload = models.CharField(max_length=64)
    erro = models.CharField(max_length=500, blank=True)
    ocorrido_em = models.DateTimeField(null=True, blank=True)
    processado_em = models.DateTimeField(null=True, blank=True)
    proxima_tentativa_em = models.DateTimeField(null=True, blank=True)

    def save(self, *args, **kwargs):
        self.payload_normalizado = normalizar_payload_evento(self.payload_normalizado)
        return super().save(*args, **kwargs)

    class Meta:
        db_table = "evento_cobranca"
        rls_policies = [
            TenantPolicy(name="evento_tenant", tenant_field="organizacao"),
            CustomPolicy(name="evento_ingresso_select", operation=BasePolicy.SELECT, expression=INGRESSO),
            CustomPolicy(name="evento_ingresso_insert", operation=BasePolicy.INSERT, expression=INGRESSO),
            IngressoUpdatePolicy(name="evento_ingresso_update", operation=BasePolicy.UPDATE, expression=INGRESSO),
        ]
        constraints = [
            models.UniqueConstraint(fields=("variante", "identificador_evento"), name="evento_cobranca_id_global"),
            models.CheckConstraint(
                condition=~models.Q(status=StatusEventoCobranca.PROCESSADO, exige_tenant=True) | models.Q(organizacao__isnull=False),
                name="evento_processado_exige_tenant",
            ),
        ]
