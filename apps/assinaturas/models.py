"""Catálogo comercial global, versionado e imutável após publicação."""

from django.core.validators import RegexValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.api.base.models import (
    ActiveManagerMixin,
    BaseQuerySet,
    BaseTenantless,
    DeferredFieldsManagerMixin,
    ExcludeDeletedManagerMixin,
)
from utils.logs import register


class Periodicidade(models.IntegerChoices):
    """Periodicidades comerciais persistidas nos preços."""

    MENSAL = 10, _("Mensal")
    ANUAL = 20, _("Anual")


CAMPOS_OPERACIONAIS_VERSAO = frozenset({"atual", "is_active", "last_modified_at"})
CAMPOS_IMUTAVEIS_VERSAO = frozenset(
    {
        "plano",
        "numero",
        "seats_inclusos",
        "limite_seats_trial",
        "duracao_trial_dias",
        "carencia_pagamento_dias",
        "carencia_excesso_seats_dias",
        "expansao_automatica_seats",
        "recursos",
        "publicada_em",
    }
)
CAMPOS_PROTEGIDOS_VERSAO = CAMPOS_IMUTAVEIS_VERSAO | {"created_at", "created_by", "is_deleted"}
CAMPOS_OPERACIONAIS_PRECO = frozenset({"is_active", "last_modified_at"})
CAMPOS_IMUTAVEIS_PRECO = frozenset(
    {
        "versao_plano",
        "periodicidade",
        "moeda",
        "valor_base_centavos",
        "valor_seat_centavos",
    }
)
CAMPOS_PROTEGIDOS_PRECO = CAMPOS_IMUTAVEIS_PRECO | {"created_at", "created_by", "is_deleted"}


def _materializar_recursos(valores):
    from apps.assinaturas.features import CATALOGO_RECURSOS

    return CATALOGO_RECURSOS.validar_snapshot(valores).materializar()


class VersoesPlanoQuerySet(BaseQuerySet):
    def update(self, **kwargs):
        if "recursos" in kwargs:
            kwargs["recursos"] = _materializar_recursos(kwargs["recursos"])
        if set(kwargs) - CAMPOS_OPERACIONAIS_VERSAO and self.filter(publicada_em__isnull=False).exists():
            raise ValueError("Versão de plano publicada é imutável.")
        return super().update(**kwargs)

    def bulk_create(
        self,
        objs,
        batch_size=None,
        ignore_conflicts=False,
        update_conflicts=False,
        update_fields=None,
        unique_fields=None,
    ):
        objetos = tuple(objs)
        for objeto in objetos:
            objeto.recursos = _materializar_recursos(objeto.recursos)
        return super().bulk_create(
            objetos,
            batch_size=batch_size,
            ignore_conflicts=ignore_conflicts,
            update_conflicts=update_conflicts,
            update_fields=update_fields,
            unique_fields=unique_fields,
        )

    def bulk_update(self, objs, fields, batch_size=None):
        objetos = tuple(objs)
        if "recursos" in fields:
            for objeto in objetos:
                objeto.recursos = _materializar_recursos(objeto.recursos)
        if set(fields) - CAMPOS_OPERACIONAIS_VERSAO:
            ids = [obj.pk for obj in objetos if obj.pk is not None]
            if self.model._base_manager.filter(pk__in=ids, publicada_em__isnull=False).exists():
                raise ValueError("Versão de plano publicada é imutável.")
        return super().bulk_update(objetos, fields, batch_size=batch_size)


class PrecosPlanoQuerySet(BaseQuerySet):
    def update(self, **kwargs):
        if set(kwargs) - CAMPOS_OPERACIONAIS_PRECO and self.filter(versao_plano__publicada_em__isnull=False).exists():
            raise ValueError("Preço de versão publicada é imutável.")
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, batch_size=None):
        objetos = tuple(objs)
        if set(fields) - CAMPOS_OPERACIONAIS_PRECO:
            ids = [obj.pk for obj in objetos if obj.pk is not None]
            if self.model._base_manager.filter(pk__in=ids, versao_plano__publicada_em__isnull=False).exists():
                raise ValueError("Preço de versão publicada é imutável.")
        return super().bulk_update(objetos, fields, batch_size=batch_size)


