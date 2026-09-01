"""Catálogo global e contratos tenantizados de assinatura."""

from typing import cast

from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.api.base.models import (
    ActiveManagerMixin,
    Base,
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


class StatusAssinatura(models.IntegerChoices):
    PENDENTE = 10, _("Pendente")
    EM_TRIAL = 20, _("Em trial")
    ATIVA = 30, _("Ativa")
    ENCERRADA = 40, _("Encerrada")


class StatusFinanceiro(models.IntegerChoices):
    ISENTO = 10, _("Isento")
    PENDENTE = 20, _("Pendente")
    REGULAR = 30, _("Regular")
    INADIMPLENTE = 40, _("Inadimplente")
    IRRECUPERAVEL = 50, _("Irrecuperável")


class PoliticaTrial(models.IntegerChoices):
    SEM_FORMA_PAGAMENTO = 10, _("Sem forma de pagamento")
    COM_FORMA_PAGAMENTO = 20, _("Com forma de pagamento")


class StatusPropostaComercial(models.IntegerChoices):
    RASCUNHO = 10, _("Rascunho")
    ENVIADA = 20, _("Enviada")
    ACEITA = 30, _("Aceita")
    ATIVADA = 40, _("Ativada")
    RECUSADA = 50, _("Recusada")
    EXPIRADA = 60, _("Expirada")
    CANCELADA = 70, _("Cancelada")


class ModoAtivacaoProposta(models.IntegerChoices):
    PAGAMENTO = 10, _("Pagamento")
    CONTRATUAL = 20, _("Contratual")


class TipoAlteracaoAssinatura(models.IntegerChoices):
    UPGRADE_PLANO = 10, _("Upgrade de plano")
    AUMENTO_SEATS = 20, _("Aumento de seats")
    DOWNGRADE_PLANO = 30, _("Downgrade de plano")
    REDUCAO_SEATS = 40, _("Redução de seats")
    MUDANCA_PERIODICIDADE = 50, _("Mudança de periodicidade")
    FALLBACK_TRIAL = 60, _("Fallback de trial")


class MomentoAplicacaoAlteracaoAssinatura(models.IntegerChoices):
    IMEDIATA = 10, _("Imediata")
    PROXIMO_CICLO = 20, _("Próximo ciclo")


class StatusAlteracaoAssinatura(models.IntegerChoices):
    SOLICITADA = 10, _("Solicitada")
    AGUARDANDO_GATEWAY = 20, _("Aguardando gateway")
    CONFIRMADA = 30, _("Confirmada")
    FALHOU = 40, _("Falhou")
    CANCELADA = 50, _("Cancelada")


TRANSICOES_STATUS_ALTERACAO: dict[int, frozenset[int]] = {
    StatusAlteracaoAssinatura.SOLICITADA: frozenset(
        {
            StatusAlteracaoAssinatura.AGUARDANDO_GATEWAY,
            StatusAlteracaoAssinatura.CONFIRMADA,
            StatusAlteracaoAssinatura.FALHOU,
            StatusAlteracaoAssinatura.CANCELADA,
        }
    ),
    StatusAlteracaoAssinatura.AGUARDANDO_GATEWAY: frozenset(
        {
            StatusAlteracaoAssinatura.CONFIRMADA,
            StatusAlteracaoAssinatura.FALHOU,
            StatusAlteracaoAssinatura.CANCELADA,
        }
    ),
    StatusAlteracaoAssinatura.CONFIRMADA: frozenset({StatusAlteracaoAssinatura.CANCELADA}),
    StatusAlteracaoAssinatura.FALHOU: frozenset(),
    StatusAlteracaoAssinatura.CANCELADA: frozenset(),
}


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


CAMPOS_TRANSICAO_ASSINATURA = frozenset(
    {
        "versao_plano",
        "versao_plano_id",
        "proposta_comercial",
        "proposta_comercial_id",
        "status",
        "status_financeiro",
        "revisao",
        "periodicidade",
        "moeda",
        "valor_base_centavos",
        "valor_seat_centavos",
        "seats_inclusos",
        "seats_contratados",
        "expansao_automatica_seats",
        "recursos",
        "politica_trial",
        "trial_iniciado_em",
        "trial_termina_em",
        "periodo_atual_iniciado_em",
        "periodo_atual_termina_em",
        "carencia_pagamento_dias",
        "carencia_pagamento_iniciada_em",
        "carencia_pagamento_termina_em",
        "carencia_excesso_seats_dias",
        "carencia_excesso_seats_iniciada_em",
        "carencia_excesso_seats_termina_em",
        "cancelamento_agendado_para",
        "encerrada_em",
        "motivo_encerramento",
    }
)


class AssinaturasOrganizacaoQuerySet(BaseQuerySet):
    """Mantém snapshots de recursos completos também em escritas em lote."""

    def update(self, **kwargs):
        if set(kwargs) & CAMPOS_TRANSICAO_ASSINATURA:
            raise ValueError("Termos da assinatura só podem mudar por uma transição nominal.")
        if "recursos" in kwargs:
            kwargs["recursos"] = _materializar_recursos(kwargs["recursos"])
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
        if set(fields) & CAMPOS_TRANSICAO_ASSINATURA:
            raise ValueError("Termos da assinatura só podem mudar por uma transição nominal.")
        if "recursos" in fields:
            for objeto in objetos:
                objeto.recursos = _materializar_recursos(objeto.recursos)
        return super().bulk_update(objetos, fields, batch_size=batch_size)


STATUS_PROPOSTA_EDITAVEL = StatusPropostaComercial.RASCUNHO
CAMPOS_IDENTIDADE_PROPOSTA = frozenset({"organizacao", "created_at", "created_by"})
CAMPOS_PROCESSAMENTO_PROPOSTA = frozenset(
    {
        "status",
        "revisao",
        "enviada_em",
        "aceita_em",
        "aceita_por",
        "ativada_em",
        "ativada_por",
        "justificativa_ativacao",
        "recusada_em",
        "recusada_por",
        "expirada_em",
        "cancelada_em",
        "cancelada_por",
        "is_active",
        "last_modified_at",
    }
)
CAMPOS_TERMOS_PROPOSTA = frozenset(
    {
        "versao_plano_referencia",
        "modo_ativacao",
        "periodicidade",
        "moeda",
        "valor_base_centavos",
        "valor_seat_centavos",
        "seats_inclusos",
        "seats_contratados",
        "expansao_automatica_seats",
        "recursos",
        "carencia_pagamento_dias",
        "carencia_excesso_seats_dias",
        "valida_ate",
        "is_deleted",
    }
)

CAMPOS_TERMINAIS_PROPOSTA = (
    "enviada_em",
    "aceita_em",
    "aceita_por_id",
    "ativada_em",
    "ativada_por_id",
    "recusada_em",
    "recusada_por_id",
    "expirada_em",
    "cancelada_em",
    "cancelada_por_id",
)


def _validar_estado_inicial_proposta(proposta) -> None:
    estado_invalido = (
        proposta.status != StatusPropostaComercial.RASCUNHO
        or proposta.revisao != 1
        or proposta.justificativa_ativacao != ""
        or any(getattr(proposta, campo) is not None for campo in CAMPOS_TERMINAIS_PROPOSTA)
    )
    if estado_invalido:
        raise ValueError("O estado inicial da proposta deve ser rascunho na revisão 1, sem atores ou datas terminais.")


class PropostasComerciaisQuerySet(BaseQuerySet):
    """Mantem snapshots de recursos completos em todas as escritas ORM."""

    def create(self, **kwargs):
        proposta = self.model(**kwargs)
        _validar_estado_inicial_proposta(proposta)
        self._for_write = True
        proposta.save(force_insert=True, using=self.db)
        return proposta

    def update(self, **kwargs):
        if set(kwargs) & CAMPOS_IDENTIDADE_PROPOSTA:
            raise ValueError("Identidade da proposta é imutável.")
        if set(kwargs) & CAMPOS_PROCESSAMENTO_PROPOSTA:
            raise ValueError("Campos de processamento só podem mudar por transições nominais.")
        if set(kwargs) & CAMPOS_TERMOS_PROPOSTA and self.exclude(status=STATUS_PROPOSTA_EDITAVEL).exists():
            raise ValueError("Os termos de uma proposta enviada são imutáveis.")
        if "recursos" in kwargs:
            kwargs["recursos"] = _materializar_recursos(kwargs["recursos"])
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
            _validar_estado_inicial_proposta(objeto)
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
        if set(fields) & CAMPOS_IDENTIDADE_PROPOSTA:
            raise ValueError("Identidade da proposta é imutável.")
        if set(fields) & CAMPOS_PROCESSAMENTO_PROPOSTA:
            raise ValueError("Campos de processamento só podem mudar por transições nominais.")
        if set(fields) & CAMPOS_TERMOS_PROPOSTA:
            ids = [obj.pk for obj in objetos if obj.pk is not None]
            if self.model._base_manager.filter(pk__in=ids).exclude(status=STATUS_PROPOSTA_EDITAVEL).exists():
                raise ValueError("Os termos de uma proposta enviada são imutáveis.")
        if "recursos" in fields:
            for objeto in objetos:
                objeto.recursos = _materializar_recursos(objeto.recursos)
        return super().bulk_update(objetos, fields, batch_size=batch_size)


CAMPOS_PROCESSAMENTO_ALTERACAO = frozenset(
    {
        "status",
        "processada_em",
        "aplicada_em",
        "evento_gateway",
        "falha_codigo",
        "falha_mensagem",
        "revisao_aplicada",
        "revisao_observada",
        "ignorada_em",
        "last_modified_at",
    }
)


class AlteracoesAssinaturaQuerySet(BaseQuerySet):
    """Impede que pedido e snapshots históricos sejam reescritos pelo ORM."""

    def update(self, **kwargs):
        if "status" in kwargs:
            raise ValueError("Status de alteração só pode mudar por transições nominais.")
        if set(kwargs) - CAMPOS_PROCESSAMENTO_ALTERACAO:
            raise ValueError("Pedido e snapshots da alteração são imutáveis.")
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, batch_size=None):
        if "status" in fields:
            raise ValueError("Status de alteração só pode mudar por transições nominais.")
        if set(fields) - CAMPOS_PROCESSAMENTO_ALTERACAO:
            raise ValueError("Pedido e snapshots da alteração são imutáveis.")
        return super().bulk_update(tuple(objs), fields, batch_size=batch_size)

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
        if any(objeto.tipo == TipoAlteracaoAssinatura.FALLBACK_TRIAL for objeto in objetos):
            raise ValueError("Fallback de trial é reservado ao encerramento nominal do trial.")
        return super().bulk_create(
            objetos,
            batch_size=batch_size,
            ignore_conflicts=ignore_conflicts,
            update_conflicts=update_conflicts,
            update_fields=update_fields,
            unique_fields=unique_fields,
        )


