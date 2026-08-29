"""Casos de uso tipados para contratos e alterações de assinatura."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from django.db import transaction
from django.utils import timezone

from apps.api.core.errors import APIError
from apps.assinaturas.errors import BillingErrorCode
from apps.assinaturas.features import CATALOGO_RECURSOS, PAPEIS_ISENTOS_SEAT, ValoresRecursos
from apps.assinaturas.models import (
    AlteracaoAssinatura,
    AssinaturaOrganizacao,
    MomentoAplicacaoAlteracaoAssinatura,
    Periodicidade,
    PoliticaTrial,
    PrecoPlano,
    StatusAlteracaoAssinatura,
    StatusAssinatura,
    StatusFinanceiro,
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


@dataclass(frozen=True)
class OrigemVersaoPlano:
    versao_plano: VersaoPlano

    def __post_init__(self):
        if not isinstance(self.versao_plano, VersaoPlano):
            raise ValueError("Origem de catálogo exige uma VersaoPlano.")


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
    origem_pretendida: OrigemAssinatura
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
        if not isinstance(assinatura, AssinaturaOrganizacao):
            raise ValueError("Utilização exige uma assinatura.")
        consumidos = getattr(ocupacao, "consumidos", None)
        reservados = getattr(ocupacao, "reservados", None)
        if type(consumidos) is not int or consumidos < 0 or type(reservados) is not int or reservados < 0:
            raise ValueError("Ocupação exige totais inteiros não negativos.")
        comprometidos = consumidos + reservados
        contratados = assinatura.seats_contratados
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
        if assinatura.total_centavos > 0 and assinatura.periodo_atual_termina_em is not None:
            return TermoEncerramentoAgendado(agendado_para=assinatura.periodo_atual_termina_em)
        return TermoEncerramentoImediato()

    @classmethod
    def encerrar(cls, organizacao: Organizacao, *, encerrada_em: datetime) -> None:
        """Encerra idempotentemente o contrato sob o lock da organização chamadora."""
        using = organizacao._state.db or "default"
        with transaction.atomic(using=using):
            assinatura = cls.obter_corrente(organizacao, bloquear=True)
            if assinatura is None:
                encerrada = (
                    AssinaturaOrganizacao.all_objects.using(using)
                    .select_for_update()
                    .filter(organizacao=organizacao, status=StatusAssinatura.ENCERRADA)
                    .first()
                )
                if encerrada is not None:
                    return
                raise APIError(BillingErrorCode.SUBSCRIPTION_REQUIRED, status_code=503)
            assinatura.status = StatusAssinatura.ENCERRADA
            assinatura.revisao += 1
            assinatura.cancelamento_agendado_para = None
            assinatura.encerrada_em = encerrada_em
            assinatura.motivo_encerramento = "organization_closed"
            assinatura.save(
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

            termos = comando.termos
            return AssinaturaOrganizacao.objects.using(using).create(
                organizacao=organizacao_bloqueada,
                versao_plano=comando.origem.versao_plano,
                status=comando.status,
                status_financeiro=comando.status_financeiro,
                revisao=1,
                periodicidade=termos.periodicidade,
                moeda=termos.moeda,
                valor_base_centavos=termos.valor_base_centavos,
                valor_seat_centavos=termos.valor_seat_centavos,
                seats_inclusos=termos.seats_inclusos,
                seats_contratados=termos.seats_contratados,
                expansao_automatica_seats=termos.expansao_automatica_seats,
                recursos=termos.recursos.materializar(),
                politica_trial=comando.politica_trial,
                trial_iniciado_em=agora if comando.status == StatusAssinatura.EM_TRIAL else None,
                trial_termina_em=comando.trial_termina_em,
                carencia_pagamento_dias=termos.carencia_pagamento_dias,
                carencia_excesso_seats_dias=termos.carencia_excesso_seats_dias,
                chave_idempotencia=comando.chave_idempotencia,
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
        agora = agora or organizacao.created_at
        termos = cls._termos_catalogo(
            versao_plano,
            preco_plano,
            seats_contratados=versao_plano.limite_seats_trial,
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
                chave_idempotencia=chave_idempotencia or cls._chave_preset(organizacao, versao_plano, preco_plano, "trial"),
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

        with transaction.atomic(using=using):
            organizacao = cls._bloquear_organizacao(assinatura_informada.organizacao_id, using=using)
            cls._validar_organizacao_contratavel(organizacao)
            assinatura = AssinaturaOrganizacao.all_objects.using(using).select_for_update().get(pk=assinatura_informada.pk, organizacao=organizacao)
            pedido = cls._pedido_alteracao(comando)
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
            if assinatura.revisao != comando.revisao_esperada:
                raise ConflitoRevisaoAssinatura("A revisão esperada não corresponde ao contrato corrente.")
            momento = cls._momento_padrao(comando.tipo)
            aplicar_em = cls._resolver_data_aplicacao(comando, assinatura, momento)
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
                solicitada_por=comando.solicitada_por,
                pedido=pedido,
                snapshot_anterior=snapshot_anterior,
                snapshot_pretendido=snapshot_pretendido,
                aplicar_em=aplicar_em,
            )

    @classmethod
    def marcar_aguardando_gateway(cls, alteracao: AlteracaoAssinatura) -> AlteracaoAssinatura:
        """Executa a transição nominal que antecede uma confirmação externa."""
        with cls._alteracao_bloqueada(alteracao) as bloqueada:
            if bloqueada.status == StatusAlteracaoAssinatura.AGUARDANDO_GATEWAY:
                return bloqueada
            if bloqueada.status != StatusAlteracaoAssinatura.SOLICITADA:
                raise ConflitoRevisaoAssinatura("A alteração não pode aguardar gateway no estado atual.")
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
        return aplicar_em

    @staticmethod
    def _validar_mudanca(comando: CriacaoAlteracaoAssinatura, assinatura: AssinaturaOrganizacao) -> None:
        termos = comando.termos_pretendidos
        if comando.origem_pretendida.versao_plano.is_deleted or not comando.origem_pretendida.versao_plano.is_active:
            raise ValueError("Versão pretendida precisa estar ativa.")
        if comando.tipo == TipoAlteracaoAssinatura.AUMENTO_SEATS and termos.seats_contratados <= assinatura.seats_contratados:
            raise ValueError("Aumento de seats exige uma quantidade absoluta maior.")
        if comando.tipo == TipoAlteracaoAssinatura.REDUCAO_SEATS:
            if termos.seats_contratados >= assinatura.seats_contratados:
                raise ValueError("Redução de seats exige uma quantidade absoluta menor.")
            if comando.seats_consumidos is None or termos.seats_contratados < comando.seats_consumidos:
                raise ValueError("A capacidade pretendida deve cobrir o consumo efetivo de seats.")
        if comando.tipo == TipoAlteracaoAssinatura.MUDANCA_PERIODICIDADE and termos.periodicidade == assinatura.periodicidade:
            raise ValueError("Mudança de periodicidade exige uma periodicidade diferente.")
        if comando.tipo in (TipoAlteracaoAssinatura.UPGRADE_PLANO, TipoAlteracaoAssinatura.DOWNGRADE_PLANO):
            if comando.origem_pretendida.versao_plano.pk == assinatura.versao_plano_id:
                raise ValueError("Mudança de plano exige outra versão de plano.")

    @classmethod
    def _pedido_alteracao(cls, comando: CriacaoAlteracaoAssinatura) -> dict[str, Any]:
        termos = comando.termos_pretendidos
        return {
            "tipo": int(comando.tipo),
            "revisao_esperada": comando.revisao_esperada,
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
            "aplicar_em": cls._serializar_data(comando.aplicar_em),
            "seats_consumidos": comando.seats_consumidos,
        }

    @classmethod
    def _snapshot_assinatura(cls, assinatura: AssinaturaOrganizacao) -> dict[str, Any]:
        return {
            "versao_plano_id": assinatura.versao_plano_id,
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
        assinatura.save(update_fields=[*campos, "revisao", "last_modified_at"])
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
