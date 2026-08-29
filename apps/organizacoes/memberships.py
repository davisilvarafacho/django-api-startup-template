"""Casos de uso e fatos de domínio para vínculos e convites."""

from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Count, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.api.core.errors import APIError
from apps.organizacoes.errors import OrganizationErrorCode
from apps.organizacoes.models import Convite, Organizacao, Papel, Vinculo
from apps.usuarios.models import Usuario


@dataclass(frozen=True)
class OcupacaoSeats:
    """Fatos agregados de seats usados e temporariamente reservados."""

    consumidos: int
    reservados: int


class Vinculos:
    """Mutações de vínculo/convite e cálculo explícito de ocupação."""

    @classmethod
    def criar_proprietario(cls, organizacao: Organizacao, usuario: Usuario) -> Vinculo:
        return Vinculo.objects.create(organizacao=organizacao, usuario=usuario, papel=Papel.PROPRIETARIO)

    @classmethod
    def criar_convite(
        cls,
        *,
        organizacao: Organizacao,
        email: str,
        papel: Papel,
        convidado_por: Usuario | None,
        expira_em=None,
    ) -> Convite:
        erro_capacidade = None
        convite_criado = None
        with transaction.atomic():
            organizacao = cls._bloquear_organizacao_aberta(organizacao.pk)
            assinatura, papeis_isentos = cls._bloquear_contrato_corrente(organizacao)
            if assinatura is not None:
                ocupacao = cls.calcular_ocupacao(organizacao, papeis_isentos)
                ocupacao_atual = ocupacao
                if papel not in papeis_isentos:
                    ocupacao = OcupacaoSeats(
                        consumidos=ocupacao.consumidos,
                        reservados=ocupacao.reservados + 1,
                    )
                try:
                    cls._validar_capacidade(assinatura, ocupacao, contexto_ocupacao=ocupacao_atual)
                except APIError as exc:
                    erro_capacidade = exc
                    if assinatura.expansao_automatica_seats:
                        cls._solicitar_expansao_automatica(organizacao, assinatura, ocupacao)
            if erro_capacidade is None:
                dados = {
                    "organizacao": organizacao,
                    "email": email,
                    "papel": papel,
                    "convidado_por": convidado_por,
                }
                if expira_em is not None:
                    dados["expira_em"] = expira_em
                convite_criado = Convite.objects.create(**dados)
        if erro_capacidade is not None:
            raise erro_capacidade
        assert convite_criado is not None
        return convite_criado

    @classmethod
    def aceitar_convite(cls, convite: Convite, usuario: Usuario) -> Vinculo:
        """Aceita um convite vivo e cria ou eleva o vínculo do usuário."""
        erro_capacidade = None
        vinculo = None
        with transaction.atomic():
            organizacao = cls._bloquear_organizacao_aberta(convite.organizacao_id)
            assinatura, papeis_isentos = cls._bloquear_contrato_corrente(organizacao)
            convite_bloqueado = Convite.all_objects.select_for_update().get(pk=convite.pk)
            if not convite_bloqueado.pendente:
                raise ValidationError(_("Convite expirado ou já utilizado."))

            vinculo_existente = (
                Vinculo.objects.select_for_update()
                .filter(
                    organizacao_id=convite_bloqueado.organizacao_id,
                    usuario=usuario,
                )
                .first()
            )
            if assinatura is not None:
                ocupacao = cls.calcular_ocupacao(organizacao, papeis_isentos)
                ocupacao_atual = ocupacao
                papel_anterior = vinculo_existente.papel if vinculo_existente is not None else None
                papel_pretendido = max(papel_anterior, convite_bloqueado.papel) if papel_anterior is not None else convite_bloqueado.papel
                consumo_anterior = int(papel_anterior is not None and papel_anterior not in papeis_isentos)
                consumo_pretendido = int(papel_pretendido not in papeis_isentos)
                reserva_atual = int(convite_bloqueado.papel not in papeis_isentos)
                ocupacao = OcupacaoSeats(
                    consumidos=ocupacao.consumidos - consumo_anterior + consumo_pretendido,
                    reservados=ocupacao.reservados - reserva_atual,
                )
                try:
                    cls._validar_capacidade(assinatura, ocupacao, contexto_ocupacao=ocupacao_atual)
                except APIError as exc:
                    erro_capacidade = exc
                    if assinatura.expansao_automatica_seats:
                        cls._solicitar_expansao_automatica(organizacao, assinatura, ocupacao)

            if erro_capacidade is None:
                vinculo, criado = Vinculo.objects.get_or_create(
                    organizacao_id=convite_bloqueado.organizacao_id,
                    usuario=usuario,
                    defaults={"papel": convite_bloqueado.papel},
                )
                if not criado and vinculo.papel < convite_bloqueado.papel:
                    vinculo.papel = convite_bloqueado.papel
                    vinculo.save(update_fields=["papel"])

                convite_bloqueado.aceito_em = timezone.now()
                convite_bloqueado.save(update_fields=["aceito_em"])

        if erro_capacidade is not None:
            raise erro_capacidade
        assert vinculo is not None
        return vinculo

    @classmethod
    def atualizar_convite(cls, convite: Convite, *, dados: dict) -> Convite:
        """Atualiza convite sem permitir que papel/expiração burlem a capacidade."""
        erro_capacidade = None
        convite_atualizado = None
        with transaction.atomic():
            organizacao = cls._bloquear_organizacao_aberta(convite.organizacao_id)
            assinatura, papeis_isentos = cls._bloquear_contrato_corrente(organizacao)
            convite_bloqueado = Convite.all_objects.select_for_update().get(pk=convite.pk, organizacao=organizacao)
            novos_dados = dict(dados)
            papel_pretendido = novos_dados.get("papel", convite_bloqueado.papel)
            expira_em_pretendido = novos_dados.get("expira_em", convite_bloqueado.expira_em)

            if assinatura is not None:
                agora = timezone.now()
                ocupacao_atual = cls.calcular_ocupacao(organizacao, papeis_isentos)
                reserva_anterior = cls._convite_reserva_seat(
                    convite_bloqueado,
                    papel=convite_bloqueado.papel,
                    expira_em=convite_bloqueado.expira_em,
                    papeis_isentos=papeis_isentos,
                    agora=agora,
                )
                reserva_pretendida = cls._convite_reserva_seat(
                    convite_bloqueado,
                    papel=papel_pretendido,
                    expira_em=expira_em_pretendido,
                    papeis_isentos=papeis_isentos,
                    agora=agora,
                )
                if reserva_pretendida > reserva_anterior:
                    ocupacao_pretendida = OcupacaoSeats(
                        consumidos=ocupacao_atual.consumidos,
                        reservados=ocupacao_atual.reservados - reserva_anterior + reserva_pretendida,
                    )
                    try:
                        cls._validar_capacidade(assinatura, ocupacao_pretendida, contexto_ocupacao=ocupacao_atual)
                    except APIError as exc:
                        erro_capacidade = exc
                        if assinatura.expansao_automatica_seats:
                            cls._solicitar_expansao_automatica(organizacao, assinatura, ocupacao_pretendida)

            if erro_capacidade is None:
                for campo, valor in novos_dados.items():
                    setattr(convite_bloqueado, campo, valor)
                convite_bloqueado.save()
                convite_atualizado = convite_bloqueado

        if erro_capacidade is not None:
            raise erro_capacidade
        assert convite_atualizado is not None
        return convite_atualizado

    @classmethod
    def atualizar_vinculo(cls, vinculo: Vinculo, *, dados: dict) -> Vinculo:
        """Atualiza vínculo projetando qualquer mudança de papel sob os locks globais."""
        erro_capacidade = None
        vinculo_atualizado = None
        with transaction.atomic():
            organizacao = cls._bloquear_organizacao_aberta(vinculo.organizacao_id)
            assinatura, papeis_isentos = cls._bloquear_contrato_corrente(organizacao)
            vinculo_bloqueado = Vinculo.all_objects.select_for_update().get(pk=vinculo.pk, organizacao=organizacao)
            novos_dados = dict(dados)
            times = novos_dados.pop("times", None)
            papel_pretendido = novos_dados.get("papel", vinculo_bloqueado.papel)

            if assinatura is not None:
                ocupacao_atual = cls.calcular_ocupacao(organizacao, papeis_isentos)
                consumo_anterior = int(not vinculo_bloqueado.is_deleted and vinculo_bloqueado.papel not in papeis_isentos)
                consumo_pretendido = int(not vinculo_bloqueado.is_deleted and papel_pretendido not in papeis_isentos)
                if consumo_pretendido > consumo_anterior:
                    ocupacao_pretendida = OcupacaoSeats(
                        consumidos=ocupacao_atual.consumidos - consumo_anterior + consumo_pretendido,
                        reservados=ocupacao_atual.reservados,
                    )
                    try:
                        cls._validar_capacidade(assinatura, ocupacao_pretendida, contexto_ocupacao=ocupacao_atual)
                    except APIError as exc:
                        erro_capacidade = exc
                        if assinatura.expansao_automatica_seats:
                            cls._solicitar_expansao_automatica(organizacao, assinatura, ocupacao_pretendida)

            if erro_capacidade is None:
                for campo, valor in novos_dados.items():
                    setattr(vinculo_bloqueado, campo, valor)
                if novos_dados:
                    vinculo_bloqueado.save()
                if times is not None:
                    vinculo_bloqueado.times.set(times)
                vinculo_atualizado = vinculo_bloqueado

        if erro_capacidade is not None:
            raise erro_capacidade
        assert vinculo_atualizado is not None
        return vinculo_atualizado

    @staticmethod
    def _bloquear_organizacao_aberta(organizacao_id: int) -> Organizacao:
        organizacao = Organizacao.all_objects.select_for_update().get(pk=organizacao_id)
        if organizacao.encerramento_solicitado_em is not None:
            raise APIError(OrganizationErrorCode.CLOSURE_PENDING, status_code=409)
        return organizacao

    @staticmethod
    def _bloquear_contrato_corrente(organizacao: Organizacao):
        """Mantém a ordem global organização -> assinatura antes da ocupação."""
        from apps.assinaturas.subscriptions import Assinaturas
        from apps.organizacoes.context import organizacao_atual_privilegiada

        with organizacao_atual_privilegiada(organizacao.pk):
            assinatura = Assinaturas.obter_corrente(organizacao, bloquear=True)
        if assinatura is None:
            # Organizações históricas anteriores ao onboarding contratual continuam
            # operáveis; toda organização nova recebe contrato na mesma transação.
            return None, frozenset()
        return assinatura, Assinaturas.papeis_isentos_seat(assinatura)

    @staticmethod
    def _validar_capacidade(assinatura, ocupacao: OcupacaoSeats, *, contexto_ocupacao: OcupacaoSeats) -> None:
        from apps.assinaturas.subscriptions import Assinaturas

        Assinaturas.validar_capacidade(assinatura, ocupacao, contexto_ocupacao=contexto_ocupacao)

    @staticmethod
    def _solicitar_expansao_automatica(
        organizacao: Organizacao,
        assinatura,
        ocupacao: OcupacaoSeats,
    ) -> None:
        from apps.assinaturas.subscriptions import Assinaturas
        from apps.organizacoes.context import organizacao_atual_privilegiada

        with organizacao_atual_privilegiada(organizacao.pk):
            Assinaturas.solicitar_expansao_automatica(
                assinatura,
                seats_necessarios=ocupacao.consumidos + ocupacao.reservados,
            )

    @staticmethod
    def _convite_reserva_seat(
        convite: Convite,
        *,
        papel: int | Papel,
        expira_em,
        papeis_isentos: frozenset[Papel],
        agora,
    ) -> int:
        return int(convite.is_active and not convite.is_deleted and convite.aceito_em is None and expira_em > agora and papel not in papeis_isentos)

    @classmethod
    def calcular_ocupacao(cls, organizacao: Organizacao, papeis_isentos: frozenset[Papel]) -> OcupacaoSeats:
        """Calcula a ocupação em uma agregação SQL explícita e única.

        A consulta parte da organização e usa subqueries escalares para evitar
        multiplicar linhas entre vínculos e convites.
        """
        filtro_consumidos = Q(is_deleted=False)
        if papeis_isentos:
            filtro_consumidos &= ~Q(papel__in=papeis_isentos)

        consumidos = (
            Vinculo.all_objects.filter(organizacao_id=OuterRef("pk"))
            .order_by()
            .values("organizacao_id")
            .annotate(total=Count("pk", filter=filtro_consumidos))
            .values("total")[:1]
        )
        reservados = (
            Convite.all_objects.filter(
                organizacao_id=OuterRef("pk"),
                is_active=True,
                is_deleted=False,
                aceito_em__isnull=True,
                expira_em__gt=timezone.now(),
            )
            .exclude(papel__in=papeis_isentos)
            .order_by()
            .values("organizacao_id")
            .annotate(total=Count("pk"))
            .values("total")[:1]
        )
        fatos = (
            Organizacao.all_objects.filter(pk=organizacao.pk)
            .annotate(
                consumidos=Coalesce(Subquery(consumidos, output_field=models.IntegerField()), Value(0)),
                reservados=Coalesce(Subquery(reservados, output_field=models.IntegerField()), Value(0)),
            )
            .values("consumidos", "reservados")
            .get()
        )
        return OcupacaoSeats(consumidos=fatos["consumidos"], reservados=fatos["reservados"])