VersoesPlanoQuerySetManager = models.Manager.from_queryset(VersoesPlanoQuerySet)
PrecosPlanoQuerySetManager = models.Manager.from_queryset(PrecosPlanoQuerySet)


class VersoesPlanoManager(ExcludeDeletedManagerMixin, DeferredFieldsManagerMixin, VersoesPlanoQuerySetManager):
    pass


class TodasVersoesPlanoManager(DeferredFieldsManagerMixin, VersoesPlanoQuerySetManager):
    pass


class VersoesPlanoAtivasManager(
    ActiveManagerMixin,
    ExcludeDeletedManagerMixin,
    DeferredFieldsManagerMixin,
    VersoesPlanoQuerySetManager,
):
    pass


class PrecosPlanoManager(ExcludeDeletedManagerMixin, DeferredFieldsManagerMixin, PrecosPlanoQuerySetManager):
    pass


class TodosPrecosPlanoManager(DeferredFieldsManagerMixin, PrecosPlanoQuerySetManager):
    pass


class PrecosPlanoAtivosManager(
    ActiveManagerMixin,
    ExcludeDeletedManagerMixin,
    DeferredFieldsManagerMixin,
    PrecosPlanoQuerySetManager,
):
    pass


class Plano(BaseTenantless):
    """Identidade estável de um produto comercial."""

    codigo = models.SlugField(_("código"), max_length=60)
    nome = models.CharField(_("nome"), max_length=150)
    descricao = models.TextField(_("descrição"), blank=True)
    visivel = models.BooleanField(_("visível"), default=True)

    def __str__(self):
        return self.nome

    class Meta:
        db_table = "plano"
        ordering = ("nome",)
        verbose_name = _("Plano")
        verbose_name_plural = _("Planos")
        constraints = [
            models.UniqueConstraint(
                fields=("codigo",),
                condition=models.Q(is_deleted=False),
                name="plano_codigo_unico_nao_excluido",
            )
        ]


class VersaoPlano(BaseTenantless):
    """Termos de uma versão de plano, imutáveis depois de publicados."""

    plano = models.ForeignKey(Plano, verbose_name=_("plano"), on_delete=models.PROTECT, related_name="versoes")
    numero = models.PositiveSmallIntegerField(_("número"))
    atual = models.BooleanField(_("atual"), default=False)
    seats_inclusos = models.PositiveSmallIntegerField(_("seats inclusos"), default=0)
    limite_seats_trial = models.PositiveSmallIntegerField(_("limite de seats no trial"), null=True, blank=True)
    duracao_trial_dias = models.PositiveSmallIntegerField(_("duração do trial em dias"), default=0)
    carencia_pagamento_dias = models.PositiveSmallIntegerField(_("carência de pagamento em dias"), default=7)
    carencia_excesso_seats_dias = models.PositiveSmallIntegerField(_("carência de excesso de seats em dias"), default=7)
    expansao_automatica_seats = models.BooleanField(_("expansão automática de seats"), default=False)
    recursos = models.JSONField(_("recursos"), default=dict)
    publicada_em = models.DateTimeField(_("publicada em"), null=True, blank=True)

    objects = VersoesPlanoManager()  # type: ignore[misc, assignment]
    all_objects = TodasVersoesPlanoManager()  # type: ignore[misc, assignment]
    ativos = VersoesPlanoAtivasManager()  # type: ignore[misc, assignment]

    def save(self, *args, **kwargs):
        campos_atualizados = set(kwargs["update_fields"]) if kwargs.get("update_fields") is not None else None
        publicando = campos_atualizados is not None and "publicada_em" in campos_atualizados and self.publicada_em is not None
        if self._state.adding or campos_atualizados is None or "recursos" in campos_atualizados or publicando:
            self.recursos = _materializar_recursos(self.recursos)
        if publicando and campos_atualizados is not None and "recursos" not in campos_atualizados:
            kwargs["update_fields"] = {*campos_atualizados, "recursos"}
        if self.pk is not None:
            anterior = type(self)._base_manager.filter(pk=self.pk).values("publicada_em", *CAMPOS_PROTEGIDOS_VERSAO).first()
            if anterior is not None and anterior["publicada_em"] is not None:
                mudou = any(
                    anterior[campo] != getattr(self, f"{campo}_id" if campo in {"plano", "created_by"} else campo)
                    for campo in CAMPOS_PROTEGIDOS_VERSAO
                )
                if mudou:
                    raise ValueError("Versão de plano publicada é imutável.")
        return super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        self.recursos = _materializar_recursos(self.recursos)

    def __str__(self):
        return f"{self.plano.codigo} v{self.numero}"

    class Meta:
        base_manager_name = "all_objects"
        db_table = "versao_plano"
        ordering = ("plano_id", "-numero")
        verbose_name = _("Versão de plano")
        verbose_name_plural = _("Versões de plano")
        constraints = [
            models.UniqueConstraint(fields=("plano", "numero"), name="versao_plano_numero_unico_por_plano"),
            models.UniqueConstraint(
                fields=("plano",),
                condition=models.Q(atual=True, is_deleted=False),
                name="versao_plano_atual_unica_por_plano",
            ),
            models.CheckConstraint(
                condition=models.Q(atual=False) | models.Q(publicada_em__isnull=False, is_deleted=False),
                name="versao_plano_atual_publicada_viva",
            ),
        ]


