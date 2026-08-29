"""Casos de uso e fatos de domínio para vínculos e convites."""

from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Count, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

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
            convite_bloqueado = Convite.all_objects.select_for_update().get(pk=convite.pk)
            if not convite_bloqueado.pendente:
                raise ValidationError(_("Convite expirado ou já utilizado."))

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
