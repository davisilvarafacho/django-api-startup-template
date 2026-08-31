"""Casos de uso tipados para propostas comerciais enterprise."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from django.db import transaction
from django.utils import timezone

from apps.api.autenticacao.mfa import active_factors, consume_totp
from apps.api.autenticacao.models import MFAFactorType
from apps.api.autenticacao.services import lock_user_accounts
from apps.assinaturas.features import CATALOGO_RECURSOS, ValoresRecursos
from apps.assinaturas.models import (
    AssinaturaOrganizacao,
    ModoAtivacaoProposta,
    Periodicidade,
    PropostaComercial,
    StatusFinanceiro,
    StatusPropostaComercial,
    VersaoPlano,
)
from apps.assinaturas.subscriptions import STATUS_CORRENTES, Assinaturas, TermosAssinatura

if TYPE_CHECKING:
    from apps.organizacoes.models import Organizacao
    from apps.usuarios.models import Usuario


class ConflitoPropostaComercial(ValueError):
    """A proposta divergiu da revisao, validade ou transicao esperada."""


@dataclass(frozen=True)
class CriacaoPropostaComercial:
    organizacao: Organizacao
    versao_plano_referencia: VersaoPlano | None
    modo_ativacao: ModoAtivacaoProposta
    termos: TermosAssinatura
    valida_ate: datetime
    criada_por: Usuario | None = None

    def __post_init__(self):
        if getattr(self.organizacao, "pk", None) is None:
            raise ValueError("Organizacao da proposta precisa estar persistida.")
        if self.versao_plano_referencia is not None and not isinstance(self.versao_plano_referencia, VersaoPlano):
            raise ValueError("Versao de referencia precisa ser uma VersaoPlano.")
        if not isinstance(self.modo_ativacao, ModoAtivacaoProposta):
            raise ValueError("Modo de ativacao precisa ser um ModoAtivacaoProposta concreto.")
        if not isinstance(self.termos, TermosAssinatura):
            raise ValueError("Termos da proposta precisam ser TermosAssinatura.")
        if not isinstance(self.valida_ate, datetime):
            raise ValueError("Validade da proposta precisa ser um datetime.")
        if self.criada_por is not None and getattr(self.criada_por, "pk", None) is None:
            raise ValueError("Criador da proposta precisa estar persistido.")


@dataclass(frozen=True)
class EdicaoPropostaComercial:
    proposta: PropostaComercial
    revisao_esperada: int
    termos: TermosAssinatura
    valida_ate: datetime
    versao_plano_referencia: VersaoPlano | None = None
    modo_ativacao: ModoAtivacaoProposta | None = None

    def __post_init__(self):
        if not isinstance(self.proposta, PropostaComercial) or self.proposta.pk is None:
            raise ValueError("Edicao exige uma proposta persistida.")
        _validar_revisao(self.revisao_esperada)
        if not isinstance(self.termos, TermosAssinatura):
            raise ValueError("Termos da proposta precisam ser TermosAssinatura.")
        if not isinstance(self.valida_ate, datetime):
            raise ValueError("Validade da proposta precisa ser um datetime.")
        if self.versao_plano_referencia is not None and not isinstance(self.versao_plano_referencia, VersaoPlano):
            raise ValueError("Versao de referencia precisa ser uma VersaoPlano.")
        if self.modo_ativacao is not None and not isinstance(self.modo_ativacao, ModoAtivacaoProposta):
            raise ValueError("Modo de ativacao precisa ser um ModoAtivacaoProposta concreto.")


@dataclass(frozen=True)
class PreparacaoCheckoutProposta:
    """Intencao local fechada que a Task 13 consumira para abrir checkout."""

    proposta_id: int
    organizacao_id: int
    revisao: int
    moeda: str
    total_centavos: int


@dataclass(frozen=True)
class ResultadoAceiteProposta:
    proposta: PropostaComercial
    preparacao_checkout: PreparacaoCheckoutProposta | None


def _validar_revisao(revisao: int) -> None:
    if type(revisao) is not int or revisao < 1:
        raise ValueError("Revisao esperada deve ser um inteiro positivo.")


class Propostas:
    """Orquestra transicoes nominais sem expor CRUD generico."""

    @classmethod
    def criar(cls, comando: CriacaoPropostaComercial, *, agora: datetime | None = None) -> PropostaComercial:
        if not isinstance(comando, CriacaoPropostaComercial):
            raise ValueError("Criacao exige CriacaoPropostaComercial.")
        agora = agora or timezone.now()
        if comando.valida_ate <= agora:
            raise ConflitoPropostaComercial("A validade da proposta precisa estar no futuro.")
        using = comando.organizacao._state.db or "default"
        from apps.organizacoes.context import organizacao_atual_privilegiada

        with transaction.atomic(using=using):
            usuarios = lock_user_accounts([comando.criada_por], using=using)
            organizacao = cls._bloquear_organizacao(comando.organizacao.pk, using=using)
            cls._validar_organizacao(organizacao)
            criada_por = usuarios.get(comando.criada_por.pk) if comando.criada_por is not None else None
            with organizacao_atual_privilegiada(organizacao.pk):
                termos = comando.termos
                return PropostaComercial.objects.using(using).create(
                    organizacao=organizacao,
                    versao_plano_referencia=comando.versao_plano_referencia,
                    status=StatusPropostaComercial.RASCUNHO,
                    modo_ativacao=comando.modo_ativacao,
                    revisao=1,
                    valida_ate=comando.valida_ate,
                    created_by=criada_por,
                    **cls._dados_termos(termos),
                )

    @classmethod
    def editar_rascunho(cls, comando: EdicaoPropostaComercial) -> PropostaComercial:
        if not isinstance(comando, EdicaoPropostaComercial):
            raise ValueError("Edicao exige EdicaoPropostaComercial.")
        with cls._proposta_bloqueada(comando.proposta) as proposta:
            cls._validar_revisao(proposta, comando.revisao_esperada)
            if proposta.status != StatusPropostaComercial.RASCUNHO:
                raise ConflitoPropostaComercial("Somente proposta em rascunho pode ser editada.")
            if comando.valida_ate <= timezone.now():
                raise ConflitoPropostaComercial("A validade da proposta precisa estar no futuro.")
            cls._aplicar_termos(proposta, comando.termos)
            if comando.versao_plano_referencia is not None:
                proposta.versao_plano_referencia = comando.versao_plano_referencia
            if comando.modo_ativacao is not None:
                proposta.modo_ativacao = comando.modo_ativacao
            proposta.valida_ate = comando.valida_ate
            proposta.revisao += 1
            proposta.save(
                update_fields=[
                    "versao_plano_referencia",
                    "modo_ativacao",
                    *cls._campos_termos(),
                    "valida_ate",
                    "revisao",
                    "last_modified_at",
                ]
            )
            return proposta

    @classmethod
    def enviar(
        cls,
        proposta: PropostaComercial,
        *,
        revisao_esperada: int,
        agora: datetime | None = None,
    ) -> PropostaComercial:
        _validar_revisao(revisao_esperada)
        agora = agora or timezone.now()
        with cls._proposta_bloqueada(proposta) as bloqueada:
            cls._validar_revisao(bloqueada, revisao_esperada)
            if bloqueada.status != StatusPropostaComercial.RASCUNHO:
                raise ConflitoPropostaComercial("Somente proposta em rascunho pode ser enviada.")
            cls._validar_vigente(bloqueada, agora=agora)
            bloqueada.status = StatusPropostaComercial.ENVIADA
            bloqueada.enviada_em = agora
            bloqueada.revisao += 1
            bloqueada.save(update_fields=["status", "enviada_em", "revisao", "last_modified_at"])
            return bloqueada

    @classmethod
    def aceitar(
        cls,
        proposta: PropostaComercial,
        *,
        ator: Usuario,
        revisao_esperada: int,
        agora: datetime | None = None,
    ) -> ResultadoAceiteProposta:
        _validar_revisao(revisao_esperada)
        agora = agora or timezone.now()
        with cls._estado_bloqueado(proposta, usuarios=(ator,)) as (bloqueada, usuarios):
            ator_bloqueado = usuarios[ator.pk]
            cls._validar_proprietario(bloqueada, ator_id=ator_bloqueado.pk)
            cls._validar_vigente(bloqueada, agora=agora)
            if bloqueada.status == StatusPropostaComercial.ACEITA:
                if bloqueada.aceita_por_id != ator_bloqueado.pk or revisao_esperada not in (bloqueada.revisao, bloqueada.revisao - 1):
                    raise ConflitoPropostaComercial("A revisão da proposta mudou.")
                return cls._resultado_aceite(bloqueada)
            cls._validar_revisao(bloqueada, revisao_esperada)
            if bloqueada.status != StatusPropostaComercial.ENVIADA:
                raise ConflitoPropostaComercial("A proposta nao esta disponivel para aceite.")
            bloqueada.status = StatusPropostaComercial.ACEITA
            bloqueada.aceita_em = agora
            bloqueada.aceita_por = ator_bloqueado
            bloqueada.revisao += 1
            bloqueada.save(update_fields=["status", "aceita_em", "aceita_por", "revisao", "last_modified_at"])
            return cls._resultado_aceite(bloqueada)

    @classmethod
    def recusar(
        cls,
        proposta: PropostaComercial,
        *,
        ator: Usuario,
        revisao_esperada: int,
        agora: datetime | None = None,
    ) -> PropostaComercial:
        _validar_revisao(revisao_esperada)
        agora = agora or timezone.now()
        with cls._estado_bloqueado(proposta, usuarios=(ator,)) as (bloqueada, usuarios):
            ator_bloqueado = usuarios[ator.pk]
            cls._validar_proprietario(bloqueada, ator_id=ator_bloqueado.pk)
            cls._validar_revisao(bloqueada, revisao_esperada)
            cls._validar_vigente(bloqueada, agora=agora)
            if bloqueada.status != StatusPropostaComercial.ENVIADA:
                raise ConflitoPropostaComercial("Somente proposta enviada pode ser recusada.")
            bloqueada.status = StatusPropostaComercial.RECUSADA
            bloqueada.recusada_em = agora
            bloqueada.recusada_por = ator_bloqueado
            bloqueada.revisao += 1
            bloqueada.save(update_fields=["status", "recusada_em", "recusada_por", "revisao", "last_modified_at"])
            return bloqueada

    @classmethod
    def cancelar(
        cls,
        proposta: PropostaComercial,
        *,
        ator: Usuario,
        revisao_esperada: int,
        agora: datetime | None = None,
    ) -> PropostaComercial:
        _validar_revisao(revisao_esperada)
        agora = agora or timezone.now()
        with cls._estado_bloqueado(proposta, usuarios=(ator,)) as (bloqueada, usuarios):
            ator_bloqueado = usuarios[ator.pk]
            cls._validar_operador(ator_bloqueado, permissao="assinaturas.change_propostacomercial")
            cls._validar_revisao(bloqueada, revisao_esperada)
            if bloqueada.status not in {
                StatusPropostaComercial.RASCUNHO,
                StatusPropostaComercial.ENVIADA,
                StatusPropostaComercial.ACEITA,
            }:
                raise ConflitoPropostaComercial("A proposta nao pode mais ser cancelada.")
            bloqueada.status = StatusPropostaComercial.CANCELADA
            bloqueada.cancelada_em = agora
            bloqueada.cancelada_por = ator_bloqueado
            bloqueada.revisao += 1
            bloqueada.save(update_fields=["status", "cancelada_em", "cancelada_por", "revisao", "last_modified_at"])
            return bloqueada

    @classmethod
    def expirar(
        cls,
        proposta: PropostaComercial,
        *,
        revisao_esperada: int,
        agora: datetime | None = None,
    ) -> PropostaComercial:
        _validar_revisao(revisao_esperada)
        agora = agora or timezone.now()
        with cls._proposta_bloqueada(proposta) as bloqueada:
            if bloqueada.status == StatusPropostaComercial.EXPIRADA:
                if revisao_esperada not in (bloqueada.revisao, bloqueada.revisao - 1):
                    raise ConflitoPropostaComercial("A revisão da proposta mudou.")
                return bloqueada
            cls._validar_revisao(bloqueada, revisao_esperada)
            if bloqueada.status not in {StatusPropostaComercial.ENVIADA, StatusPropostaComercial.ACEITA}:
                raise ConflitoPropostaComercial("Somente proposta enviada ou aceita pode expirar.")
            if bloqueada.valida_ate > agora:
                raise ConflitoPropostaComercial("A proposta ainda esta vigente.")
            bloqueada.status = StatusPropostaComercial.EXPIRADA
            bloqueada.expirada_em = agora
            bloqueada.revisao += 1
            bloqueada.save(update_fields=["status", "expirada_em", "revisao", "last_modified_at"])
            return bloqueada

    @classmethod
    def ativar_contratual(
        cls,
        proposta: PropostaComercial,
        *,
        operador: Usuario,
        revisao_esperada: int,
        codigo_mfa: str,
        justificativa: str,
        agora: datetime | None = None,
    ) -> AssinaturaOrganizacao:
        _validar_revisao(revisao_esperada)
        agora = agora or timezone.now()
        justificativa = justificativa.strip()
        with cls._estado_bloqueado(proposta, usuarios=(operador,)) as (bloqueada, usuarios):
            operador_bloqueado = usuarios[operador.pk]
            cls._validar_operador(
                operador_bloqueado,
                permissao="assinaturas.activate_contractual_propostacomercial",
            )
            if not justificativa:
                raise ConflitoPropostaComercial("A justificativa da ativacao contratual e obrigatoria.")
            cls._validar_mfa(operador_bloqueado, codigo_mfa=codigo_mfa, agora=agora)
            return cls._ativar(
                bloqueada,
                revisao_esperada=revisao_esperada,
                modo=ModoAtivacaoProposta.CONTRATUAL,
                status_financeiro=StatusFinanceiro.ISENTO,
                agora=agora,
                operador=operador_bloqueado,
                justificativa=justificativa,
            )

    @classmethod
    def ativar_pagamento_confirmado(
        cls,
        proposta: PropostaComercial,
        *,
        revisao_esperada: int,
        agora: datetime | None = None,
    ) -> AssinaturaOrganizacao:
        """Seam local consumido futuramente pela confirmacao nominal do checkout."""
        _validar_revisao(revisao_esperada)
        agora = agora or timezone.now()
        with cls._proposta_bloqueada(proposta) as bloqueada:
            return cls._ativar(
                bloqueada,
                revisao_esperada=revisao_esperada,
                modo=ModoAtivacaoProposta.PAGAMENTO,
                status_financeiro=StatusFinanceiro.REGULAR,
                agora=agora,
            )

    @classmethod
    def _ativar(
        cls,
        proposta: PropostaComercial,
        *,
        revisao_esperada: int,
        modo: ModoAtivacaoProposta,
        status_financeiro: StatusFinanceiro,
        agora: datetime,
        operador: Usuario | None = None,
        justificativa: str = "",
    ) -> AssinaturaOrganizacao:
        if proposta.status == StatusPropostaComercial.ATIVADA:
            assinatura = AssinaturaOrganizacao.all_objects.filter(proposta_comercial=proposta).first()
            if assinatura is not None and revisao_esperada in (proposta.revisao, proposta.revisao - 1):
                return assinatura
        cls._validar_revisao(proposta, revisao_esperada)
        cls._validar_vigente(proposta, agora=agora)
        if proposta.status != StatusPropostaComercial.ACEITA:
            raise ConflitoPropostaComercial("Somente proposta aceita pode ser ativada.")
        if proposta.modo_ativacao != modo:
            raise ConflitoPropostaComercial("O modo de ativacao da proposta nao corresponde ao fluxo solicitado.")

        assinatura = Assinaturas.criar_enterprise(
            proposta_comercial=proposta,
            status_financeiro=status_financeiro,
            agora=agora,
        )
        proposta.status = StatusPropostaComercial.ATIVADA
        proposta.ativada_em = agora
        proposta.ativada_por = operador
        proposta.justificativa_ativacao = justificativa
        proposta.revisao += 1
        proposta.save(
            update_fields=[
                "status",
                "ativada_em",
                "ativada_por",
                "justificativa_ativacao",
                "revisao",
                "last_modified_at",
            ]
        )
        return assinatura

    @staticmethod
    def _validar_organizacao(organizacao: Organizacao) -> None:
        if organizacao.is_deleted or not organizacao.is_active or organizacao.encerramento_solicitado_em is not None:
            raise ConflitoPropostaComercial("A organizacao nao esta disponivel para proposta.")

    @staticmethod
    def _bloquear_organizacao(organizacao_id: int, *, using: str) -> Organizacao:
        from apps.organizacoes.models import Organizacao

        return Organizacao.all_objects.using(using).select_for_update().get(pk=organizacao_id)

    @classmethod
    @contextmanager
    def _proposta_bloqueada(
        cls,
        proposta: PropostaComercial,
        *,
        usuarios: tuple[Usuario, ...] = (),
    ) -> Iterator[PropostaComercial]:
        with cls._estado_bloqueado(proposta, usuarios=usuarios) as (bloqueada, _):
            yield bloqueada

    @classmethod
    @contextmanager
    def _estado_bloqueado(
        cls,
        proposta: PropostaComercial,
        *,
        usuarios: tuple[Usuario, ...] = (),
    ) -> Iterator[tuple[PropostaComercial, dict[int, Usuario]]]:
        if not isinstance(proposta, PropostaComercial) or proposta.pk is None:
            raise ValueError("Transicao exige uma proposta persistida.")
        using = proposta._state.db or "default"
        from apps.organizacoes.context import organizacao_atual_privilegiada

        with transaction.atomic(using=using):
            usuarios_bloqueados = lock_user_accounts(usuarios, using=using)
            organizacao = cls._bloquear_organizacao(proposta.organizacao_id, using=using)
            cls._validar_organizacao(organizacao)
            with organizacao_atual_privilegiada(organizacao.pk):
                AssinaturaOrganizacao.all_objects.using(using).select_for_update().filter(
                    organizacao=organizacao,
                    status__in=STATUS_CORRENTES,
                ).first()
                yield PropostaComercial.all_objects.using(using).select_for_update().get(pk=proposta.pk), usuarios_bloqueados

    @staticmethod
    def _validar_revisao(proposta: PropostaComercial, revisao_esperada: int) -> None:
        if proposta.revisao != revisao_esperada:
            raise ConflitoPropostaComercial("A revisão da proposta mudou.")

    @staticmethod
    def _validar_vigente(proposta: PropostaComercial, *, agora: datetime) -> None:
        if proposta.valida_ate <= agora:
            raise ConflitoPropostaComercial("A validade da proposta expirou.")

    @staticmethod
    def _validar_proprietario(proposta: PropostaComercial, *, ator_id: int) -> None:
        from apps.organizacoes.models import Papel, Vinculo

        autorizado = Vinculo.objects.filter(
            organizacao_id=proposta.organizacao_id,
            usuario_id=ator_id,
            papel=Papel.PROPRIETARIO,
            is_active=True,
            is_deleted=False,
        ).exists()
        if not autorizado:
            raise ConflitoPropostaComercial("Somente proprietário pode aceitar a proposta.")

    @staticmethod
    def _validar_operador(operador: Usuario, *, permissao: str) -> None:
        if not operador.is_active or not operador.is_staff or not operador.has_perm(permissao):
            raise ConflitoPropostaComercial("A acao exige operador autorizado.")

    @staticmethod
    def _validar_mfa(operador: Usuario, *, codigo_mfa: str, agora: datetime) -> None:
        if type(codigo_mfa) is not str:
            raise ConflitoPropostaComercial("Codigo MFA invalido.")
        fator = active_factors(operador).select_for_update().filter(type=MFAFactorType.TOTP).first()
        if fator is None or not consume_totp(fator, codigo_mfa, now=agora):
            raise ConflitoPropostaComercial("Codigo MFA invalido ou expirado.")

    @classmethod
    def _resultado_aceite(cls, proposta: PropostaComercial) -> ResultadoAceiteProposta:
        preparacao = None
        if proposta.modo_ativacao == ModoAtivacaoProposta.PAGAMENTO:
            termos = cls.termos(proposta)
            preparacao = PreparacaoCheckoutProposta(
                proposta_id=proposta.pk,
                organizacao_id=proposta.organizacao_id,
                revisao=proposta.revisao,
                moeda=proposta.moeda,
                total_centavos=Assinaturas.calcular_preco(termos).total_centavos,
            )
        return ResultadoAceiteProposta(proposta=proposta, preparacao_checkout=preparacao)

    @staticmethod
    def termos(proposta: PropostaComercial) -> TermosAssinatura:
        return TermosAssinatura(
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

    @staticmethod
    def _dados_termos(termos: TermosAssinatura) -> dict[str, object]:
        return {
            "periodicidade": termos.periodicidade,
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

    @classmethod
    def _aplicar_termos(cls, proposta: PropostaComercial, termos: TermosAssinatura) -> None:
        for campo, valor in cls._dados_termos(termos).items():
            setattr(proposta, campo, valor)

    @staticmethod
    def _campos_termos() -> tuple[str, ...]:
        return (
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
