"""Casos de uso tipados para contratos e alterações de assinatura."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from django.db import transaction
from django.utils import timezone

from apps.api.autenticacao.services import lock_user_accounts
from apps.api.core.errors import APIError
from apps.assinaturas.errors import BillingErrorCode
from apps.assinaturas.features import CATALOGO_RECURSOS, PAPEIS_ISENTOS_SEAT, ValoresRecursos
from apps.assinaturas.models import (
    AlteracaoAssinatura,
    AssinaturaOrganizacao,
    ModoAtivacaoProposta,
    MomentoAplicacaoAlteracaoAssinatura,
    Periodicidade,
    PoliticaTrial,
    PrecoPlano,
    PropostaComercial,
    StatusAlteracaoAssinatura,
    StatusAssinatura,
    StatusFinanceiro,
    StatusPropostaComercial,
    TipoAlteracaoAssinatura,
    VersaoPlano,
)

if TYPE_CHECKING:
    from apps.organizacoes.memberships import OcupacaoSeats
    from apps.organizacoes.models import Organizacao, Papel
    from apps.organizacoes.organizations import TermoEncerramento
    from apps.usuarios.models import Usuario

MAIOR_BIGINT = 2**63 - 1
MAIOR_SMALLINT = 2**15 - 1
MOEDA = re.compile(r"^[A-Z]{3}$")
STATUS_CORRENTES = (StatusAssinatura.PENDENTE, StatusAssinatura.EM_TRIAL, StatusAssinatura.ATIVA)


class ErroAssinatura(ValueError):
    """Base dos conflitos deliberados do núcleo contratual."""


class ConflitoIdempotenciaAssinatura(ErroAssinatura):
    """A mesma chave foi reutilizada para outro comando."""


class ConflitoRevisaoAssinatura(ErroAssinatura):
    """O contrato corrente diverge da revisão ou do ciclo esperado."""


class MotivoFallbackTrial(StrEnum):
    TRIAL_LOCAL_ENCERRADO = "local_trial_ended"
    SEM_PAGAMENTO_VALIDO = "missing_valid_payment"
    PRIMEIRA_COBRANCA_FALHOU = "first_charge_failed"


@dataclass(frozen=True, slots=True)
class PagamentoTrialConfirmado:
    seats_contratados: int
    periodo_iniciado_em: datetime
    periodo_termina_em: datetime

    def __post_init__(self):
        if type(self.seats_contratados) is not int or not 0 <= self.seats_contratados <= MAIOR_SMALLINT:
            raise ValueError("Pagamento do trial exige seats contratados no intervalo de smallint.")
        datas = (self.periodo_iniciado_em, self.periodo_termina_em)
        if not all(isinstance(data, datetime) and timezone.is_aware(data) for data in datas):
            raise ValueError("Periodo pago do trial exige datetimes conscientes de fuso.")
        if self.periodo_iniciado_em >= self.periodo_termina_em:
            raise ValueError("Periodo pago do trial deve terminar depois do inicio.")


@dataclass(frozen=True, slots=True)
class FallbackTrialGratuito:
    motivo: MotivoFallbackTrial

    def __post_init__(self):
        if not isinstance(self.motivo, MotivoFallbackTrial):
            raise ValueError("Fallback do trial exige um motivo tipado.")


type ResultadoProcessamentoTrial = PagamentoTrialConfirmado | FallbackTrialGratuito


@dataclass(frozen=True, slots=True)
class TrialConvertido:
    assinatura: AssinaturaOrganizacao


@dataclass(frozen=True, slots=True)
class TrialConvertidoParaGratuito:
    assinatura: AssinaturaOrganizacao
    alteracao: AlteracaoAssinatura


type ResultadoEncerramentoTrial = TrialConvertido | TrialConvertidoParaGratuito


@dataclass(frozen=True)
class OrigemVersaoPlano:
    versao_plano: VersaoPlano

    def __post_init__(self):
        if not isinstance(self.versao_plano, VersaoPlano):
            raise ValueError("Origem de catálogo exige uma VersaoPlano.")


@dataclass(frozen=True)
class OrigemPropostaComercial:
    proposta_comercial: PropostaComercial

    def __post_init__(self):
        if not isinstance(self.proposta_comercial, PropostaComercial):
            raise ValueError("Origem enterprise exige uma PropostaComercial.")


type OrigemAssinatura = OrigemVersaoPlano


@dataclass(frozen=True)
class TermosAssinatura:
    periodicidade: Periodicidade
    moeda: str
    valor_base_centavos: int
    valor_seat_centavos: int
    seats_inclusos: int
    seats_contratados: int
    expansao_automatica_seats: bool
    recursos: ValoresRecursos
    carencia_pagamento_dias: int
    carencia_excesso_seats_dias: int

    def __post_init__(self):
        if not isinstance(self.periodicidade, Periodicidade):
            raise ValueError("Periodicidade dos termos deve ser uma Periodicidade concreta.")
        if type(self.moeda) is not str or not MOEDA.fullmatch(self.moeda):
            raise ValueError("Moeda deve ter três letras maiúsculas.")
        valores_bigint = (self.valor_base_centavos, self.valor_seat_centavos)
        valores_smallint = (
            self.seats_inclusos,
            self.seats_contratados,
            self.carencia_pagamento_dias,
            self.carencia_excesso_seats_dias,
        )
        if any(type(valor) is not int or not 0 <= valor <= MAIOR_BIGINT for valor in valores_bigint):
            raise ValueError("Valores monetários devem ser inteiros não negativos no intervalo de bigint.")
        if any(type(valor) is not int or not 0 <= valor <= MAIOR_SMALLINT for valor in valores_smallint):
            raise ValueError("Seats e carências devem ser inteiros não negativos no intervalo de smallint.")
        if type(self.expansao_automatica_seats) is not bool:
            raise ValueError("Expansão automática de seats deve ser booleana.")
        if not isinstance(self.recursos, ValoresRecursos):
            raise ValueError("Recursos dos termos devem ser ValoresRecursos.")


@dataclass(frozen=True)
class CriacaoAssinatura:
    organizacao: Organizacao
    origem: OrigemAssinatura
    termos: TermosAssinatura
    status: StatusAssinatura
    status_financeiro: StatusFinanceiro
    politica_trial: PoliticaTrial | None
    trial_termina_em: datetime | None
    chave_idempotencia: str

    def __post_init__(self):
        if getattr(self.organizacao, "pk", None) is None:
            raise ValueError("Organização da assinatura precisa estar persistida.")
        if not isinstance(self.origem, OrigemVersaoPlano):
            raise ValueError("Origem da assinatura precisa ser uma origem de catálogo.")
        if not isinstance(self.termos, TermosAssinatura):
            raise ValueError("Termos da assinatura precisam ser TermosAssinatura.")
        if not isinstance(self.status, StatusAssinatura):
            raise ValueError("Status precisa ser um StatusAssinatura concreto.")
        if not isinstance(self.status_financeiro, StatusFinanceiro):
            raise ValueError("Status financeiro precisa ser um StatusFinanceiro concreto.")
        if self.politica_trial is not None and not isinstance(self.politica_trial, PoliticaTrial):
            raise ValueError("Política de trial inválida.")
        if type(self.chave_idempotencia) is not str or not self.chave_idempotencia.strip() or len(self.chave_idempotencia) > 120:
            raise ValueError("Chave de idempotência deve ser texto não vazio com até 120 caracteres.")
        if self.status == StatusAssinatura.ENCERRADA:
            raise ValueError("Assinatura Encerrada não pode nascer pelo comando de criação corrente.")
        if self.status == StatusAssinatura.PENDENTE and self.status_financeiro != StatusFinanceiro.PENDENTE:
            raise ValueError("Assinatura Pendente exige status financeiro pendente.")
        if self.status == StatusAssinatura.ATIVA and self.status_financeiro == StatusFinanceiro.PENDENTE:
            raise ValueError("Assinatura Ativa não pode ter status financeiro pendente.")
        if self.status == StatusAssinatura.EM_TRIAL:
            if self.status_financeiro != StatusFinanceiro.ISENTO or self.politica_trial is None or self.trial_termina_em is None:
                raise ValueError("Assinatura em trial exige política, término e status financeiro isento.")
        elif self.politica_trial is not None or self.trial_termina_em is not None:
            raise ValueError("Assinatura que não nasce em trial não recebe datas ou política de trial.")


@dataclass(frozen=True)
class CriacaoAlteracaoAssinatura:
    assinatura: AssinaturaOrganizacao
    tipo: TipoAlteracaoAssinatura
    origem_pretendida: OrigemVersaoPlano
    termos_pretendidos: TermosAssinatura
    revisao_esperada: int
    chave_idempotencia: str
    aplicar_em: datetime | None = None
    solicitada_por: Usuario | None = None
    seats_consumidos: int | None = None

    def __post_init__(self):
        if not isinstance(self.assinatura, AssinaturaOrganizacao) or self.assinatura.pk is None:
            raise ValueError("Alteração exige uma assinatura persistida.")
        if not isinstance(self.tipo, TipoAlteracaoAssinatura):
            raise ValueError("Tipo da alteração deve ser um TipoAlteracaoAssinatura concreto.")
        if self.tipo == TipoAlteracaoAssinatura.FALLBACK_TRIAL:
            raise ValueError("Fallback de trial e reservado ao encerramento nominal do trial.")
        if not isinstance(self.origem_pretendida, OrigemVersaoPlano):
            raise ValueError("Origem pretendida precisa ser uma origem de catálogo.")
        if not isinstance(self.termos_pretendidos, TermosAssinatura):
            raise ValueError("Termos pretendidos precisam ser TermosAssinatura.")
        if type(self.revisao_esperada) is not int or not 1 <= self.revisao_esperada <= MAIOR_SMALLINT - 1:
            raise ValueError("Revisão esperada deve ser um inteiro positivo aplicável.")
        if type(self.chave_idempotencia) is not str or not self.chave_idempotencia.strip() or len(self.chave_idempotencia) > 120:
            raise ValueError("Chave de idempotência deve ser texto não vazio com até 120 caracteres.")
        if self.aplicar_em is not None and not isinstance(self.aplicar_em, datetime):
            raise ValueError("Data de aplicação precisa ser um datetime.")
        if self.solicitada_por is not None and getattr(self.solicitada_por, "pk", None) is None:
            raise ValueError("Solicitante da alteração precisa estar persistido.")
        if self.seats_consumidos is not None and (type(self.seats_consumidos) is not int or self.seats_consumidos < 0):
            raise ValueError("Consumo de seats deve ser inteiro não negativo.")


@dataclass(frozen=True)
class PrecoCalculadoAssinatura:
    seats_cobrados: int
    total_centavos: int


@dataclass(frozen=True)
class UtilizacaoSeats:
    contratados: int
    consumidos: int
    reservados: int
    comprometidos: int
    disponiveis: int
    excesso_real: int
    excesso_comprometido: int


class Assinaturas:
    """Cria contratos por uma única fronteira transacional e tipada."""

    @classmethod
    def calcular_preco(cls, termos: TermosAssinatura) -> PrecoCalculadoAssinatura:
        if not isinstance(termos, TermosAssinatura):
            raise ValueError("Cálculo de preço exige TermosAssinatura.")
        seats_cobrados = max(0, termos.seats_contratados - termos.seats_inclusos)
        return PrecoCalculadoAssinatura(
            seats_cobrados=seats_cobrados,
            total_centavos=termos.valor_base_centavos + seats_cobrados * termos.valor_seat_centavos,
        )

    @classmethod
    def calcular_utilizacao(cls, assinatura: AssinaturaOrganizacao, ocupacao: OcupacaoSeats) -> UtilizacaoSeats:
        """Combina fatos já carregados sem disparar consultas implícitas."""
        from apps.organizacoes.memberships import OcupacaoSeats

        if not isinstance(assinatura, AssinaturaOrganizacao):
            raise ValueError("Utilização exige uma assinatura.")
        if not isinstance(ocupacao, OcupacaoSeats):
            raise ValueError("Utilização exige uma OcupacaoSeats.")
        if "seats_contratados" in assinatura.get_deferred_fields():
            raise ValueError("Utilização não aceita assinatura com campos deferred.")
        consumidos = ocupacao.consumidos
        reservados = ocupacao.reservados
        if type(consumidos) is not int or consumidos < 0 or type(reservados) is not int or reservados < 0:
            raise ValueError("Ocupação exige totais inteiros nao negativos.")
        comprometidos = consumidos + reservados
        contratados = assinatura.seats_contratados
        if type(contratados) is not int or contratados < 0:
            raise ValueError("Assinatura exige seats_contratados inteiro não negativo.")
        return UtilizacaoSeats(
            contratados=contratados,
            consumidos=consumidos,
            reservados=reservados,
            comprometidos=comprometidos,
            disponiveis=max(contratados - comprometidos, 0),
            excesso_real=max(consumidos - contratados, 0),
            excesso_comprometido=max(comprometidos - contratados, 0),
        )

    @classmethod
    def validar_capacidade(
        cls,
        assinatura: AssinaturaOrganizacao,
        ocupacao: OcupacaoSeats,
        *,
        contexto_ocupacao: OcupacaoSeats | None = None,
    ) -> UtilizacaoSeats:
        utilizacao = cls.calcular_utilizacao(assinatura, ocupacao)
        if utilizacao.excesso_comprometido:
            contexto = cls.calcular_utilizacao(assinatura, contexto_ocupacao or ocupacao)
            raise APIError(
                BillingErrorCode.SEAT_LIMIT_REACHED,
                status_code=409,
                context={
                    "contratados": utilizacao.contratados,
                    "consumidos": contexto.consumidos,
                    "reservados": contexto.reservados,
                },
            )
        return utilizacao

    @classmethod
    def registrar_falha_renovacao(
        cls,
        assinatura: AssinaturaOrganizacao,
        *,
        agora: datetime,
    ) -> AssinaturaOrganizacao:
        """Abre a carencia financeira somente na primeira falha da renovacao."""
        cls._validar_transicao_temporal(assinatura, agora)
        using = assinatura._state.db or "default"
        with transaction.atomic(using=using):
            cls._bloquear_organizacao(assinatura.organizacao_id, using=using)
            bloqueada = AssinaturaOrganizacao.all_objects.using(using).select_for_update().get(pk=assinatura.pk)
            if bloqueada.status != StatusAssinatura.ATIVA:
                return bloqueada
            if bloqueada.status_financeiro == StatusFinanceiro.INADIMPLENTE:
                return bloqueada
            if bloqueada.status_financeiro != StatusFinanceiro.REGULAR:
                return bloqueada

            bloqueada.status_financeiro = StatusFinanceiro.INADIMPLENTE
            bloqueada.carencia_pagamento_iniciada_em = agora
            bloqueada.carencia_pagamento_termina_em = agora + timedelta(days=bloqueada.carencia_pagamento_dias)
            bloqueada.revisao += 1
            bloqueada._salvar_transicao(
                using=using,
                update_fields=[
                    "status_financeiro",
                    "carencia_pagamento_iniciada_em",
                    "carencia_pagamento_termina_em",
                    "revisao",
                    "last_modified_at",
                ],
            )
            return bloqueada

    @classmethod
    def registrar_pagamento_confirmado(
        cls,
        assinatura: AssinaturaOrganizacao,
        *,
        agora: datetime,
    ) -> AssinaturaOrganizacao:
        """Regulariza somente a causa financeira, preservando excesso de seats."""
        cls._validar_transicao_temporal(assinatura, agora)
        using = assinatura._state.db or "default"
        with transaction.atomic(using=using):
            cls._bloquear_organizacao(assinatura.organizacao_id, using=using)
            bloqueada = AssinaturaOrganizacao.all_objects.using(using).select_for_update().get(pk=assinatura.pk)
            if bloqueada.status != StatusAssinatura.ATIVA:
                return bloqueada
            if (
                bloqueada.status_financeiro in (StatusFinanceiro.REGULAR, StatusFinanceiro.ISENTO)
                and bloqueada.carencia_pagamento_iniciada_em is None
                and bloqueada.carencia_pagamento_termina_em is None
            ):
                return bloqueada
            if bloqueada.status_financeiro not in (
                StatusFinanceiro.REGULAR,
                StatusFinanceiro.INADIMPLENTE,
                StatusFinanceiro.IRRECUPERAVEL,
            ):
                return bloqueada

            bloqueada.status_financeiro = StatusFinanceiro.REGULAR
            bloqueada.carencia_pagamento_iniciada_em = None
            bloqueada.carencia_pagamento_termina_em = None
            bloqueada.revisao += 1
            bloqueada._salvar_transicao(
                using=using,
                update_fields=[
                    "status_financeiro",
                    "carencia_pagamento_iniciada_em",
                    "carencia_pagamento_termina_em",
                    "revisao",
                    "last_modified_at",
                ],
            )
            return bloqueada

    @classmethod
    def reconciliar_carencia_seats(
        cls,
        assinatura: AssinaturaOrganizacao,
        *,
        agora: datetime,
    ) -> AssinaturaOrganizacao:
        """Abre ou limpa somente a carencia de excesso real de seats."""
        cls._validar_transicao_temporal(assinatura, agora)
        using = assinatura._state.db or "default"
        with transaction.atomic(using=using):
            organizacao_bloqueada = cls._bloquear_organizacao(assinatura.organizacao_id, using=using)
            bloqueada = AssinaturaOrganizacao.all_objects.using(using).select_for_update().get(pk=assinatura.pk)
            if bloqueada.status != StatusAssinatura.ATIVA:
                return bloqueada
            from apps.organizacoes.memberships import Vinculos

            ocupacao = Vinculos.calcular_ocupacao(organizacao_bloqueada, cls.papeis_isentos_seat(bloqueada))
            utilizacao = cls.calcular_utilizacao(bloqueada, ocupacao)
            if utilizacao.excesso_real > 0:
                if bloqueada.carencia_excesso_seats_iniciada_em is not None:
                    return bloqueada
                bloqueada.carencia_excesso_seats_iniciada_em = agora
                bloqueada.carencia_excesso_seats_termina_em = agora + timedelta(days=bloqueada.carencia_excesso_seats_dias)
            else:
                if bloqueada.carencia_excesso_seats_iniciada_em is None and bloqueada.carencia_excesso_seats_termina_em is None:
                    return bloqueada
                bloqueada.carencia_excesso_seats_iniciada_em = None
                bloqueada.carencia_excesso_seats_termina_em = None
            bloqueada.revisao += 1
            bloqueada._salvar_transicao(
                using=using,
                update_fields=[
                    "carencia_excesso_seats_iniciada_em",
                    "carencia_excesso_seats_termina_em",
                    "revisao",
                    "last_modified_at",
                ],
            )
            return bloqueada

    @classmethod
    def encerrar_trial(
        cls,
        assinatura: AssinaturaOrganizacao,
        *,
        resultado: ResultadoProcessamentoTrial,
        agora: datetime,
    ) -> ResultadoEncerramentoTrial:
        """Converte ou aplica fallback no mesmo contrato, sob uma unica revisao."""
        cls._validar_transicao_temporal(assinatura, agora)
        if not isinstance(resultado, (PagamentoTrialConfirmado, FallbackTrialGratuito)):
            raise ValueError("Encerramento do trial exige um resultado tipado.")

        termos_gratuitos = None
        versao_gratuita = None
        if isinstance(resultado, FallbackTrialGratuito):
            from apps.assinaturas.catalogs import CatalogoPlanos

            versao_gratuita, preco_gratuito = CatalogoPlanos.obter_versao_inicial(
                codigo="gratuito",
                periodicidade=Periodicidade(assinatura.periodicidade),
                moeda=assinatura.moeda,
            )
            termos_gratuitos = cls._termos_catalogo(
                versao_gratuita,
                preco_gratuito,
                seats_contratados=versao_gratuita.seats_inclusos,
            )

        using = assinatura._state.db or "default"
        with transaction.atomic(using=using):
            organizacao_bloqueada = cls._bloquear_organizacao(assinatura.organizacao_id, using=using)
            bloqueada = AssinaturaOrganizacao.all_objects.using(using).select_for_update().get(pk=assinatura.pk)
            from apps.organizacoes.memberships import Vinculos

            ocupacao = Vinculos.calcular_ocupacao(organizacao_bloqueada, cls.papeis_isentos_seat(bloqueada))
            if isinstance(resultado, FallbackTrialGratuito):
                assert versao_gratuita is not None
                assert termos_gratuitos is not None
                return cls._aplicar_fallback_trial(
                    bloqueada,
                    resultado=resultado,
                    termos=termos_gratuitos,
                    versao_plano=versao_gratuita,
                    ocupacao=ocupacao,
                    agora=agora,
                    using=using,
                )
            return cls._converter_trial_pago(
                bloqueada,
                pagamento=resultado,
                ocupacao=ocupacao,
                agora=agora,
                using=using,
            )

    @classmethod
    def solicitar_expansao_automatica(
        cls,
        assinatura: AssinaturaOrganizacao,
        *,
        seats_necessarios: int,
    ) -> AlteracaoAssinatura:
        """Registra localmente o aumento absoluto; confirmação continua nominal."""
        if not isinstance(assinatura, AssinaturaOrganizacao) or assinatura.pk is None:
            raise ValueError("Expansão automática exige uma assinatura persistida.")
        if not assinatura.expansao_automatica_seats:
            raise ValueError("O contrato não permite expansão automática de seats.")
        if assinatura.versao_plano is None:
            raise ValueError("Expansão automática exige origem de catálogo.")
        if type(seats_necessarios) is not int or seats_necessarios <= assinatura.seats_contratados:
            raise ValueError("Expansão automática exige capacidade absoluta maior que a atual.")
        termos = TermosAssinatura(
            periodicidade=Periodicidade(assinatura.periodicidade),
            moeda=assinatura.moeda,
            valor_base_centavos=assinatura.valor_base_centavos,
            valor_seat_centavos=assinatura.valor_seat_centavos,
            seats_inclusos=assinatura.seats_inclusos,
            seats_contratados=seats_necessarios,
            expansao_automatica_seats=assinatura.expansao_automatica_seats,
            recursos=ValoresRecursos(CATALOGO_RECURSOS, assinatura.recursos),
            carencia_pagamento_dias=assinatura.carencia_pagamento_dias,
            carencia_excesso_seats_dias=assinatura.carencia_excesso_seats_dias,
        )
        return cls.solicitar_alteracao(
            CriacaoAlteracaoAssinatura(
                assinatura=assinatura,
                tipo=TipoAlteracaoAssinatura.AUMENTO_SEATS,
                origem_pretendida=OrigemVersaoPlano(assinatura.versao_plano),
                termos_pretendidos=termos,
                revisao_esperada=assinatura.revisao,
                chave_idempotencia=f"auto-seats:{assinatura.pk}:{assinatura.revisao}:{seats_necessarios}",
            )
        )

    @classmethod
    def obter_corrente(
        cls,
        organizacao: Organizacao,
        *,
        bloquear: bool = False,
    ) -> AssinaturaOrganizacao | None:
        using = organizacao._state.db or "default"
        queryset = AssinaturaOrganizacao.all_objects.using(using).filter(
            organizacao=organizacao,
            status__in=STATUS_CORRENTES,
        )
        if bloquear:
            queryset = queryset.select_for_update()
        return queryset.first()

    @staticmethod
    def papeis_isentos_seat(assinatura: AssinaturaOrganizacao) -> frozenset[Papel]:
        return ValoresRecursos(CATALOGO_RECURSOS, assinatura.recursos).obter(PAPEIS_ISENTOS_SEAT)

    @classmethod
    def obter_termo_encerramento(cls, organizacao: Organizacao) -> TermoEncerramento:
        from apps.organizacoes.context import organizacao_atual_privilegiada
        from apps.organizacoes.organizations import TermoEncerramentoAgendado, TermoEncerramentoImediato

        with organizacao_atual_privilegiada(organizacao.pk):
            assinatura = cls.obter_corrente(organizacao)
        if assinatura is None:
            raise APIError(BillingErrorCode.SUBSCRIPTION_REQUIRED, status_code=503)
        if assinatura.status == StatusAssinatura.ATIVA and assinatura.total_centavos > 0 and assinatura.periodo_atual_termina_em is not None:
            return TermoEncerramentoAgendado(agendado_para=assinatura.periodo_atual_termina_em)
        return TermoEncerramentoImediato()

    @classmethod
    def solicitar_encerramento(
        cls,
        organizacao: Organizacao,
        *,
        agora: datetime,
        revisao_esperada: int | None = None,
    ) -> TermoEncerramento:
        """Decide o termo e persiste o cancelamento sob organização -> assinatura."""
        from apps.organizacoes.organizations import TermoEncerramentoAgendado, TermoEncerramentoImediato

        using = organizacao._state.db or "default"
        with transaction.atomic(using=using):
            organizacao = cls._bloquear_organizacao(organizacao.pk, using=using)
            assinatura = cls.obter_corrente(organizacao, bloquear=True)
            if assinatura is None:
                raise APIError(BillingErrorCode.SUBSCRIPTION_REQUIRED, status_code=503)
            if revisao_esperada is not None and assinatura.revisao != revisao_esperada:
                raise ConflitoRevisaoAssinatura("A revisão esperada não corresponde ao contrato corrente.")
            cls._cancelar_alteracoes_pendentes_por_encerramento(assinatura, agora=agora, using=using)
            if assinatura.status != StatusAssinatura.ATIVA or assinatura.total_centavos <= 0 or assinatura.periodo_atual_termina_em is None:
                assinatura.status = StatusAssinatura.ENCERRADA
                assinatura.revisao += 1
                assinatura.cancelamento_agendado_para = None
                assinatura.encerrada_em = agora
                assinatura.motivo_encerramento = "subscription_cancelled"
                assinatura._salvar_transicao(
                    using=using,
                    update_fields=[
                        "status",
                        "revisao",
                        "cancelamento_agendado_para",
                        "encerrada_em",
                        "motivo_encerramento",
                        "last_modified_at",
                    ],
                )
                return TermoEncerramentoImediato()
            if assinatura.cancelamento_agendado_para is not None:
                return TermoEncerramentoAgendado(agendado_para=assinatura.cancelamento_agendado_para)
            assinatura.cancelamento_agendado_para = assinatura.periodo_atual_termina_em
            assinatura.revisao += 1
            assinatura._salvar_transicao(
                using=using,
                update_fields=["cancelamento_agendado_para", "revisao", "last_modified_at"],
            )
            return TermoEncerramentoAgendado(agendado_para=assinatura.cancelamento_agendado_para)

    @classmethod
    def cancelar_encerramento(cls, organizacao: Organizacao, *, revisao_esperada: int | None = None) -> None:
        """Limpa o agendamento contratual sob a mesma ordem global de locks."""
        using = organizacao._state.db or "default"
        with transaction.atomic(using=using):
            organizacao = cls._bloquear_organizacao(organizacao.pk, using=using)
            assinatura = cls.obter_corrente(organizacao, bloquear=True)
            if assinatura is None:
                raise APIError(BillingErrorCode.SUBSCRIPTION_REQUIRED, status_code=503)
            if revisao_esperada is not None and assinatura.revisao != revisao_esperada:
                raise ConflitoRevisaoAssinatura("A revisão esperada não corresponde ao contrato corrente.")
            if assinatura.cancelamento_agendado_para is None:
                return
            assinatura.cancelamento_agendado_para = None
            assinatura.revisao += 1
            assinatura._salvar_transicao(
                using=using,
                update_fields=["cancelamento_agendado_para", "revisao", "last_modified_at"],
            )

    @classmethod
    def encerrar(cls, organizacao: Organizacao, *, encerrada_em: datetime) -> None:
        """Encerra idempotentemente preservando organização -> assinatura."""
        using = organizacao._state.db or "default"
        with transaction.atomic(using=using):
            organizacao = cls._bloquear_organizacao(organizacao.pk, using=using)
            assinatura = cls.obter_corrente(organizacao, bloquear=True)
            if assinatura is None:
                encerrada = (
                    AssinaturaOrganizacao.all_objects.using(using)
                    .select_for_update()
                    .filter(organizacao=organizacao, status=StatusAssinatura.ENCERRADA)
                    .first()
                )
                if encerrada is not None:
                    if encerrada.motivo_encerramento != "organization_closed" or encerrada.encerrada_em != encerrada_em:
                        encerrada.revisao += 1
                        encerrada.encerrada_em = encerrada_em
                        encerrada.motivo_encerramento = "organization_closed"
                        encerrada._salvar_transicao(
                            using=using,
                            update_fields=["revisao", "encerrada_em", "motivo_encerramento", "last_modified_at"],
                        )
                    return
                raise APIError(BillingErrorCode.SUBSCRIPTION_REQUIRED, status_code=503)
            cls._cancelar_alteracoes_pendentes_por_encerramento(assinatura, agora=encerrada_em, using=using)
            assinatura.status = StatusAssinatura.ENCERRADA
            assinatura.revisao += 1
            assinatura.cancelamento_agendado_para = None
            assinatura.encerrada_em = encerrada_em
            assinatura.motivo_encerramento = "organization_closed"
            assinatura._salvar_transicao(
                using=using,
                update_fields=[
                    "status",
                    "revisao",
                    "cancelamento_agendado_para",
                    "encerrada_em",
                    "motivo_encerramento",
                    "last_modified_at",
                ],
            )

    @classmethod
    def criar(cls, comando: CriacaoAssinatura, *, agora: datetime | None = None) -> AssinaturaOrganizacao:
        """Único caminho base que instancia ``AssinaturaOrganizacao``."""
        if not isinstance(comando, CriacaoAssinatura):
            raise ValueError("Criação de assinatura exige CriacaoAssinatura.")
        organizacao = comando.organizacao
        agora = agora or timezone.now()
        using = organizacao._state.db or "default"

        with transaction.atomic(using=using):
            modelo_organizacao = type(organizacao)
            organizacao_bloqueada = modelo_organizacao.all_objects.using(using).select_for_update().get(pk=organizacao.pk)
            existente = (
                AssinaturaOrganizacao.all_objects.using(using)
                .select_for_update()
                .filter(
                    organizacao=organizacao_bloqueada,
                    chave_idempotencia=comando.chave_idempotencia,
                )
                .first()
            )
            if existente is not None:
                if not cls._equivale_ao_comando(existente, comando):
                    raise ConflitoIdempotenciaAssinatura("A chave de idempotência já foi usada com outro payload.")
                return existente

            cls._validar_organizacao_contratavel(organizacao_bloqueada)
            corrente = (
                AssinaturaOrganizacao.all_objects.using(using)
                .select_for_update()
                .filter(organizacao=organizacao_bloqueada, status__in=STATUS_CORRENTES)
                .first()
            )
            if corrente is not None:
                raise ConflitoRevisaoAssinatura("A organização já possui um contrato corrente.")

            return cls._persistir_assinatura(
                organizacao=organizacao_bloqueada,
                versao_plano=comando.origem.versao_plano,
                proposta_comercial=None,
                termos=comando.termos,
                status=comando.status,
                status_financeiro=comando.status_financeiro,
                politica_trial=comando.politica_trial,
                trial_termina_em=comando.trial_termina_em,
                chave_idempotencia=comando.chave_idempotencia,
                agora=agora,
                using=using,
            )

    @staticmethod
    def _persistir_assinatura(
        *,
        organizacao: Organizacao,
        versao_plano: VersaoPlano | None,
        proposta_comercial: PropostaComercial | None,
        termos: TermosAssinatura,
        status: StatusAssinatura,
        status_financeiro: StatusFinanceiro,
        politica_trial: PoliticaTrial | None,
        trial_termina_em: datetime | None,
        chave_idempotencia: str,
        agora: datetime,
        using: str,
    ) -> AssinaturaOrganizacao:
        return AssinaturaOrganizacao.objects.using(using).create(
            organizacao=organizacao,
            versao_plano=versao_plano,
            proposta_comercial=proposta_comercial,
            status=status,
            status_financeiro=status_financeiro,
            revisao=1,
            periodicidade=termos.periodicidade,
            moeda=termos.moeda,
            valor_base_centavos=termos.valor_base_centavos,
            valor_seat_centavos=termos.valor_seat_centavos,
            seats_inclusos=termos.seats_inclusos,
            seats_contratados=termos.seats_contratados,
            expansao_automatica_seats=termos.expansao_automatica_seats,
            recursos=termos.recursos.materializar(),
            politica_trial=politica_trial,
            trial_iniciado_em=agora if status == StatusAssinatura.EM_TRIAL else None,
            trial_termina_em=trial_termina_em,
            carencia_pagamento_dias=termos.carencia_pagamento_dias,
            carencia_excesso_seats_dias=termos.carencia_excesso_seats_dias,
            chave_idempotencia=chave_idempotencia,
        )

    @classmethod
    def criar_gratuita(
        cls,
        *,
        organizacao: Organizacao,
        versao_plano: VersaoPlano,
        preco_plano: PrecoPlano,
        chave_idempotencia: str | None = None,
    ) -> AssinaturaOrganizacao:
        termos = cls._termos_catalogo(versao_plano, preco_plano, seats_contratados=versao_plano.seats_inclusos)
        if termos.valor_base_centavos != 0 or termos.valor_seat_centavos != 0:
            raise ValueError("Preset gratuito exige preço base e seat iguais a zero.")
        return cls.criar(
            CriacaoAssinatura(
                organizacao=organizacao,
                origem=OrigemVersaoPlano(versao_plano),
                termos=termos,
                status=StatusAssinatura.ATIVA,
                status_financeiro=StatusFinanceiro.ISENTO,
                politica_trial=None,
                trial_termina_em=None,
                chave_idempotencia=chave_idempotencia or cls._chave_preset(organizacao, versao_plano, preco_plano, "gratuita"),
            )
        )

    @classmethod
    def criar_trial(
        cls,
        *,
        organizacao: Organizacao,
        versao_plano: VersaoPlano,
        preco_plano: PrecoPlano,
        politica_trial: PoliticaTrial = PoliticaTrial.SEM_FORMA_PAGAMENTO,
        chave_idempotencia: str | None = None,
        agora: datetime | None = None,
    ) -> AssinaturaOrganizacao:
        if versao_plano.limite_seats_trial is None or versao_plano.duracao_trial_dias <= 0:
            raise ValueError("Versão informada não admite trial.")
        chave = chave_idempotencia or cls._chave_preset(organizacao, versao_plano, preco_plano, "trial")
        if agora is None:
            using = organizacao._state.db or "default"
            with transaction.atomic(using=using):
                organizacao = type(organizacao).all_objects.using(using).select_for_update().get(pk=organizacao.pk)
                existente = (
                    AssinaturaOrganizacao.all_objects.using(using)
                    .select_for_update()
                    .filter(organizacao=organizacao, chave_idempotencia=chave)
                    .first()
                )
                agora = existente.trial_iniciado_em if existente is not None else timezone.now()
                if agora is None:
                    raise ConflitoIdempotenciaAssinatura("O trial idempotente existente não possui início coerente.")
                return cls._criar_trial_em(organizacao, versao_plano, preco_plano, politica_trial, chave, agora)
        return cls._criar_trial_em(organizacao, versao_plano, preco_plano, politica_trial, chave, agora)

    @classmethod
    def _criar_trial_em(
        cls,
        organizacao: Organizacao,
        versao_plano: VersaoPlano,
        preco_plano: PrecoPlano,
        politica_trial: PoliticaTrial,
        chave_idempotencia: str,
        agora: datetime,
    ) -> AssinaturaOrganizacao:
        limite_seats_trial = versao_plano.limite_seats_trial
        if limite_seats_trial is None:
            raise ValueError("Versão informada não admite trial.")
        termos = cls._termos_catalogo(
            versao_plano,
            preco_plano,
            seats_contratados=limite_seats_trial,
        )
        return cls.criar(
            CriacaoAssinatura(
                organizacao=organizacao,
                origem=OrigemVersaoPlano(versao_plano),
                termos=termos,
                status=StatusAssinatura.EM_TRIAL,
                status_financeiro=StatusFinanceiro.ISENTO,
                politica_trial=politica_trial,
                trial_termina_em=agora + timedelta(days=versao_plano.duracao_trial_dias),
                chave_idempotencia=chave_idempotencia,
            ),
            agora=agora,
        )

    @classmethod
    def criar_paga(
        cls,
        *,
        organizacao: Organizacao,
        versao_plano: VersaoPlano,
        preco_plano: PrecoPlano,
        seats_contratados: int,
        chave_idempotencia: str | None = None,
    ) -> AssinaturaOrganizacao:
        termos = cls._termos_catalogo(versao_plano, preco_plano, seats_contratados=seats_contratados)
        if cls.calcular_preco(termos).total_centavos == 0:
            raise ValueError("Preset pago exige preço total de valor positivo.")
        return cls.criar(
            CriacaoAssinatura(
                organizacao=organizacao,
                origem=OrigemVersaoPlano(versao_plano),
                termos=termos,
                status=StatusAssinatura.PENDENTE,
                status_financeiro=StatusFinanceiro.PENDENTE,
                politica_trial=None,
                trial_termina_em=None,
                chave_idempotencia=chave_idempotencia or cls._chave_preset(organizacao, versao_plano, preco_plano, "paga"),
            )
        )

    @classmethod
    def solicitar_alteracao(cls, comando: CriacaoAlteracaoAssinatura) -> AlteracaoAssinatura:
        """Persiste um pedido imutável depois de validar a revisão corrente."""
        if not isinstance(comando, CriacaoAlteracaoAssinatura):
            raise ValueError("Solicitação exige CriacaoAlteracaoAssinatura.")
        assinatura_informada = comando.assinatura
        using = assinatura_informada._state.db or "default"
        momento = cls._momento_padrao(comando.tipo)
        aplicar_em = cls._resolver_data_aplicacao(comando, assinatura_informada, momento)
        pedido = cls._pedido_alteracao(comando, momento=momento, aplicar_em=aplicar_em)

        with transaction.atomic(using=using):
            usuarios_bloqueados = lock_user_accounts((comando.solicitada_por,), using=using)
            solicitante = usuarios_bloqueados.get(comando.solicitada_por.pk) if comando.solicitada_por is not None else None
            organizacao = cls._bloquear_organizacao(assinatura_informada.organizacao_id, using=using)
            cls._validar_organizacao_contratavel(organizacao)
            assinatura = AssinaturaOrganizacao.all_objects.using(using).select_for_update().get(pk=assinatura_informada.pk, organizacao=organizacao)
            existente = (
                AlteracaoAssinatura.all_objects.using(using)
                .select_for_update()
                .filter(organizacao=organizacao, chave_idempotencia=comando.chave_idempotencia)
                .first()
            )
            if existente is not None:
                if existente.pedido != pedido:
                    raise ConflitoIdempotenciaAssinatura("A chave de idempotência da alteração já foi usada com outro payload.")
                return existente

            if assinatura.status not in STATUS_CORRENTES:
                raise ConflitoRevisaoAssinatura("Somente o contrato corrente pode ser alterado.")
            if assinatura.cancelamento_agendado_para is not None:
                raise ConflitoRevisaoAssinatura("O contrato possui cancelamento agendado.")
            if assinatura.revisao != comando.revisao_esperada:
                raise ConflitoRevisaoAssinatura("A revisão esperada não corresponde ao contrato corrente.")
            if aplicar_em is not None and aplicar_em <= timezone.now():
                raise ValueError("Alteração do próximo ciclo exige uma data futura de aplicação.")
            cls._validar_mudanca(comando, assinatura)

            snapshot_anterior = cls._snapshot_assinatura(assinatura)
            snapshot_pretendido = cls._snapshot_pretendido(assinatura, comando)
            return AlteracaoAssinatura.objects.using(using).create(
                organizacao=organizacao,
                assinatura=assinatura,
                tipo=comando.tipo,
                momento_aplicacao=momento,
                status=StatusAlteracaoAssinatura.SOLICITADA,
                revisao_esperada=comando.revisao_esperada,
                chave_idempotencia=comando.chave_idempotencia,
                solicitada_por=solicitante,
                pedido=pedido,
                snapshot_anterior=snapshot_anterior,
                snapshot_pretendido=snapshot_pretendido,
                aplicar_em=aplicar_em,
            )

    @classmethod
    def solicitar_alteracao_catalogo(
        cls,
        assinatura: AssinaturaOrganizacao,
        *,
        tipo: TipoAlteracaoAssinatura,
        revisao_esperada: int,
        chave_idempotencia: str,
        solicitada_por: Usuario,
        versao_plano_id: int | None = None,
        periodicidade: Periodicidade | None = None,
        seats_contratados: int | None = None,
        seats_consumidos: int | None = None,
    ) -> AlteracaoAssinatura:
        """Traduz uma intenção pública em termos completos e imutáveis."""
        if not isinstance(assinatura, AssinaturaOrganizacao) or assinatura.pk is None:
            raise ValueError("Alteração de catálogo exige uma assinatura persistida.")
        if not isinstance(tipo, TipoAlteracaoAssinatura) or tipo == TipoAlteracaoAssinatura.FALLBACK_TRIAL:
            raise ValueError("Tipo de alteração pública inválido.")
        if assinatura.versao_plano_id is None:
            raise ValueError("Alteração de catálogo exige uma assinatura originada de plano.")
        versao_atual = assinatura.versao_plano
        if versao_atual is None:
            raise ValueError("Alteração de catálogo exige uma versão de plano disponível.")

        tipos_seats = (TipoAlteracaoAssinatura.AUMENTO_SEATS, TipoAlteracaoAssinatura.REDUCAO_SEATS)
        if tipo in tipos_seats:
            if seats_contratados is None:
                raise ValueError("Alteração de seats exige a quantidade absoluta pretendida.")
            versao = versao_atual
            termos = cls._termos_atuais(assinatura, seats_contratados=seats_contratados)
        else:
            if tipo == TipoAlteracaoAssinatura.MUDANCA_PERIODICIDADE:
                versao = versao_atual
            else:
                if versao_plano_id is None:
                    raise ValueError("Mudança de plano exige a versão pretendida.")
                versao = VersaoPlano.objects.select_related("plano").filter(pk=versao_plano_id, is_active=True, publicada_em__isnull=False).first()
                if versao is None:
                    raise ValueError("Versão de plano pretendida não está disponível.")
            periodicidade_pretendida = periodicidade or Periodicidade(assinatura.periodicidade)
            preco = (
                PrecoPlano.objects.filter(
                    versao_plano=versao,
                    periodicidade=periodicidade_pretendida,
                    moeda=assinatura.moeda,
                    is_active=True,
                )
                .order_by("pk")
                .first()
            )
            if preco is None:
                raise ValueError("Preço publicado não está disponível para a alteração.")
            termos = cls._termos_catalogo(
                versao,
                preco,
                seats_contratados=assinatura.seats_contratados,
            )

        return cls.solicitar_alteracao(
            CriacaoAlteracaoAssinatura(
                assinatura=assinatura,
                tipo=tipo,
                origem_pretendida=OrigemVersaoPlano(versao),
                termos_pretendidos=termos,
                revisao_esperada=revisao_esperada,
                chave_idempotencia=chave_idempotencia,
                solicitada_por=solicitada_por,
                seats_consumidos=seats_consumidos,
            )
        )

    @classmethod
    def marcar_aguardando_gateway(cls, alteracao: AlteracaoAssinatura) -> AlteracaoAssinatura:
        """Executa a transição nominal que antecede uma confirmação externa."""
        with cls._alteracao_bloqueada(alteracao) as bloqueada:
            if bloqueada.status == StatusAlteracaoAssinatura.AGUARDANDO_GATEWAY:
                return bloqueada
            if bloqueada.status != StatusAlteracaoAssinatura.SOLICITADA:
                raise ConflitoRevisaoAssinatura("A alteração não pode aguardar gateway no estado atual.")
            cls._validar_alteracao_processavel(bloqueada)
            bloqueada.status = StatusAlteracaoAssinatura.AGUARDANDO_GATEWAY
            bloqueada.save(update_fields=["status", "last_modified_at"])
            return bloqueada

    @classmethod
    def confirmar_alteracao(
        cls,
        alteracao: AlteracaoAssinatura,
        *,
        evento_gateway: str | None = None,
        agora: datetime | None = None,
        permitir_evento_atrasado: bool = False,
    ) -> AlteracaoAssinatura:
        """Confirma a alteração sem permitir que uma revisão atrasada regrida o contrato."""
        agora = agora or timezone.now()
        with cls._alteracao_bloqueada(alteracao) as bloqueada:
            if bloqueada.status == StatusAlteracaoAssinatura.CONFIRMADA:
                return bloqueada
            if bloqueada.status not in (
                StatusAlteracaoAssinatura.SOLICITADA,
                StatusAlteracaoAssinatura.AGUARDANDO_GATEWAY,
            ):
                raise ConflitoRevisaoAssinatura("A alteração não pode ser confirmada no estado atual.")
            assinatura = AssinaturaOrganizacao.all_objects.select_for_update().get(pk=bloqueada.assinatura_id)
            cls._validar_alteracao_processavel(bloqueada, assinatura=assinatura)
            if assinatura.revisao != bloqueada.revisao_esperada:
                if not permitir_evento_atrasado:
                    raise ConflitoRevisaoAssinatura("A revisão da alteração não é mais a revisão corrente.")
                cls._completar_historico_atrasado(bloqueada, assinatura, evento_gateway=evento_gateway, agora=agora)
                return bloqueada

            bloqueada.status = StatusAlteracaoAssinatura.CONFIRMADA
            bloqueada.processada_em = agora
            bloqueada.evento_gateway = evento_gateway
            deve_aplicar = (
                bloqueada.momento_aplicacao == MomentoAplicacaoAlteracaoAssinatura.IMEDIATA
                or bloqueada.aplicar_em is not None
                and bloqueada.aplicar_em <= agora
            )
            campos = ["status", "processada_em", "evento_gateway", "last_modified_at"]
            if deve_aplicar:
                cls._aplicar_snapshot(bloqueada, assinatura, agora=agora)
                campos.extend(("aplicada_em", "revisao_aplicada"))
            bloqueada.save(update_fields=campos)
            return bloqueada

    @classmethod
    def aplicar_alteracao_agendada(
        cls,
        alteracao: AlteracaoAssinatura,
        *,
        agora: datetime | None = None,
        permitir_evento_atrasado: bool = False,
    ) -> AlteracaoAssinatura:
        """Aplica uma mudança já confirmada quando chega a data do próximo ciclo."""
        agora = agora or timezone.now()
        with cls._alteracao_bloqueada(alteracao) as bloqueada:
            if bloqueada.aplicada_em is not None:
                return bloqueada
            if bloqueada.status != StatusAlteracaoAssinatura.CONFIRMADA:
                raise ConflitoRevisaoAssinatura("Somente alteração confirmada pode ser aplicada.")
            if bloqueada.momento_aplicacao != MomentoAplicacaoAlteracaoAssinatura.PROXIMO_CICLO:
                raise ConflitoRevisaoAssinatura("A alteração não possui aplicação agendada.")
            if bloqueada.aplicar_em is None or bloqueada.aplicar_em > agora:
                raise ConflitoRevisaoAssinatura("A data de aplicação da alteração ainda não chegou.")
            assinatura = AssinaturaOrganizacao.all_objects.select_for_update().get(pk=bloqueada.assinatura_id)
            cls._validar_alteracao_processavel(bloqueada, assinatura=assinatura)
            if assinatura.revisao != bloqueada.revisao_esperada:
                if not permitir_evento_atrasado:
                    raise ConflitoRevisaoAssinatura("A revisão da alteração não é mais a revisão corrente.")
                cls._completar_historico_atrasado(bloqueada, assinatura, evento_gateway=bloqueada.evento_gateway, agora=agora)
                return bloqueada
            cls._aplicar_snapshot(bloqueada, assinatura, agora=agora)
            bloqueada.save(update_fields=["aplicada_em", "revisao_aplicada", "last_modified_at"])
            return bloqueada

    @classmethod
    def falhar_alteracao(
        cls,
        alteracao: AlteracaoAssinatura,
        *,
        codigo: str,
        mensagem: str,
        agora: datetime | None = None,
    ) -> AlteracaoAssinatura:
        """Registra falha normalizada apenas a partir de um estado processável."""
        agora = agora or timezone.now()
        with cls._alteracao_bloqueada(alteracao) as bloqueada:
            if bloqueada.status not in (
                StatusAlteracaoAssinatura.SOLICITADA,
                StatusAlteracaoAssinatura.AGUARDANDO_GATEWAY,
            ):
                raise ConflitoRevisaoAssinatura("A alteração não pode falhar no estado atual.")
            bloqueada.status = StatusAlteracaoAssinatura.FALHOU
            bloqueada.processada_em = agora
            bloqueada.falha_codigo = codigo
            bloqueada.falha_mensagem = mensagem
            bloqueada.save(update_fields=["status", "processada_em", "falha_codigo", "falha_mensagem", "last_modified_at"])
            return bloqueada

    @classmethod
    def cancelar_alteracao(
        cls,
        alteracao: AlteracaoAssinatura,
        *,
        agora: datetime | None = None,
    ) -> AlteracaoAssinatura:
        """Cancela nominalmente uma solicitação ainda não confirmada."""
        agora = agora or timezone.now()
        with cls._alteracao_bloqueada(alteracao) as bloqueada:
            if bloqueada.status not in (
                StatusAlteracaoAssinatura.SOLICITADA,
                StatusAlteracaoAssinatura.AGUARDANDO_GATEWAY,
            ):
                raise ConflitoRevisaoAssinatura("A alteração não pode ser cancelada no estado atual.")
            bloqueada.status = StatusAlteracaoAssinatura.CANCELADA
            bloqueada.processada_em = agora
            bloqueada.save(update_fields=["status", "processada_em", "last_modified_at"])
            return bloqueada

    @classmethod
    def _converter_trial_pago(
        cls,
        assinatura: AssinaturaOrganizacao,
        *,
        pagamento: PagamentoTrialConfirmado,
        ocupacao: OcupacaoSeats,
        agora: datetime,
        using: str,
    ) -> TrialConvertido:
        if assinatura.status != StatusAssinatura.EM_TRIAL:
            equivalente = (
                assinatura.status == StatusAssinatura.ATIVA
                and assinatura.status_financeiro == StatusFinanceiro.REGULAR
                and assinatura.seats_contratados == pagamento.seats_contratados
                and assinatura.periodo_atual_iniciado_em == pagamento.periodo_iniciado_em
                and assinatura.periodo_atual_termina_em == pagamento.periodo_termina_em
            )
            if equivalente:
                return TrialConvertido(assinatura=assinatura)
            raise ConflitoRevisaoAssinatura("O trial ja foi encerrado com outro resultado.")
        cls._validar_trial_pronto_para_encerramento(assinatura, agora)
        if pagamento.periodo_iniciado_em > agora:
            raise ConflitoRevisaoAssinatura("O periodo pago do trial ainda nao iniciou.")
        if pagamento.seats_contratados < ocupacao.consumidos:
            raise ConflitoRevisaoAssinatura("Os seats pagos precisam cobrir o consumo real do trial.")

        assinatura.status = StatusAssinatura.ATIVA
        assinatura.status_financeiro = StatusFinanceiro.REGULAR
        assinatura.seats_contratados = pagamento.seats_contratados
        assinatura.periodo_atual_iniciado_em = pagamento.periodo_iniciado_em
        assinatura.periodo_atual_termina_em = pagamento.periodo_termina_em
        assinatura.carencia_pagamento_iniciada_em = None
        assinatura.carencia_pagamento_termina_em = None
        assinatura.carencia_excesso_seats_iniciada_em = None
        assinatura.carencia_excesso_seats_termina_em = None
        assinatura.revisao += 1
        assinatura._salvar_transicao(
            using=using,
            update_fields=[
                "status",
                "status_financeiro",
                "seats_contratados",
                "periodo_atual_iniciado_em",
                "periodo_atual_termina_em",
                "carencia_pagamento_iniciada_em",
                "carencia_pagamento_termina_em",
                "carencia_excesso_seats_iniciada_em",
                "carencia_excesso_seats_termina_em",
                "revisao",
                "last_modified_at",
            ],
        )
        return TrialConvertido(assinatura=assinatura)

    @classmethod
    def _aplicar_fallback_trial(
        cls,
        assinatura: AssinaturaOrganizacao,
        *,
        resultado: FallbackTrialGratuito,
        termos: TermosAssinatura,
        versao_plano: VersaoPlano,
        ocupacao: OcupacaoSeats,
        agora: datetime,
        using: str,
    ) -> TrialConvertidoParaGratuito:
        chave = f"trial-fallback:{assinatura.pk}"
        existente = (
            AlteracaoAssinatura.all_objects.using(using)
            .select_for_update()
            .filter(organizacao_id=assinatura.organizacao_id, chave_idempotencia=chave)
            .first()
        )
        if existente is not None:
            if existente.tipo != TipoAlteracaoAssinatura.FALLBACK_TRIAL or existente.pedido.get("motivo") != resultado.motivo.value:
                raise ConflitoIdempotenciaAssinatura("O fallback idempotente do trial diverge do resultado original.")
            return TrialConvertidoParaGratuito(assinatura=assinatura, alteracao=existente)
        if assinatura.status != StatusAssinatura.EM_TRIAL:
            raise ConflitoRevisaoAssinatura("O trial ja foi encerrado com outro resultado.")
        cls._validar_trial_pronto_para_encerramento(assinatura, agora)

        snapshot_anterior = cls._snapshot_assinatura(assinatura)
        revisao_anterior = assinatura.revisao
        excesso_real = max(ocupacao.consumidos - termos.seats_contratados, 0)
        carencia_seats_iniciada_em = agora if excesso_real else None
        carencia_seats_termina_em = agora + timedelta(days=termos.carencia_excesso_seats_dias) if excesso_real else None

        snapshot_pretendido = dict(snapshot_anterior)
        snapshot_pretendido.update(
            {
                "versao_plano_id": versao_plano.pk,
                "proposta_comercial_id": None,
                "status": int(StatusAssinatura.ATIVA),
                "status_financeiro": int(StatusFinanceiro.ISENTO),
                "revisao": revisao_anterior + 1,
                **cls._dados_termos(termos),
                "carencia_pagamento_iniciada_em": None,
                "carencia_pagamento_termina_em": None,
                "carencia_excesso_seats_iniciada_em": cls._serializar_data(carencia_seats_iniciada_em),
                "carencia_excesso_seats_termina_em": cls._serializar_data(carencia_seats_termina_em),
            }
        )
        alteracao = AlteracaoAssinatura(
            organizacao_id=assinatura.organizacao_id,
            assinatura=assinatura,
            tipo=TipoAlteracaoAssinatura.FALLBACK_TRIAL,
            momento_aplicacao=MomentoAplicacaoAlteracaoAssinatura.IMEDIATA,
            status=StatusAlteracaoAssinatura.SOLICITADA,
            revisao_esperada=revisao_anterior,
            chave_idempotencia=chave,
            pedido={
                "assinatura_id": assinatura.pk,
                "motivo": resultado.motivo.value,
                "versao_plano_id": versao_plano.pk,
                "seats_consumidos": ocupacao.consumidos,
            },
            snapshot_anterior=snapshot_anterior,
            snapshot_pretendido=snapshot_pretendido,
        )
        alteracao.save(using=using, force_insert=True, _permitir_fallback_trial=True)

        campos_termos = cls._dados_termos(termos)
        for campo, valor in campos_termos.items():
            setattr(assinatura, campo, valor)
        assinatura.versao_plano = versao_plano
        assinatura.proposta_comercial = None
        assinatura.status = StatusAssinatura.ATIVA
        assinatura.status_financeiro = StatusFinanceiro.ISENTO
        assinatura.carencia_pagamento_iniciada_em = None
        assinatura.carencia_pagamento_termina_em = None
        assinatura.carencia_excesso_seats_iniciada_em = carencia_seats_iniciada_em
        assinatura.carencia_excesso_seats_termina_em = carencia_seats_termina_em
        assinatura.revisao = revisao_anterior + 1
        assinatura._salvar_transicao(
            using=using,
            update_fields=[
                "versao_plano",
                "proposta_comercial",
                "status",
                "status_financeiro",
                *campos_termos,
                "carencia_pagamento_iniciada_em",
                "carencia_pagamento_termina_em",
                "carencia_excesso_seats_iniciada_em",
                "carencia_excesso_seats_termina_em",
                "revisao",
                "last_modified_at",
            ],
        )
        alteracao.status = StatusAlteracaoAssinatura.CONFIRMADA
        alteracao.processada_em = agora
        alteracao.aplicada_em = agora
        alteracao.revisao_aplicada = assinatura.revisao
        alteracao.save(
            using=using,
            update_fields=[
                "status",
                "processada_em",
                "aplicada_em",
                "revisao_aplicada",
                "last_modified_at",
            ],
        )
        return TrialConvertidoParaGratuito(assinatura=assinatura, alteracao=alteracao)

    @staticmethod
    def _validar_trial_pronto_para_encerramento(assinatura: AssinaturaOrganizacao, agora: datetime) -> None:
        if assinatura.trial_termina_em is None or agora < assinatura.trial_termina_em:
            raise ConflitoRevisaoAssinatura("O trial ainda nao chegou ao encerramento.")

    @staticmethod
    def _validar_transicao_temporal(assinatura: AssinaturaOrganizacao, agora: datetime) -> None:
        if not isinstance(assinatura, AssinaturaOrganizacao) or assinatura.pk is None:
            raise ValueError("Transicao contratual exige uma assinatura persistida.")
        if not isinstance(agora, datetime) or not timezone.is_aware(agora):
            raise ValueError("Transicao contratual exige um datetime consciente de fuso.")

    @staticmethod
    def _validar_organizacao_contratavel(organizacao: Organizacao) -> None:
        if organizacao.is_deleted or not organizacao.is_active:
            raise ConflitoRevisaoAssinatura("A organização não está ativa.")
        if organizacao.encerramento_solicitado_em is not None:
            raise ConflitoRevisaoAssinatura("A organização possui encerramento pendente.")

    @staticmethod
    def _bloquear_organizacao(organizacao_id: int, *, using: str) -> Organizacao:
        from apps.organizacoes.models import Organizacao

        return Organizacao.all_objects.using(using).select_for_update().get(pk=organizacao_id)

    @classmethod
    @contextmanager
    def _alteracao_bloqueada(cls, alteracao: AlteracaoAssinatura) -> Iterator[AlteracaoAssinatura]:
        """Abre o atomic que preserva a ordem organização -> assinatura -> alteração."""
        if not isinstance(alteracao, AlteracaoAssinatura) or alteracao.pk is None:
            raise ValueError("Transição exige uma alteração persistida.")
        using = alteracao._state.db or "default"
        with transaction.atomic(using=using):
            cls._bloquear_organizacao(alteracao.organizacao_id, using=using)
            AssinaturaOrganizacao.all_objects.using(using).select_for_update().get(pk=alteracao.assinatura_id)
            yield AlteracaoAssinatura.all_objects.using(using).select_for_update().get(pk=alteracao.pk)

    @classmethod
    def _validar_alteracao_processavel(
        cls,
        alteracao: AlteracaoAssinatura,
        *,
        assinatura: AssinaturaOrganizacao | None = None,
    ) -> None:
        using = alteracao._state.db or "default"
        organizacao = cls._bloquear_organizacao(alteracao.organizacao_id, using=using)
        cls._validar_organizacao_contratavel(organizacao)
        assinatura = assinatura or AssinaturaOrganizacao.all_objects.using(using).select_for_update().get(pk=alteracao.assinatura_id)
        if assinatura.status not in STATUS_CORRENTES or assinatura.cancelamento_agendado_para is not None:
            raise ConflitoRevisaoAssinatura("O contrato não aceita alterações durante o cancelamento.")

    @staticmethod
    def _cancelar_alteracoes_pendentes_por_encerramento(
        assinatura: AssinaturaOrganizacao,
        *,
        agora: datetime,
        using: str,
    ) -> None:
        pendentes = list(
            AlteracaoAssinatura.all_objects.using(using)
            .select_for_update()
            .filter(
                assinatura=assinatura,
                status__in=(
                    StatusAlteracaoAssinatura.SOLICITADA,
                    StatusAlteracaoAssinatura.AGUARDANDO_GATEWAY,
                    StatusAlteracaoAssinatura.CONFIRMADA,
                ),
                aplicada_em__isnull=True,
                ignorada_em__isnull=True,
            )
            .order_by("pk")
        )
        for alteracao in pendentes:
            alteracao.status = StatusAlteracaoAssinatura.CANCELADA
            alteracao.processada_em = agora
            alteracao.save(update_fields=["status", "processada_em", "last_modified_at"])

    @staticmethod
    def _momento_padrao(tipo: TipoAlteracaoAssinatura) -> MomentoAplicacaoAlteracaoAssinatura:
        if tipo in (TipoAlteracaoAssinatura.UPGRADE_PLANO, TipoAlteracaoAssinatura.AUMENTO_SEATS):
            return MomentoAplicacaoAlteracaoAssinatura.IMEDIATA
        return MomentoAplicacaoAlteracaoAssinatura.PROXIMO_CICLO

    @staticmethod
    def _resolver_data_aplicacao(
        comando: CriacaoAlteracaoAssinatura,
        assinatura: AssinaturaOrganizacao,
        momento: MomentoAplicacaoAlteracaoAssinatura,
    ) -> datetime | None:
        if momento == MomentoAplicacaoAlteracaoAssinatura.IMEDIATA:
            if comando.aplicar_em is not None:
                raise ValueError("Alteração imediata não recebe data futura de aplicação.")
            return None
        aplicar_em = comando.aplicar_em or assinatura.periodo_atual_termina_em
        if aplicar_em is None:
            raise ValueError("Alteração do próximo ciclo exige uma data de aplicação.")
        if aplicar_em != assinatura.periodo_atual_termina_em:
            raise ValueError("Alteração do próximo ciclo deve ser aplicada no fim do período atual.")
        return aplicar_em

    @staticmethod
    def _validar_mudanca(comando: CriacaoAlteracaoAssinatura, assinatura: AssinaturaOrganizacao) -> None:
        termos = comando.termos_pretendidos
        versao_pretendida = comando.origem_pretendida.versao_plano
        if comando.tipo in (TipoAlteracaoAssinatura.AUMENTO_SEATS, TipoAlteracaoAssinatura.REDUCAO_SEATS):
            esperado = Assinaturas._dados_termos_atuais(assinatura)
            esperado["seats_contratados"] = termos.seats_contratados
            if versao_pretendida.pk != assinatura.versao_plano_id or Assinaturas._dados_termos(termos) != esperado:
                raise ValueError("Alteração de seats pode mudar somente seats_contratados.")
            if comando.tipo == TipoAlteracaoAssinatura.AUMENTO_SEATS:
                if termos.seats_contratados <= assinatura.seats_contratados:
                    raise ValueError("Aumento de seats exige uma quantidade absoluta maior.")
            else:
                if termos.seats_contratados >= assinatura.seats_contratados:
                    raise ValueError("Redução de seats exige uma quantidade absoluta menor.")
                if comando.seats_consumidos is None or termos.seats_contratados < comando.seats_consumidos:
                    raise ValueError("A capacidade pretendida deve cobrir o consumo efetivo de seats.")
            return

        if versao_pretendida.is_deleted or not versao_pretendida.is_active or versao_pretendida.publicada_em is None:
            raise ValueError("Versão pretendida precisa estar ativa.")
        if comando.tipo == TipoAlteracaoAssinatura.MUDANCA_PERIODICIDADE:
            if versao_pretendida.pk != assinatura.versao_plano_id or termos.periodicidade == assinatura.periodicidade:
                raise ValueError("Mudança de periodicidade exige outra periodicidade do mesmo plano.")
            Assinaturas._validar_termos_publicados(assinatura, versao_pretendida, termos)
        elif comando.tipo in (TipoAlteracaoAssinatura.UPGRADE_PLANO, TipoAlteracaoAssinatura.DOWNGRADE_PLANO):
            if versao_pretendida.pk == assinatura.versao_plano_id:
                raise ValueError("Mudança de plano exige outra versão de plano.")
            if termos.periodicidade != assinatura.periodicidade:
                raise ValueError("Mudança de plano exige termos publicados sem mascarar periodicidade.")
            Assinaturas._validar_termos_publicados(assinatura, versao_pretendida, termos)

    @classmethod
    def _validar_termos_publicados(
        cls,
        assinatura: AssinaturaOrganizacao,
        versao_pretendida: VersaoPlano,
        termos: TermosAssinatura,
    ) -> None:
        preco = (
            PrecoPlano.objects.filter(
                versao_plano=versao_pretendida,
                periodicidade=termos.periodicidade,
                moeda=assinatura.moeda,
                is_active=True,
            )
            .order_by("pk")
            .first()
        )
        if preco is None:
            raise ValueError("Alteração exige preço publicado ativo para os termos pretendidos.")
        esperados = cls._termos_catalogo(
            versao_pretendida,
            preco,
            seats_contratados=assinatura.seats_contratados,
        )
        if cls._dados_termos(termos) != cls._dados_termos(esperados):
            raise ValueError("Alteração exige os termos publicados sem mudanças mascaradas.")

    @staticmethod
    def _dados_termos(termos: TermosAssinatura) -> dict[str, Any]:
        return {
            "periodicidade": int(termos.periodicidade),
            "moeda": termos.moeda,
            "valor_base_centavos": termos.valor_base_centavos,
            "valor_seat_centavos": termos.valor_seat_centavos,
            "seats_inclusos": termos.seats_inclusos,
            "seats_contratados": termos.seats_contratados,
            "expansao_automatica_seats": termos.expansao_automatica_seats,
            "recursos": termos.recursos.materializar(),
            "carencia_pagamento_dias": termos.carencia_pagamento_dias,
            "carencia_excesso_seats_dias": termos.carencia_excesso_seats_dias,
        }

    @staticmethod
    def _dados_termos_atuais(assinatura: AssinaturaOrganizacao) -> dict[str, Any]:
        return {
            "periodicidade": int(assinatura.periodicidade),
            "moeda": assinatura.moeda,
            "valor_base_centavos": assinatura.valor_base_centavos,
            "valor_seat_centavos": assinatura.valor_seat_centavos,
            "seats_inclusos": assinatura.seats_inclusos,
            "seats_contratados": assinatura.seats_contratados,
            "expansao_automatica_seats": assinatura.expansao_automatica_seats,
            "recursos": CATALOGO_RECURSOS.validar_snapshot(assinatura.recursos).materializar(),
            "carencia_pagamento_dias": assinatura.carencia_pagamento_dias,
            "carencia_excesso_seats_dias": assinatura.carencia_excesso_seats_dias,
        }

    @staticmethod
    def _termos_atuais(assinatura: AssinaturaOrganizacao, *, seats_contratados: int) -> TermosAssinatura:
        return TermosAssinatura(
            periodicidade=Periodicidade(assinatura.periodicidade),
            moeda=assinatura.moeda,
            valor_base_centavos=assinatura.valor_base_centavos,
            valor_seat_centavos=assinatura.valor_seat_centavos,
            seats_inclusos=assinatura.seats_inclusos,
            seats_contratados=seats_contratados,
            expansao_automatica_seats=assinatura.expansao_automatica_seats,
            recursos=ValoresRecursos(CATALOGO_RECURSOS, assinatura.recursos),
            carencia_pagamento_dias=assinatura.carencia_pagamento_dias,
            carencia_excesso_seats_dias=assinatura.carencia_excesso_seats_dias,
        )

    @classmethod
    def _pedido_alteracao(
        cls,
        comando: CriacaoAlteracaoAssinatura,
        *,
        momento: MomentoAplicacaoAlteracaoAssinatura,
        aplicar_em: datetime | None,
    ) -> dict[str, Any]:
        termos = comando.termos_pretendidos
        return {
            "organizacao_id": comando.assinatura.organizacao_id,
            "assinatura_id": comando.assinatura.pk,
            "tipo": int(comando.tipo),
            "momento_aplicacao": int(momento),
            "revisao_esperada": comando.revisao_esperada,
            "chave_idempotencia": comando.chave_idempotencia,
            "solicitada_por_id": comando.solicitada_por.pk if comando.solicitada_por is not None else None,
            "versao_plano_id": comando.origem_pretendida.versao_plano.pk,
            "periodicidade": int(termos.periodicidade),
            "moeda": termos.moeda,
            "valor_base_centavos": termos.valor_base_centavos,
            "valor_seat_centavos": termos.valor_seat_centavos,
            "seats_inclusos": termos.seats_inclusos,
            "seats_contratados": termos.seats_contratados,
            "expansao_automatica_seats": termos.expansao_automatica_seats,
            "recursos": termos.recursos.materializar(),
            "carencia_pagamento_dias": termos.carencia_pagamento_dias,
            "carencia_excesso_seats_dias": termos.carencia_excesso_seats_dias,
            "aplicar_em": cls._serializar_data(aplicar_em),
            "seats_consumidos": comando.seats_consumidos,
        }

    @classmethod
    def _snapshot_assinatura(cls, assinatura: AssinaturaOrganizacao) -> dict[str, Any]:
        return {
            "versao_plano_id": assinatura.versao_plano_id,
            "proposta_comercial_id": assinatura.proposta_comercial_id,
            "status": int(assinatura.status),
            "status_financeiro": int(assinatura.status_financeiro),
            "revisao": assinatura.revisao,
            "periodicidade": int(assinatura.periodicidade),
            "moeda": assinatura.moeda,
            "valor_base_centavos": assinatura.valor_base_centavos,
            "valor_seat_centavos": assinatura.valor_seat_centavos,
            "seats_inclusos": assinatura.seats_inclusos,
            "seats_contratados": assinatura.seats_contratados,
            "expansao_automatica_seats": assinatura.expansao_automatica_seats,
            "recursos": assinatura.recursos,
            "politica_trial": int(assinatura.politica_trial) if assinatura.politica_trial is not None else None,
            "trial_iniciado_em": cls._serializar_data(assinatura.trial_iniciado_em),
            "trial_termina_em": cls._serializar_data(assinatura.trial_termina_em),
            "periodo_atual_iniciado_em": cls._serializar_data(assinatura.periodo_atual_iniciado_em),
            "periodo_atual_termina_em": cls._serializar_data(assinatura.periodo_atual_termina_em),
            "carencia_pagamento_dias": assinatura.carencia_pagamento_dias,
            "carencia_pagamento_iniciada_em": cls._serializar_data(assinatura.carencia_pagamento_iniciada_em),
            "carencia_pagamento_termina_em": cls._serializar_data(assinatura.carencia_pagamento_termina_em),
            "carencia_excesso_seats_dias": assinatura.carencia_excesso_seats_dias,
            "carencia_excesso_seats_iniciada_em": cls._serializar_data(assinatura.carencia_excesso_seats_iniciada_em),
            "carencia_excesso_seats_termina_em": cls._serializar_data(assinatura.carencia_excesso_seats_termina_em),
            "cancelamento_agendado_para": cls._serializar_data(assinatura.cancelamento_agendado_para),
            "encerrada_em": cls._serializar_data(assinatura.encerrada_em),
            "motivo_encerramento": assinatura.motivo_encerramento,
        }

    @classmethod
    def _snapshot_pretendido(
        cls,
        assinatura: AssinaturaOrganizacao,
        comando: CriacaoAlteracaoAssinatura,
    ) -> dict[str, Any]:
        snapshot = cls._snapshot_assinatura(assinatura)
        termos = comando.termos_pretendidos
        snapshot.update(
            {
                "versao_plano_id": comando.origem_pretendida.versao_plano.pk,
                "proposta_comercial_id": None,
                "revisao": comando.revisao_esperada + 1,
                "periodicidade": int(termos.periodicidade),
                "moeda": termos.moeda,
                "valor_base_centavos": termos.valor_base_centavos,
                "valor_seat_centavos": termos.valor_seat_centavos,
                "seats_inclusos": termos.seats_inclusos,
                "seats_contratados": termos.seats_contratados,
                "expansao_automatica_seats": termos.expansao_automatica_seats,
                "recursos": termos.recursos.materializar(),
                "carencia_pagamento_dias": termos.carencia_pagamento_dias,
                "carencia_excesso_seats_dias": termos.carencia_excesso_seats_dias,
            }
        )
        return snapshot

    @staticmethod
    def _serializar_data(valor: datetime | None) -> str | None:
        return valor.isoformat() if valor is not None else None

    @classmethod
    def _aplicar_snapshot(
        cls,
        alteracao: AlteracaoAssinatura,
        assinatura: AssinaturaOrganizacao,
        *,
        agora: datetime,
    ) -> None:
        pretendido = alteracao.snapshot_pretendido
        campos = (
            "versao_plano_id",
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
        )
        for campo in campos:
            setattr(assinatura, campo, pretendido[campo])
        assinatura.revisao = alteracao.revisao_esperada + 1
        assinatura._salvar_transicao(update_fields=[*campos, "revisao", "last_modified_at"])
        alteracao.aplicada_em = agora
        alteracao.revisao_aplicada = assinatura.revisao

    @staticmethod
    def _completar_historico_atrasado(
        alteracao: AlteracaoAssinatura,
        assinatura: AssinaturaOrganizacao,
        *,
        evento_gateway: str | None,
        agora: datetime,
    ) -> None:
        alteracao.status = StatusAlteracaoAssinatura.CONFIRMADA
        alteracao.processada_em = agora
        alteracao.evento_gateway = evento_gateway
        alteracao.revisao_observada = assinatura.revisao
        alteracao.ignorada_em = agora
        alteracao.save(
            update_fields=[
                "status",
                "processada_em",
                "evento_gateway",
                "revisao_observada",
                "ignorada_em",
                "last_modified_at",
            ]
        )

    @staticmethod
    def _termos_catalogo(
        versao_plano: VersaoPlano,
        preco_plano: PrecoPlano,
        *,
        seats_contratados: int,
    ) -> TermosAssinatura:
        if not isinstance(versao_plano, VersaoPlano) or not isinstance(preco_plano, PrecoPlano):
            raise ValueError("Preset exige VersaoPlano e PrecoPlano.")
        if preco_plano.versao_plano_id != versao_plano.pk:
            raise ValueError("O preço não pertence à versão informada.")
        if versao_plano.is_deleted or not versao_plano.is_active or preco_plano.is_deleted or not preco_plano.is_active:
            raise ValueError("Versão e preço precisam estar ativos.")
        return TermosAssinatura(
            periodicidade=Periodicidade(preco_plano.periodicidade),
            moeda=preco_plano.moeda,
            valor_base_centavos=preco_plano.valor_base_centavos,
            valor_seat_centavos=preco_plano.valor_seat_centavos,
            seats_inclusos=versao_plano.seats_inclusos,
            seats_contratados=seats_contratados,
            expansao_automatica_seats=versao_plano.expansao_automatica_seats,
            recursos=ValoresRecursos(CATALOGO_RECURSOS, versao_plano.recursos),
            carencia_pagamento_dias=versao_plano.carencia_pagamento_dias,
            carencia_excesso_seats_dias=versao_plano.carencia_excesso_seats_dias,
        )

    @staticmethod
    def _chave_preset(organizacao: Organizacao, versao_plano: VersaoPlano, preco_plano: PrecoPlano, preset: str) -> str:
        return f"assinatura:{organizacao.pk}:{preset}:{versao_plano.pk}:{preco_plano.pk}"

    @staticmethod
    def _equivale_ao_comando(assinatura: AssinaturaOrganizacao, comando: CriacaoAssinatura) -> bool:
        termos = comando.termos
        esperado = {
            "versao_plano_id": comando.origem.versao_plano.pk,
            "proposta_comercial_id": None,
            "status": comando.status,
            "status_financeiro": comando.status_financeiro,
            "periodicidade": termos.periodicidade,
            "moeda": termos.moeda,
            "valor_base_centavos": termos.valor_base_centavos,
            "valor_seat_centavos": termos.valor_seat_centavos,
            "seats_inclusos": termos.seats_inclusos,
            "seats_contratados": termos.seats_contratados,
            "expansao_automatica_seats": termos.expansao_automatica_seats,
            "recursos": termos.recursos.materializar(),
            "politica_trial": comando.politica_trial,
            "trial_termina_em": comando.trial_termina_em,
            "carencia_pagamento_dias": termos.carencia_pagamento_dias,
            "carencia_excesso_seats_dias": termos.carencia_excesso_seats_dias,
        }
        return all(getattr(assinatura, campo) == valor for campo, valor in esperado.items())


def _criar_assinatura_enterprise_de_proposta(
    *,
    proposta_comercial: PropostaComercial,
    agora: datetime,
) -> AssinaturaOrganizacao:
    """Cria o ciclo enterprise exclusivamente para a ativação nominal."""
    from apps.organizacoes.context import organizacao_atual_privilegiada
    from apps.organizacoes.models import Organizacao

    if not isinstance(proposta_comercial, PropostaComercial) or proposta_comercial.pk is None:
        raise ValueError("Criação enterprise exige uma proposta persistida.")
    using = proposta_comercial._state.db or "default"
    chave = f"assinatura:proposta:{proposta_comercial.pk}"

    with transaction.atomic(using=using):
        organizacao = Organizacao.all_objects.using(using).select_for_update().get(pk=proposta_comercial.organizacao_id)
        Assinaturas._validar_organizacao_contratavel(organizacao)
        with organizacao_atual_privilegiada(organizacao.pk):
            corrente = Assinaturas.obter_corrente(organizacao, bloquear=True)
            proposta = PropostaComercial.all_objects.using(using).select_for_update().get(pk=proposta_comercial.pk)
            if proposta.organizacao_id != organizacao.pk:
                raise ConflitoRevisaoAssinatura("A proposta pertence a outra organização.")
            if proposta.modo_ativacao != ModoAtivacaoProposta.CONTRATUAL:
                raise ConflitoRevisaoAssinatura("A criação enterprise exige proposta contratual.")
            if proposta.valida_ate <= agora:
                raise ConflitoRevisaoAssinatura("A proposta enterprise expirou.")
            if proposta.status not in (StatusPropostaComercial.ACEITA, StatusPropostaComercial.ATIVADA):
                raise ConflitoRevisaoAssinatura("A proposta precisa estar aceita para criar o ciclo enterprise.")

            existente = (
                AssinaturaOrganizacao.all_objects.using(using).select_for_update().filter(organizacao=organizacao, chave_idempotencia=chave).first()
            )
            if existente is not None:
                if (
                    existente.versao_plano_id is not None
                    or existente.proposta_comercial_id != proposta.pk
                    or existente.status != StatusAssinatura.ATIVA
                    or existente.status_financeiro != StatusFinanceiro.ISENTO
                ):
                    raise ConflitoIdempotenciaAssinatura("O ciclo enterprise existente diverge da proposta contratual.")
                return existente
            if proposta.status != StatusPropostaComercial.ACEITA:
                raise ConflitoIdempotenciaAssinatura("Proposta ativada não possui ciclo enterprise compatível.")

            if corrente is not None:
                Assinaturas._cancelar_alteracoes_pendentes_por_encerramento(corrente, agora=agora, using=using)
                corrente.status = StatusAssinatura.ENCERRADA
                corrente.revisao += 1
                corrente.cancelamento_agendado_para = None
                corrente.encerrada_em = agora
                corrente.motivo_encerramento = "proposal_replaced"
                corrente._salvar_transicao(
                    update_fields=[
                        "status",
                        "revisao",
                        "cancelamento_agendado_para",
                        "encerrada_em",
                        "motivo_encerramento",
                        "last_modified_at",
                    ]
                )

            termos = TermosAssinatura(
                periodicidade=Periodicidade(proposta.periodicidade),
                moeda=proposta.moeda,
                valor_base_centavos=proposta.valor_base_centavos,
                valor_seat_centavos=proposta.valor_seat_centavos,
                seats_inclusos=proposta.seats_inclusos,
                seats_contratados=proposta.seats_contratados,
                expansao_automatica_seats=proposta.expansao_automatica_seats,
                recursos=ValoresRecursos(CATALOGO_RECURSOS, proposta.recursos),
                carencia_pagamento_dias=proposta.carencia_pagamento_dias,
                carencia_excesso_seats_dias=proposta.carencia_excesso_seats_dias,
            )
            return Assinaturas._persistir_assinatura(
                organizacao=organizacao,
                versao_plano=None,
                proposta_comercial=proposta,
                termos=termos,
                status=StatusAssinatura.ATIVA,
                status_financeiro=StatusFinanceiro.ISENTO,
                politica_trial=None,
                trial_termina_em=None,
                chave_idempotencia=chave,
                agora=agora,
                using=using,
            )
