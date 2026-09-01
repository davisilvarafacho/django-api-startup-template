"""Politica pura de acesso derivada do snapshot contratual e da ocupacao."""

from dataclasses import dataclass
from datetime import datetime

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.assinaturas.models import AssinaturaOrganizacao, StatusAssinatura, StatusFinanceiro
from apps.assinaturas.subscriptions import UtilizacaoSeats


class StatusAcesso(models.IntegerChoices):
    LIBERADO = 10, _("Liberado")
    EM_CARENCIA = 20, _("Em carencia")
    RESTRITO = 30, _("Restrito")


class MotivoRestricao(models.TextChoices):
    PAYMENT_GRACE_PERIOD = "payment_grace_period", _("Carencia financeira em andamento")
    SEAT_OVERAGE_GRACE_PERIOD = "seat_overage_grace_period", _("Carencia de excesso de seats em andamento")
    PAYMENT_GRACE_PERIOD_EXPIRED = "payment_grace_period_expired", _("Carencia financeira expirada")
    SEAT_OVERAGE_GRACE_PERIOD_EXPIRED = "seat_overage_grace_period_expired", _("Carencia de excesso de seats expirada")
    SUBSCRIPTION_PENDING = "subscription_pending", _("Assinatura pendente")
    TRIAL_EXPIRED = "trial_expired", _("Trial expirado")
    SUBSCRIPTION_ENDED = "subscription_ended", _("Assinatura encerrada")


@dataclass(frozen=True, slots=True)
class SituacaoAcesso:
    status: StatusAcesso
    motivos: tuple[MotivoRestricao, ...]
    regularizar_ate: datetime | None

    def __post_init__(self):
        if not isinstance(self.status, StatusAcesso):
            raise ValueError("Status da situacao de acesso deve ser um StatusAcesso concreto.")
        if type(self.motivos) is not tuple or not all(isinstance(motivo, MotivoRestricao) for motivo in self.motivos):
            raise ValueError("Motivos da situacao de acesso devem ser uma tupla tipada.")
        if self.regularizar_ate is not None and (not isinstance(self.regularizar_ate, datetime) or not timezone.is_aware(self.regularizar_ate)):
            raise ValueError("Prazo de regularizacao deve ser um datetime consciente de fuso.")


class PoliticaAcessoAssinatura:
    """Avalia acesso sem consultar banco ou alterar o contrato."""

    @classmethod
    def avaliar(
        cls,
        assinatura: AssinaturaOrganizacao,
        utilizacao: UtilizacaoSeats,
        agora: datetime,
    ) -> SituacaoAcesso:
        if not isinstance(assinatura, AssinaturaOrganizacao):
            raise ValueError("Politica de acesso exige uma AssinaturaOrganizacao.")
        cls._validar_utilizacao(utilizacao)
        if not isinstance(agora, datetime) or not timezone.is_aware(agora):
            raise ValueError("A avaliacao de acesso exige um datetime consciente de fuso.")

        status = StatusAssinatura(assinatura.status)
        if status == StatusAssinatura.ENCERRADA:
            return cls._restrito(MotivoRestricao.SUBSCRIPTION_ENDED)
        if status == StatusAssinatura.PENDENTE:
            return cls._restrito(MotivoRestricao.SUBSCRIPTION_PENDING)
        if status == StatusAssinatura.EM_TRIAL:
            termina_em = assinatura.trial_termina_em
            if termina_em is None or not timezone.is_aware(termina_em) or agora >= termina_em:
                return cls._restrito(MotivoRestricao.TRIAL_EXPIRED, prazo=termina_em if termina_em and timezone.is_aware(termina_em) else None)

        motivos: list[MotivoRestricao] = []
        prazos: list[datetime] = []
        restrito = False

        if status == StatusAssinatura.ATIVA and assinatura.status_financeiro in (
            StatusFinanceiro.INADIMPLENTE,
            StatusFinanceiro.IRRECUPERAVEL,
        ):
            prazo = assinatura.carencia_pagamento_termina_em
            em_carencia = assinatura.status_financeiro == StatusFinanceiro.INADIMPLENTE and cls._prazo_vigente(
                assinatura.carencia_pagamento_iniciada_em, prazo, agora
            )
            if em_carencia:
                assert prazo is not None
                motivos.append(MotivoRestricao.PAYMENT_GRACE_PERIOD)
                prazos.append(prazo)
            else:
                motivos.append(MotivoRestricao.PAYMENT_GRACE_PERIOD_EXPIRED)
                restrito = True
                if prazo is not None and timezone.is_aware(prazo):
                    prazos.append(prazo)

        if utilizacao.excesso_real > 0:
            prazo = assinatura.carencia_excesso_seats_termina_em
            if cls._prazo_vigente(assinatura.carencia_excesso_seats_iniciada_em, prazo, agora):
                assert prazo is not None
                motivos.append(MotivoRestricao.SEAT_OVERAGE_GRACE_PERIOD)
                prazos.append(prazo)
            else:
                motivos.append(MotivoRestricao.SEAT_OVERAGE_GRACE_PERIOD_EXPIRED)
                restrito = True
                if prazo is not None and timezone.is_aware(prazo):
                    prazos.append(prazo)

        if not motivos:
            return SituacaoAcesso(status=StatusAcesso.LIBERADO, motivos=(), regularizar_ate=None)
        return SituacaoAcesso(
            status=StatusAcesso.RESTRITO if restrito else StatusAcesso.EM_CARENCIA,
            motivos=tuple(motivos),
            regularizar_ate=min(prazos) if prazos else None,
        )

    @staticmethod
    def _prazo_vigente(iniciada_em: datetime | None, termina_em: datetime | None, agora: datetime) -> bool:
        return bool(
            iniciada_em is not None
            and termina_em is not None
            and timezone.is_aware(iniciada_em)
            and timezone.is_aware(termina_em)
            and iniciada_em <= agora < termina_em
        )

    @staticmethod
    def _restrito(motivo: MotivoRestricao, *, prazo: datetime | None = None) -> SituacaoAcesso:
        return SituacaoAcesso(status=StatusAcesso.RESTRITO, motivos=(motivo,), regularizar_ate=prazo)

    @staticmethod
    def _validar_utilizacao(utilizacao: UtilizacaoSeats) -> None:
        if not isinstance(utilizacao, UtilizacaoSeats):
            raise ValueError("Politica de acesso exige uma UtilizacaoSeats.")
        valores = (
            utilizacao.contratados,
            utilizacao.consumidos,
            utilizacao.reservados,
            utilizacao.comprometidos,
            utilizacao.disponiveis,
            utilizacao.excesso_real,
            utilizacao.excesso_comprometido,
        )
        coerente = all(type(valor) is int and valor >= 0 for valor in valores) and utilizacao == UtilizacaoSeats(
            contratados=utilizacao.contratados,
            consumidos=utilizacao.consumidos,
            reservados=utilizacao.reservados,
            comprometidos=utilizacao.consumidos + utilizacao.reservados,
            disponiveis=max(utilizacao.contratados - utilizacao.consumidos - utilizacao.reservados, 0),
            excesso_real=max(utilizacao.consumidos - utilizacao.contratados, 0),
            excesso_comprometido=max(utilizacao.consumidos + utilizacao.reservados - utilizacao.contratados, 0),
        )
        if not coerente:
            raise ValueError("UtilizacaoSeats incoerente com as formulas contratuais.")