class PrecoPlano(BaseTenantless):
    """Preço de uma versão por periodicidade e moeda."""

    versao_plano = models.ForeignKey(VersaoPlano, verbose_name=_("versão do plano"), on_delete=models.PROTECT, related_name="precos")
    periodicidade = models.PositiveSmallIntegerField(_("periodicidade"), choices=Periodicidade.choices)
    moeda = models.CharField(
        _("moeda"),
        max_length=3,
        validators=[RegexValidator(regex=r"^[A-Z]{3}$", message=_("Informe três letras maiúsculas."))],
    )
    valor_base_centavos = models.PositiveBigIntegerField(_("valor base em centavos"))
    valor_seat_centavos = models.PositiveBigIntegerField(_("valor por seat em centavos"))

    objects = PrecosPlanoManager()  # type: ignore[misc, assignment]
    all_objects = TodosPrecosPlanoManager()  # type: ignore[misc, assignment]
    ativos = PrecosPlanoAtivosManager()  # type: ignore[misc, assignment]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            anterior = type(self)._base_manager.filter(pk=self.pk).values("versao_plano__publicada_em", *CAMPOS_PROTEGIDOS_PRECO).first()
            if anterior is not None and anterior["versao_plano__publicada_em"] is not None:
                mudou = any(
                    anterior[campo] != getattr(self, f"{campo}_id" if campo in {"versao_plano", "created_by"} else campo)
                    for campo in CAMPOS_PROTEGIDOS_PRECO
                )
                if mudou:
                    raise ValueError("Preço de versão publicada é imutável.")
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.versao_plano} - {self.get_periodicidade_display()} {self.moeda}"

    class Meta:
        base_manager_name = "all_objects"
        db_table = "preco_plano"
        ordering = ("versao_plano_id", "periodicidade", "moeda")
        verbose_name = _("Preço de plano")
        verbose_name_plural = _("Preços de plano")
        constraints = [
            models.UniqueConstraint(
                fields=("versao_plano", "periodicidade", "moeda"),
                name="preco_plano_unico_por_versao_periodicidade_moeda",
            ),
            models.CheckConstraint(condition=models.Q(moeda__regex=r"^[A-Z]{3}$"), name="preco_plano_moeda_iso_maiuscula"),
            models.CheckConstraint(condition=models.Q(valor_base_centavos__gte=0), name="preco_plano_valor_base_nao_negativo"),
            models.CheckConstraint(condition=models.Q(valor_seat_centavos__gte=0), name="preco_plano_valor_seat_nao_negativo"),
        ]


register(Plano)
register(VersaoPlano)
register(PrecoPlano)