AssinaturasOrganizacaoQuerySetManager = models.Manager.from_queryset(AssinaturasOrganizacaoQuerySet)
PropostasComerciaisQuerySetManager = models.Manager.from_queryset(PropostasComerciaisQuerySet)
AlteracoesAssinaturaQuerySetManager = models.Manager.from_queryset(AlteracoesAssinaturaQuerySet)


class AssinaturasOrganizacaoManager(ExcludeDeletedManagerMixin, DeferredFieldsManagerMixin, AssinaturasOrganizacaoQuerySetManager):
    pass


class TodasAssinaturasOrganizacaoManager(DeferredFieldsManagerMixin, AssinaturasOrganizacaoQuerySetManager):
    pass


class AssinaturasOrganizacaoAtivasManager(
    ActiveManagerMixin,
    ExcludeDeletedManagerMixin,
    DeferredFieldsManagerMixin,
    AssinaturasOrganizacaoQuerySetManager,
):
    pass


class PropostasComerciaisManager(ExcludeDeletedManagerMixin, DeferredFieldsManagerMixin, PropostasComerciaisQuerySetManager):
    pass


class TodasPropostasComerciaisManager(DeferredFieldsManagerMixin, PropostasComerciaisQuerySetManager):
    pass


class PropostasComerciaisAtivasManager(
    ActiveManagerMixin,
    ExcludeDeletedManagerMixin,
    DeferredFieldsManagerMixin,
    PropostasComerciaisQuerySetManager,
):
    pass


