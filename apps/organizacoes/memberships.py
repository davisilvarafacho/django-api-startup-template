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
                cls._validar_capacidade(assinatura, ocupacao, contexto_ocupacao=ocupacao_atual)
            dados = {
                "organizacao": organizacao,
                "email": email,
                "papel": papel,
                "convidado_por": convidado_por,
            }
            if expira_em is not None:
                dados["expira_em"] = expira_em
            return Convite.objects.create(**dados)

    @classmethod
    def aceitar_convite(cls, convite: Convite, usuario: Usuario) -> Vinculo:
        """Aceita um convite vivo e cria ou eleva o vínculo do usuário."""
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
                cls._validar_capacidade(assinatura, ocupacao, contexto_ocupacao=ocupacao_atual)

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

        return vinculo

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