class AlteracoesAssinaturaManager(ExcludeDeletedManagerMixin, DeferredFieldsManagerMixin, AlteracoesAssinaturaQuerySetManager):
    pass


class TodasAlteracoesAssinaturaManager(DeferredFieldsManagerMixin, AlteracoesAssinaturaQuerySetManager):
    pass


class AlteracoesAssinaturaAtivasManager(
    ActiveManagerMixin,
    ExcludeDeletedManagerMixin,
    DeferredFieldsManagerMixin,
    AlteracoesAssinaturaQuerySetManager,
):
    pass


def _condicao_estado_datas_proposta() -> models.Q:
    sem_ativacao_ou_terminal = models.Q(
        ativada_em__isnull=True,
        ativada_por__isnull=True,
        justificativa_ativacao="",
        recusada_em__isnull=True,
        recusada_por__isnull=True,
        expirada_em__isnull=True,
        cancelada_em__isnull=True,
        cancelada_por__isnull=True,
    )
    rascunho = (
        models.Q(
            status=StatusPropostaComercial.RASCUNHO,
            enviada_em__isnull=True,
            aceita_em__isnull=True,
            aceita_por__isnull=True,
        )
        & sem_ativacao_ou_terminal
    )
    enviada = (
        models.Q(
            status=StatusPropostaComercial.ENVIADA,
            enviada_em__isnull=False,
            enviada_em__lt=models.F("valida_ate"),
            aceita_em__isnull=True,
            aceita_por__isnull=True,
        )
        & sem_ativacao_ou_terminal
    )
    aceita = (
        models.Q(
            status=StatusPropostaComercial.ACEITA,
            enviada_em__isnull=False,
            aceita_em__isnull=False,
            aceita_por__isnull=False,
            enviada_em__lte=models.F("aceita_em"),
            aceita_em__lt=models.F("valida_ate"),
        )
        & sem_ativacao_ou_terminal
    )
    ativada = models.Q(
        status=StatusPropostaComercial.ATIVADA,
        modo_ativacao=ModoAtivacaoProposta.CONTRATUAL,
        enviada_em__isnull=False,
        aceita_em__isnull=False,
        aceita_por__isnull=False,
        ativada_em__isnull=False,
        ativada_por__isnull=False,
        enviada_em__lte=models.F("aceita_em"),
        aceita_em__lte=models.F("ativada_em"),
        ativada_em__lt=models.F("valida_ate"),
        recusada_em__isnull=True,
        recusada_por__isnull=True,
        expirada_em__isnull=True,
        cancelada_em__isnull=True,
        cancelada_por__isnull=True,
    ) & ~models.Q(justificativa_ativacao="")
    recusada = models.Q(
        status=StatusPropostaComercial.RECUSADA,
        enviada_em__isnull=False,
        aceita_em__isnull=True,
        aceita_por__isnull=True,
        ativada_em__isnull=True,
        ativada_por__isnull=True,
        justificativa_ativacao="",
        recusada_em__isnull=False,
        recusada_por__isnull=False,
        enviada_em__lte=models.F("recusada_em"),
        recusada_em__lt=models.F("valida_ate"),
        expirada_em__isnull=True,
        cancelada_em__isnull=True,
        cancelada_por__isnull=True,
    )
    expirada = models.Q(
        status=StatusPropostaComercial.EXPIRADA,
        enviada_em__isnull=False,
        ativada_em__isnull=True,
        ativada_por__isnull=True,
        justificativa_ativacao="",
        recusada_em__isnull=True,
        recusada_por__isnull=True,
        expirada_em__isnull=False,
        enviada_em__lte=models.F("expirada_em"),
        valida_ate__lte=models.F("expirada_em"),
        cancelada_em__isnull=True,
        cancelada_por__isnull=True,
    ) & (
        models.Q(aceita_em__isnull=True, aceita_por__isnull=True)
        | models.Q(
            aceita_em__isnull=False,
            aceita_por__isnull=False,
            enviada_em__lte=models.F("aceita_em"),
            aceita_em__lte=models.F("expirada_em"),
        )
    )
    cancelada = models.Q(
        status=StatusPropostaComercial.CANCELADA,
        ativada_em__isnull=True,
        ativada_por__isnull=True,
        justificativa_ativacao="",
        recusada_em__isnull=True,
        recusada_por__isnull=True,
        expirada_em__isnull=True,
        cancelada_em__isnull=False,
        cancelada_por__isnull=False,
    ) & (
        models.Q(enviada_em__isnull=True, aceita_em__isnull=True, aceita_por__isnull=True)
        | models.Q(
            enviada_em__isnull=False,
            enviada_em__lte=models.F("cancelada_em"),
            aceita_em__isnull=True,
            aceita_por__isnull=True,
        )
        | models.Q(
            enviada_em__isnull=False,
            aceita_em__isnull=False,
            aceita_por__isnull=False,
            enviada_em__lte=models.F("aceita_em"),
            aceita_em__lte=models.F("cancelada_em"),
        )
    )
    return rascunho | enviada | aceita | ativada | recusada | expirada | cancelada


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


class PropostaComercial(Base):
    """Snapshot negociado especificamente para uma organizacao."""

    versao_plano_referencia = models.ForeignKey(
        VersaoPlano,
        verbose_name=_("versao do plano de referencia"),
        on_delete=models.PROTECT,
        related_name="propostas_comerciais",
        null=True,
        blank=True,
    )
    status = models.PositiveSmallIntegerField(
        _("status"),
        choices=StatusPropostaComercial.choices,
        default=StatusPropostaComercial.RASCUNHO,
    )
    modo_ativacao = models.PositiveSmallIntegerField(_("modo de ativacao"), choices=ModoAtivacaoProposta.choices)
    revisao = models.PositiveSmallIntegerField(_("revisao"), default=1)
    periodicidade = models.PositiveSmallIntegerField(_("periodicidade"), choices=Periodicidade.choices)
    moeda = models.CharField(
        _("moeda"),
        max_length=3,
        validators=[RegexValidator(regex=r"^[A-Z]{3}$", message=_("Informe tres letras maiusculas."))],
    )
    valor_base_centavos = models.PositiveBigIntegerField(_("valor base em centavos"))
    valor_seat_centavos = models.PositiveBigIntegerField(_("valor por seat em centavos"))
    seats_inclusos = models.PositiveSmallIntegerField(_("seats inclusos"), default=0)
    seats_contratados = models.PositiveSmallIntegerField(_("seats contratados"), default=0)
    expansao_automatica_seats = models.BooleanField(_("expansao automatica de seats"), default=False)
    recursos = models.JSONField(_("recursos"), default=dict)
    carencia_pagamento_dias = models.PositiveSmallIntegerField(_("carencia de pagamento em dias"), default=0)
    carencia_excesso_seats_dias = models.PositiveSmallIntegerField(_("carencia de excesso de seats em dias"), default=0)
    valida_ate = models.DateTimeField(_("valida ate"))
    enviada_em = models.DateTimeField(_("enviada em"), null=True, blank=True)
    aceita_em = models.DateTimeField(_("aceita em"), null=True, blank=True)
    aceita_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("aceita por"),
        on_delete=models.PROTECT,
        related_name="propostas_comerciais_aceitas",
        null=True,
        blank=True,
    )
    ativada_em = models.DateTimeField(_("ativada em"), null=True, blank=True)
    ativada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("ativada por"),
        on_delete=models.PROTECT,
        related_name="propostas_comerciais_ativadas",
        null=True,
        blank=True,
    )
    justificativa_ativacao = models.TextField(_("justificativa da ativacao"), blank=True, default="")
    recusada_em = models.DateTimeField(_("recusada em"), null=True, blank=True)
    recusada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("recusada por"),
        on_delete=models.PROTECT,
        related_name="propostas_comerciais_recusadas",
        null=True,
        blank=True,
    )
    expirada_em = models.DateTimeField(_("expirada em"), null=True, blank=True)
    cancelada_em = models.DateTimeField(_("cancelada em"), null=True, blank=True)
    cancelada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("cancelada por"),
        on_delete=models.PROTECT,
        related_name="propostas_comerciais_canceladas",
        null=True,
        blank=True,
    )

    objects = PropostasComerciaisManager()  # type: ignore[misc, assignment]
    all_objects = TodasPropostasComerciaisManager()  # type: ignore[misc, assignment]
    ativos = PropostasComerciaisAtivasManager()  # type: ignore[misc, assignment]

    def save(self, *args, **kwargs):
        campos_atualizados = set(kwargs["update_fields"]) if kwargs.get("update_fields") is not None else None
        if self._state.adding:
            _validar_estado_inicial_proposta(self)
        if self._state.adding or campos_atualizados is None or "recursos" in campos_atualizados:
            self.recursos = _materializar_recursos(self.recursos)
        if self.pk is not None:
            anterior = dict(type(self)._base_manager.using(self._state.db).filter(pk=self.pk).values().first() or {})
            if anterior:
                for campo in CAMPOS_IDENTIDADE_PROPOSTA:
                    field = cast(models.Field, self._meta.get_field(campo))
                    if anterior[field.attname] != getattr(self, field.attname):
                        raise ValueError("Identidade da proposta é imutável.")
                if anterior["status"] != STATUS_PROPOSTA_EDITAVEL:
                    for campo in CAMPOS_TERMOS_PROPOSTA:
                        field = cast(models.Field, self._meta.get_field(campo))
                        if anterior[field.attname] != getattr(self, field.attname):
                            raise ValueError("Os termos de uma proposta enviada são imutáveis.")
                for campo in CAMPOS_PROCESSAMENTO_PROPOSTA:
                    field = cast(models.Field, self._meta.get_field(campo))
                    if anterior[field.attname] != getattr(self, field.attname):
                        raise ValueError("Transições de proposta só podem ser executadas pelos casos de uso nominais.")
        return super().save(*args, **kwargs)

    def _salvar_transicao(self, *, update_fields: list[str]):
        """Persiste uma transição já validada pelo caso de uso nominal."""
        if "recursos" in update_fields:
            self.recursos = _materializar_recursos(self.recursos)
        return super().save(update_fields=update_fields)

    def clean(self):
        super().clean()
        self.recursos = _materializar_recursos(self.recursos)

    def __str__(self):
        return f"Proposta #{self.pk or 'nova'} @ {self.organizacao_id}"

    class Meta:
        base_manager_name = "all_objects"
        db_table = "proposta_comercial"
        ordering = ("-created_at", "-pk")
        verbose_name = _("Proposta comercial")
        verbose_name_plural = _("Propostas comerciais")
        permissions = (("activate_contractual_propostacomercial", _("Pode ativar proposta comercial contratual")),)
        indexes = [models.Index(fields=("organizacao", "status", "valida_ate"), name="proposta_org_status_val_idx")]
        constraints = [
            models.CheckConstraint(condition=models.Q(status__in=StatusPropostaComercial.values), name="proposta_status_dominio"),
            models.CheckConstraint(condition=models.Q(modo_ativacao__in=ModoAtivacaoProposta.values), name="proposta_modo_dominio"),
            models.CheckConstraint(condition=models.Q(periodicidade__in=Periodicidade.values), name="proposta_periodicidade_dominio"),
            models.CheckConstraint(condition=models.Q(revisao__gte=1), name="proposta_revisao_positiva"),
            models.CheckConstraint(condition=models.Q(moeda__regex=r"^[A-Z]{3}$"), name="proposta_moeda_iso_maiuscula"),
            models.CheckConstraint(condition=models.Q(valor_base_centavos__gte=0), name="proposta_valor_base_nao_negativo"),
            models.CheckConstraint(condition=models.Q(valor_seat_centavos__gte=0), name="proposta_valor_seat_nao_negativo"),
            models.CheckConstraint(condition=models.Q(seats_inclusos__gte=0), name="proposta_seats_inclusos_nao_negativo"),
            models.CheckConstraint(condition=models.Q(seats_contratados__gte=0), name="proposta_seats_contratados_nao_negativo"),
            models.CheckConstraint(condition=models.Q(carencia_pagamento_dias__gte=0), name="proposta_carencia_pagamento_nao_negativa"),
            models.CheckConstraint(condition=models.Q(carencia_excesso_seats_dias__gte=0), name="proposta_carencia_seats_nao_negativa"),
            models.CheckConstraint(
                condition=_condicao_estado_datas_proposta(),
                name="proposta_estado_datas_coerente",
            ),
        ]


class AssinaturaOrganizacao(Base):
    """Snapshot corrente dos termos contratuais de uma organização."""

    versao_plano = models.ForeignKey(
        VersaoPlano,
        verbose_name=_("versão do plano"),
        on_delete=models.PROTECT,
        related_name="assinaturas_organizacoes",
        null=True,
        blank=True,
    )
    proposta_comercial = models.ForeignKey(
        PropostaComercial,
        verbose_name=_("proposta comercial"),
        on_delete=models.PROTECT,
        related_name="assinaturas_organizacoes",
        null=True,
        blank=True,
    )
    status = models.PositiveSmallIntegerField(_("status"), choices=StatusAssinatura.choices)
    status_financeiro = models.PositiveSmallIntegerField(_("status financeiro"), choices=StatusFinanceiro.choices)
    revisao = models.PositiveSmallIntegerField(_("revisão"), default=1)
    periodicidade = models.PositiveSmallIntegerField(_("periodicidade"), choices=Periodicidade.choices)
    moeda = models.CharField(
        _("moeda"),
        max_length=3,
        validators=[RegexValidator(regex=r"^[A-Z]{3}$", message=_("Informe três letras maiúsculas."))],
    )
    valor_base_centavos = models.PositiveBigIntegerField(_("valor base em centavos"))
    valor_seat_centavos = models.PositiveBigIntegerField(_("valor por seat em centavos"))
    seats_inclusos = models.PositiveSmallIntegerField(_("seats inclusos"), default=0)
    seats_contratados = models.PositiveSmallIntegerField(_("seats contratados"), default=0)
    expansao_automatica_seats = models.BooleanField(_("expansão automática de seats"), default=False)
    recursos = models.JSONField(_("recursos"), default=dict)
    politica_trial = models.PositiveSmallIntegerField(_("política de trial"), choices=PoliticaTrial.choices, null=True, blank=True)
    trial_iniciado_em = models.DateTimeField(_("trial iniciado em"), null=True, blank=True)
    trial_termina_em = models.DateTimeField(_("trial termina em"), null=True, blank=True)
    periodo_atual_iniciado_em = models.DateTimeField(_("período atual iniciado em"), null=True, blank=True)
    periodo_atual_termina_em = models.DateTimeField(_("período atual termina em"), null=True, blank=True)
    carencia_pagamento_dias = models.PositiveSmallIntegerField(_("carência de pagamento em dias"), default=0)
    carencia_pagamento_iniciada_em = models.DateTimeField(_("carência de pagamento iniciada em"), null=True, blank=True)
    carencia_pagamento_termina_em = models.DateTimeField(_("carência de pagamento termina em"), null=True, blank=True)
    carencia_excesso_seats_dias = models.PositiveSmallIntegerField(_("carência de excesso de seats em dias"), default=0)
    carencia_excesso_seats_iniciada_em = models.DateTimeField(_("carência de excesso de seats iniciada em"), null=True, blank=True)
    carencia_excesso_seats_termina_em = models.DateTimeField(_("carência de excesso de seats termina em"), null=True, blank=True)
    cancelamento_agendado_para = models.DateTimeField(_("cancelamento agendado para"), null=True, blank=True)
    encerrada_em = models.DateTimeField(_("encerrada em"), null=True, blank=True)
    motivo_encerramento = models.CharField(_("motivo do encerramento"), max_length=100, null=True, blank=True)
    chave_idempotencia = models.CharField(_("chave de idempotência"), max_length=120)

    objects = AssinaturasOrganizacaoManager()  # type: ignore[misc, assignment]
    all_objects = TodasAssinaturasOrganizacaoManager()  # type: ignore[misc, assignment]
    ativos = AssinaturasOrganizacaoAtivasManager()  # type: ignore[misc, assignment]

    def save(self, *args, **kwargs):
        campos_atualizados = set(kwargs["update_fields"]) if kwargs.get("update_fields") is not None else None
        if not self._state.adding and not getattr(self, "_transicao_nominal_em_curso", False):
            if campos_atualizados is None:
                campos_persistidos = {campo.attname for campo in self._meta.concrete_fields if campo.name in CAMPOS_TRANSICAO_ASSINATURA}
                anterior = type(self)._base_manager.filter(pk=self.pk).values(*campos_persistidos).first()
                mudou_transicao = anterior is not None and any(anterior[campo] != getattr(self, campo) for campo in campos_persistidos)
            else:
                mudou_transicao = bool(campos_atualizados & CAMPOS_TRANSICAO_ASSINATURA)
            if mudou_transicao:
                raise ValueError("Termos da assinatura só podem mudar por uma transição nominal.")
        if self._state.adding or campos_atualizados is None or "recursos" in campos_atualizados:
            self.recursos = _materializar_recursos(self.recursos)
        return super().save(*args, **kwargs)

    def _salvar_transicao(self, *args, **kwargs):
        """Persiste uma transição validada por um caso de uso nominal."""
        if self._state.adding:
            raise ValueError("A criação de assinatura usa o caminho nominal próprio.")
        self._transicao_nominal_em_curso = True
        try:
            return self.save(*args, **kwargs)
        finally:
            del self._transicao_nominal_em_curso

    def clean(self):
        super().clean()
        self.recursos = _materializar_recursos(self.recursos)

    @property
    def seats_cobrados(self) -> int:
        return max(0, self.seats_contratados - self.seats_inclusos)

    @property
    def total_centavos(self) -> int:
        return self.valor_base_centavos + self.seats_cobrados * self.valor_seat_centavos

    def __str__(self):
        return f"Assinatura #{self.pk or 'nova'} @ {self.organizacao_id}"

    class Meta:
        base_manager_name = "all_objects"
        db_table = "assinatura_organizacao"
        ordering = ("-created_at", "-pk")
        verbose_name = _("Assinatura da organização")
        verbose_name_plural = _("Assinaturas das organizações")
        indexes = [models.Index(fields=("organizacao", "status"), name="assinatura_org_status_idx")]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(versao_plano__isnull=False, proposta_comercial__isnull=True)
                    | models.Q(versao_plano__isnull=True, proposta_comercial__isnull=False)
                ),
                name="assinatura_origem_xor",
            ),
            models.UniqueConstraint(
                fields=("organizacao", "chave_idempotencia"),
                name="assinatura_org_chave_idempotencia_unica",
            ),
            models.UniqueConstraint(
                fields=("organizacao",),
                condition=models.Q(status__in=(StatusAssinatura.PENDENTE, StatusAssinatura.EM_TRIAL, StatusAssinatura.ATIVA)),
                name="assinatura_org_contrato_corrente_unico",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status=StatusAssinatura.ENCERRADA, encerrada_em__isnull=False, motivo_encerramento__isnull=False)
                    & ~models.Q(motivo_encerramento="")
                )
                | (~models.Q(status=StatusAssinatura.ENCERRADA) & models.Q(encerrada_em__isnull=True, motivo_encerramento__isnull=True)),
                name="assinatura_encerramento_coerente",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status=StatusAssinatura.EM_TRIAL,
                        politica_trial__isnull=False,
                        trial_iniciado_em__isnull=False,
                        trial_termina_em__isnull=False,
                        trial_iniciado_em__lt=models.F("trial_termina_em"),
                    )
                    | (
                        models.Q(status__in=(StatusAssinatura.ATIVA, StatusAssinatura.ENCERRADA))
                        & (
                            models.Q(politica_trial__isnull=True, trial_iniciado_em__isnull=True, trial_termina_em__isnull=True)
                            | models.Q(
                                politica_trial__isnull=False,
                                trial_iniciado_em__isnull=False,
                                trial_termina_em__isnull=False,
                                trial_iniciado_em__lt=models.F("trial_termina_em"),
                            )
                        )
                    )
                    | models.Q(
                        status=StatusAssinatura.PENDENTE,
                        politica_trial__isnull=True,
                        trial_iniciado_em__isnull=True,
                        trial_termina_em__isnull=True,
                    )
                ),
                name="assinatura_trial_coerente",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(periodo_atual_iniciado_em__isnull=True, periodo_atual_termina_em__isnull=True)
                    | models.Q(
                        periodo_atual_iniciado_em__isnull=False,
                        periodo_atual_termina_em__isnull=False,
                        periodo_atual_iniciado_em__lt=models.F("periodo_atual_termina_em"),
                    )
                ),
                name="assinatura_periodo_atual_coerente",
            ),
            models.CheckConstraint(
                condition=models.Q(cancelamento_agendado_para__isnull=True)
                | models.Q(
                    status=StatusAssinatura.ATIVA,
                    periodo_atual_termina_em__isnull=False,
                    cancelamento_agendado_para=models.F("periodo_atual_termina_em"),
                ),
                name="assinatura_cancelamento_agendado_coerente",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(carencia_pagamento_iniciada_em__isnull=True, carencia_pagamento_termina_em__isnull=True)
                    | models.Q(
                        carencia_pagamento_iniciada_em__isnull=False,
                        carencia_pagamento_termina_em__isnull=False,
                        carencia_pagamento_iniciada_em__lte=models.F("carencia_pagamento_termina_em"),
                    )
                ),
                name="assinatura_carencia_pagamento_coerente",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(carencia_excesso_seats_iniciada_em__isnull=True, carencia_excesso_seats_termina_em__isnull=True)
                    | models.Q(
                        carencia_excesso_seats_iniciada_em__isnull=False,
                        carencia_excesso_seats_termina_em__isnull=False,
                        carencia_excesso_seats_iniciada_em__lte=models.F("carencia_excesso_seats_termina_em"),
                    )
                ),
                name="assinatura_carencia_seats_coerente",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status=StatusAssinatura.PENDENTE, status_financeiro=StatusFinanceiro.PENDENTE)
                    | models.Q(status=StatusAssinatura.EM_TRIAL, status_financeiro=StatusFinanceiro.ISENTO)
                    | models.Q(
                        status=StatusAssinatura.ATIVA,
                        status_financeiro__in=(
                            StatusFinanceiro.ISENTO,
                            StatusFinanceiro.REGULAR,
                            StatusFinanceiro.INADIMPLENTE,
                            StatusFinanceiro.IRRECUPERAVEL,
                        ),
                    )
                    | models.Q(status=StatusAssinatura.ENCERRADA)
                ),
                name="assinatura_status_financeiro_coerente",
            ),
            models.CheckConstraint(condition=models.Q(moeda__regex=r"^[A-Z]{3}$"), name="assinatura_moeda_iso_maiuscula"),
            models.CheckConstraint(condition=models.Q(revisao__gte=1), name="assinatura_revisao_positiva"),
            models.CheckConstraint(condition=models.Q(status__in=StatusAssinatura.values), name="assinatura_status_dominio"),
            models.CheckConstraint(condition=models.Q(status_financeiro__in=StatusFinanceiro.values), name="assinatura_financeiro_dominio"),
            models.CheckConstraint(condition=models.Q(periodicidade__in=Periodicidade.values), name="assinatura_periodicidade_dominio"),
            models.CheckConstraint(
                condition=models.Q(politica_trial__isnull=True) | models.Q(politica_trial__in=PoliticaTrial.values),
                name="assinatura_politica_trial_dominio",
            ),
        ]


class AlteracaoAssinatura(Base):
    """Pedido imutável e metadados evolutivos de uma mudança contratual."""

    assinatura = models.ForeignKey(
        AssinaturaOrganizacao,
        verbose_name=_("assinatura"),
        on_delete=models.PROTECT,
        related_name="alteracoes",
    )
    tipo = models.PositiveSmallIntegerField(_("tipo"), choices=TipoAlteracaoAssinatura.choices)
    momento_aplicacao = models.PositiveSmallIntegerField(_("momento de aplicação"), choices=MomentoAplicacaoAlteracaoAssinatura.choices)
    status = models.PositiveSmallIntegerField(_("status"), choices=StatusAlteracaoAssinatura.choices, default=StatusAlteracaoAssinatura.SOLICITADA)
    revisao_esperada = models.PositiveSmallIntegerField(_("revisão esperada"))
    chave_idempotencia = models.CharField(_("chave de idempotência"), max_length=120)
    solicitada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("solicitada por"),
        on_delete=models.PROTECT,
        related_name="alteracoes_assinatura_solicitadas",
        null=True,
        blank=True,
    )
    pedido = models.JSONField(_("pedido"))
    snapshot_anterior = models.JSONField(_("snapshot anterior"))
    snapshot_pretendido = models.JSONField(_("snapshot pretendido"))
    aplicar_em = models.DateTimeField(_("aplicar em"), null=True, blank=True)
    processada_em = models.DateTimeField(_("processada em"), null=True, blank=True)
    aplicada_em = models.DateTimeField(_("aplicada em"), null=True, blank=True)
    evento_gateway = models.CharField(_("evento do gateway"), max_length=255, null=True, blank=True)
    falha_codigo = models.CharField(_("código da falha"), max_length=100, null=True, blank=True)
    falha_mensagem = models.CharField(_("mensagem da falha"), max_length=500, null=True, blank=True)
    revisao_aplicada = models.PositiveSmallIntegerField(_("revisão aplicada"), null=True, blank=True)
    revisao_observada = models.PositiveSmallIntegerField(_("revisão observada"), null=True, blank=True)
    ignorada_em = models.DateTimeField(_("ignorada em"), null=True, blank=True)

    objects = AlteracoesAssinaturaManager()  # type: ignore[misc, assignment]
    all_objects = TodasAlteracoesAssinaturaManager()  # type: ignore[misc, assignment]
    ativos = AlteracoesAssinaturaAtivasManager()  # type: ignore[misc, assignment]

    def save(self, *args, **kwargs):
        permitir_fallback_trial = kwargs.pop("_permitir_fallback_trial", False)
        if self._state.adding and self.tipo == TipoAlteracaoAssinatura.FALLBACK_TRIAL and not permitir_fallback_trial:
            raise ValueError("Fallback de trial é reservado ao encerramento nominal do trial.")
        if self.pk is not None:
            anterior = dict(type(self)._base_manager.using(self._state.db).filter(pk=self.pk).values().first() or {})
            if anterior:
                campos_imutaveis = {
                    field.name for field in self._meta.concrete_fields if field.name not in CAMPOS_PROCESSAMENTO_ALTERACAO and not field.primary_key
                }
                mudou = any(
                    anterior[field.attname] != getattr(self, field.attname) for field in self._meta.concrete_fields if field.name in campos_imutaveis
                )
                if mudou:
                    raise ValueError("Pedido e snapshots da alteração são imutáveis.")
                status_anterior = cast(int, anterior["status"])
                if status_anterior != self.status and self.status not in TRANSICOES_STATUS_ALTERACAO.get(status_anterior, frozenset()):
                    raise ValueError("Transição inválida de status da alteração.")
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"Alteração #{self.pk or 'nova'} da assinatura {self.assinatura_id}"

    class Meta:
        base_manager_name = "all_objects"
        db_table = "alteracao_assinatura"
        ordering = ("-created_at", "-pk")
        verbose_name = _("Alteração de assinatura")
        verbose_name_plural = _("Alterações de assinatura")
        indexes = [models.Index(fields=("organizacao", "assinatura", "status"), name="alteracao_org_ass_status_idx")]
        constraints = [
            models.UniqueConstraint(
                fields=("organizacao", "chave_idempotencia"),
                name="alteracao_org_chave_idempotencia_unica",
            ),
            models.CheckConstraint(condition=models.Q(revisao_esperada__gte=1), name="alteracao_revisao_esperada_positiva"),
            models.CheckConstraint(
                condition=(
                    models.Q(momento_aplicacao=MomentoAplicacaoAlteracaoAssinatura.IMEDIATA, aplicar_em__isnull=True)
                    | models.Q(
                        momento_aplicacao=MomentoAplicacaoAlteracaoAssinatura.PROXIMO_CICLO,
                        aplicar_em__isnull=False,
                        aplicar_em__gt=models.F("created_at"),
                    )
                ),
                name="alteracao_momento_data_coerente",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(aplicada_em__isnull=True, revisao_aplicada__isnull=True)
                    | models.Q(
                        aplicada_em__isnull=False,
                        revisao_aplicada__isnull=False,
                        status=StatusAlteracaoAssinatura.CONFIRMADA,
                        revisao_aplicada=models.F("revisao_esperada") + 1,
                    )
                ),
                name="alteracao_aplicacao_coerente",
            ),
            models.CheckConstraint(
                condition=models.Q(ignorada_em__isnull=True, revisao_observada__isnull=True)
                | models.Q(
                    ignorada_em__isnull=False,
                    revisao_observada__isnull=False,
                    processada_em__isnull=False,
                    aplicada_em__isnull=True,
                    status=StatusAlteracaoAssinatura.CONFIRMADA,
                ),
                name="alteracao_evento_ignorado_coerente",
            ),
            models.CheckConstraint(condition=models.Q(tipo__in=TipoAlteracaoAssinatura.values), name="alteracao_tipo_dominio"),
            models.CheckConstraint(
                condition=(
                    ~models.Q(tipo=TipoAlteracaoAssinatura.FALLBACK_TRIAL)
                    | models.Q(
                        momento_aplicacao=MomentoAplicacaoAlteracaoAssinatura.IMEDIATA,
                        solicitada_por__isnull=True,
                    )
                ),
                name="alteracao_fallback_trial_reservado",
            ),
            models.CheckConstraint(
                condition=models.Q(momento_aplicacao__in=MomentoAplicacaoAlteracaoAssinatura.values),
                name="alteracao_momento_dominio",
            ),
            models.CheckConstraint(condition=models.Q(status__in=StatusAlteracaoAssinatura.values), name="alteracao_status_dominio"),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status__in=(StatusAlteracaoAssinatura.SOLICITADA, StatusAlteracaoAssinatura.AGUARDANDO_GATEWAY),
                        processada_em__isnull=True,
                        aplicada_em__isnull=True,
                        evento_gateway__isnull=True,
                        falha_codigo__isnull=True,
                        falha_mensagem__isnull=True,
                        revisao_aplicada__isnull=True,
                        revisao_observada__isnull=True,
                        ignorada_em__isnull=True,
                    )
                    | (
                        models.Q(
                            status=StatusAlteracaoAssinatura.CONFIRMADA,
                            processada_em__isnull=False,
                            falha_codigo__isnull=True,
                            falha_mensagem__isnull=True,
                        )
                        & (
                            models.Q(
                                aplicada_em__isnull=False,
                                revisao_aplicada__isnull=False,
                                revisao_observada__isnull=True,
                                ignorada_em__isnull=True,
                            )
                            | models.Q(
                                momento_aplicacao=MomentoAplicacaoAlteracaoAssinatura.PROXIMO_CICLO,
                                aplicada_em__isnull=True,
                                revisao_aplicada__isnull=True,
                                revisao_observada__isnull=True,
                                ignorada_em__isnull=True,
                            )
                            | models.Q(
                                aplicada_em__isnull=True,
                                revisao_aplicada__isnull=True,
                                revisao_observada__isnull=False,
                                ignorada_em__isnull=False,
                            )
                        )
                    )
                    | (
                        models.Q(
                            status=StatusAlteracaoAssinatura.FALHOU,
                            processada_em__isnull=False,
                            falha_codigo__isnull=False,
                            falha_mensagem__isnull=False,
                            aplicada_em__isnull=True,
                            revisao_aplicada__isnull=True,
                            revisao_observada__isnull=True,
                            ignorada_em__isnull=True,
                        )
                        & ~models.Q(falha_codigo="")
                        & ~models.Q(falha_mensagem="")
                    )
                    | models.Q(
                        status=StatusAlteracaoAssinatura.CANCELADA,
                        processada_em__isnull=False,
                        falha_codigo__isnull=True,
                        falha_mensagem__isnull=True,
                        aplicada_em__isnull=True,
                        revisao_aplicada__isnull=True,
                        revisao_observada__isnull=True,
                        ignorada_em__isnull=True,
                    )
                ),
                name="alteracao_processamento_coerente",
            ),
        ]


register(Plano)
register(VersaoPlano)
register(PrecoPlano)
register(PropostaComercial)
register(AssinaturaOrganizacao)
register(AlteracaoAssinatura)
