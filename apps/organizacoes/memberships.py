"""Casos de uso e fatos de domínio para vínculos e convites."""

from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Count, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.api.autenticacao.errors import AuthErrorCode
from apps.api.autenticacao.services import lock_user_accounts
from apps.api.core.errors import APIError
from apps.organizacoes.errors import OrganizationErrorCode
from apps.organizacoes.models import Convite, Organizacao, Papel, Vinculo
from apps.usuarios.errors import AccountErrorCode
from apps.usuarios.models import Usuario
from apps.usuarios.policies import exigir_email_verificado


@dataclass(frozen=True)
class OcupacaoSeats:
    """Fatos agregados de seats usados e temporariamente reservados."""

    consumidos: int
    reservados: int


class Vinculos:
    """Mutações de vínculo/convite e cálculo explícito de ocupação."""

    @classmethod
    def bloquear_e_exigir_papel(
        cls,
        *,
        organizacao: Organizacao,
        usuario: Usuario,
        papel_minimo: Papel | None,
        using: str = "default",
    ) -> Vinculo:
        """Revalida conta/vínculo e, quando aplicável, o papel sob lock."""
        if not transaction.get_connection(using).in_atomic_block:
            raise RuntimeError("A revalidação de vínculo exige uma seção crítica já aberta.")
        if usuario.is_deleted or not usuario.is_active or usuario.exclusao_agendada_para is not None:
            raise APIError(AuthErrorCode.USER_INACTIVE, status_code=401)
        vinculo = Vinculo.all_objects.using(using).select_for_update().filter(organizacao_id=organizacao.pk, usuario_id=usuario.pk).first()
        if vinculo is None or vinculo.is_deleted:
            raise APIError(OrganizationErrorCode.MEMBERSHIP_REQUIRED, status_code=403)
        if not vinculo.is_active:
            raise APIError(OrganizationErrorCode.MEMBERSHIP_INACTIVE, status_code=403)
        if papel_minimo is not None and vinculo.papel < papel_minimo:
            raise APIError(OrganizationErrorCode.ROLE_INSUFFICIENT, status_code=403)
        return vinculo

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
        ator: Usuario | None = None,
        validar_papel_ator: bool = True,
    ) -> Convite:
        erro_capacidade = None
        convite_criado = None
        using = organizacao._state.db or "default"
        with transaction.atomic(using=using):
            usuarios_bloqueados = lock_user_accounts((ator, convidado_por), using=using)
            ator_bloqueado = usuarios_bloqueados.get(ator.pk) if ator is not None else None
            convidado_por_bloqueado = usuarios_bloqueados.get(convidado_por.pk) if convidado_por is not None else None
            organizacao = cls._bloquear_organizacao_aberta(organizacao.pk, using=using)
            if ator_bloqueado is not None:
                vinculo_ator = cls.bloquear_e_exigir_papel(
                    organizacao=organizacao,
                    usuario=ator_bloqueado,
                    papel_minimo=Papel.GESTOR if validar_papel_ator else None,
                    using=using,
                )
                if validar_papel_ator:
                    cls._exigir_papel_concedivel(papel, vinculo_ator, mensagem="Você não pode convidar alguém para um papel acima do seu.")
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
                    "convidado_por": convidado_por_bloqueado,
                }
                if expira_em is not None:
                    dados["expira_em"] = expira_em
                convite_criado = Convite.objects.using(using).create(**dados)
        if erro_capacidade is not None:
            raise erro_capacidade
        assert convite_criado is not None
        return convite_criado

    @classmethod
    def aceitar_convite(cls, convite: Convite, usuario: Usuario) -> Vinculo:
        """Aceita um convite vivo e cria ou eleva o vínculo do usuário."""
        erro_capacidade = None
        vinculo = None
        using = convite._state.db or "default"
        with transaction.atomic(using=using):
            usuario = lock_user_accounts((usuario,), using=using)[usuario.pk]
            if usuario.is_deleted or not usuario.is_active or usuario.exclusao_agendada_para is not None:
                raise APIError(AuthErrorCode.USER_INACTIVE, status_code=401)
            exigir_email_verificado(usuario)
            organizacao = cls._bloquear_organizacao_aberta(convite.organizacao_id, using=using)
            assinatura, papeis_isentos = cls._bloquear_contrato_corrente(organizacao)
            convite_bloqueado = Convite.all_objects.using(using).select_for_update().get(pk=convite.pk)
            if not convite_bloqueado.pendente:
                raise ValidationError(_("Convite expirado ou já utilizado."))
            if convite_bloqueado.email.casefold() != usuario.email.casefold():
                raise APIError(
                    OrganizationErrorCode.INVITATION_EMAIL_MISMATCH,
                    status_code=422,
                    field="token",
                )

            vinculo_existente = (
                Vinculo.objects.using(using)
                .select_for_update()
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
                vinculo, criado = Vinculo.objects.using(using).get_or_create(
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
    def atualizar_convite(
        cls,
        convite: Convite,
        *,
        dados: dict,
        ator: Usuario | None = None,
        validar_papel_ator: bool = True,
    ) -> Convite:
        """Atualiza convite sem permitir que papel/expiração burlem a capacidade."""
        if {"convidado_por", "convidado_por_id"} & dados.keys():
            raise ValueError("O campo convidado_por é imutável.")
        erro_capacidade = None
        convite_atualizado = None
        using = convite._state.db or "default"
        with transaction.atomic(using=using):
            usuarios_bloqueados = lock_user_accounts((ator,), using=using)
            ator_bloqueado = usuarios_bloqueados.get(ator.pk) if ator is not None else None
            organizacao = cls._bloquear_organizacao_aberta(convite.organizacao_id, using=using)
            vinculo_ator = None
            if ator_bloqueado is not None:
                vinculo_ator = cls.bloquear_e_exigir_papel(
                    organizacao=organizacao,
                    usuario=ator_bloqueado,
                    papel_minimo=Papel.GESTOR if validar_papel_ator else None,
                    using=using,
                )
            novos_dados = dict(dados)
            if validar_papel_ator and vinculo_ator is not None and "papel" in novos_dados:
                cls._exigir_papel_concedivel(
                    novos_dados["papel"],
                    vinculo_ator,
                    mensagem="Você não pode conceder um papel acima do seu.",
                )
            assinatura, papeis_isentos = cls._bloquear_contrato_corrente(organizacao)
            convite_bloqueado = Convite.all_objects.using(using).select_for_update().get(pk=convite.pk, organizacao=organizacao)
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
                convite_bloqueado.save(using=using)
                convite_atualizado = convite_bloqueado

        if erro_capacidade is not None:
            raise erro_capacidade
        assert convite_atualizado is not None
        return convite_atualizado

    @classmethod
    def remover_convite(
        cls,
        convite: Convite,
        *,
        ator: Usuario | None = None,
        validar_papel_ator: bool = True,
    ) -> None:
        """Remove um convite após revalidar o ator sob os mesmos locks da API."""
        using = convite._state.db or "default"
        with transaction.atomic(using=using):
            usuarios_bloqueados = lock_user_accounts((ator,), using=using)
            ator_bloqueado = usuarios_bloqueados.get(ator.pk) if ator is not None else None
            organizacao = cls._bloquear_organizacao_aberta(convite.organizacao_id, using=using)
            if ator_bloqueado is not None:
                cls.bloquear_e_exigir_papel(
                    organizacao=organizacao,
                    usuario=ator_bloqueado,
                    papel_minimo=Papel.GESTOR if validar_papel_ator else None,
                    using=using,
                )
            convite_bloqueado = Convite.all_objects.using(using).select_for_update().get(pk=convite.pk, organizacao=organizacao)
            convite_bloqueado.delete(using=using)

    @classmethod
    def atualizar_vinculo(
        cls,
        vinculo: Vinculo,
        *,
        dados: dict,
        ator: Usuario | None = None,
        validar_papel_ator: bool = True,
    ) -> Vinculo:
        """Atualiza vínculo projetando qualquer mudança de papel sob os locks globais."""
        if {"usuario", "usuario_id"} & dados.keys():
            raise ValueError("O campo usuario é imutável.")
        erro_capacidade = None
        vinculo_atualizado = None
        using = vinculo._state.db or "default"
        with transaction.atomic(using=using):
            usuarios_bloqueados = lock_user_accounts((ator,), using=using)
            ator_bloqueado = usuarios_bloqueados.get(ator.pk) if ator is not None else None
            organizacao = cls._bloquear_organizacao_aberta(vinculo.organizacao_id, using=using)
            vinculo_ator = None
            if ator_bloqueado is not None:
                vinculo_ator = cls.bloquear_e_exigir_papel(
                    organizacao=organizacao,
                    usuario=ator_bloqueado,
                    papel_minimo=Papel.ADMINISTRADOR if validar_papel_ator else None,
                    using=using,
                )
            novos_dados = dict(dados)
            if validar_papel_ator and vinculo_ator is not None and "papel" in novos_dados:
                cls._exigir_papel_concedivel(
                    novos_dados["papel"],
                    vinculo_ator,
                    mensagem="Você não pode conceder um papel acima do seu.",
                )
            assinatura, papeis_isentos = cls._bloquear_contrato_corrente(organizacao)
            vinculo_bloqueado = Vinculo.all_objects.using(using).select_for_update().get(pk=vinculo.pk, organizacao=organizacao)
            times = novos_dados.pop("times", None)
            papel_pretendido = novos_dados.get("papel", vinculo_bloqueado.papel)
            cls._proteger_ultimo_proprietario(
                vinculo_bloqueado,
                papel_pretendido=papel_pretendido,
                is_active_pretendido=novos_dados.get("is_active", vinculo_bloqueado.is_active),
                is_deleted_pretendido=novos_dados.get("is_deleted", vinculo_bloqueado.is_deleted),
                using=using,
            )

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

    @classmethod
    def remover_vinculo(
        cls,
        vinculo: Vinculo,
        *,
        ator: Usuario | None = None,
        validar_papel_ator: bool = True,
    ) -> None:
        """Remove logicamente um vínculo sem abandonar a organização sem dono."""
        using = vinculo._state.db or "default"
        with transaction.atomic(using=using):
            usuarios_bloqueados = lock_user_accounts((ator,), using=using)
            ator_bloqueado = usuarios_bloqueados.get(ator.pk) if ator is not None else None
            organizacao = cls._bloquear_organizacao_aberta(vinculo.organizacao_id, using=using)
            if ator_bloqueado is not None:
                cls.bloquear_e_exigir_papel(
                    organizacao=organizacao,
                    usuario=ator_bloqueado,
                    papel_minimo=Papel.ADMINISTRADOR if validar_papel_ator else None,
                    using=using,
                )
            vinculo_bloqueado = Vinculo.all_objects.using(using).select_for_update().get(pk=vinculo.pk, organizacao=organizacao)
            cls._proteger_ultimo_proprietario(
                vinculo_bloqueado,
                papel_pretendido=vinculo_bloqueado.papel,
                is_active_pretendido=vinculo_bloqueado.is_active,
                is_deleted_pretendido=True,
                using=using,
            )
            vinculo_bloqueado.delete(using=using)

    @staticmethod
    def _exigir_papel_concedivel(papel: int | Papel, vinculo_ator: Vinculo, *, mensagem: str) -> None:
        if papel > vinculo_ator.papel:
            raise APIError(
                OrganizationErrorCode.ROLE_INSUFFICIENT,
                status_code=422,
                field="papel",
                message=mensagem,
            )

    @staticmethod
    def _proteger_ultimo_proprietario(
        vinculo: Vinculo,
        *,
        papel_pretendido: int | Papel,
        is_active_pretendido: bool,
        is_deleted_pretendido: bool,
        using: str,
    ) -> None:
        """Preserva ao menos um proprietário ativo sob o lock da organização."""
        era_proprietario_ativo = vinculo.papel == Papel.PROPRIETARIO and vinculo.is_active and not vinculo.is_deleted
        permanecera_proprietario_ativo = papel_pretendido == Papel.PROPRIETARIO and is_active_pretendido and not is_deleted_pretendido
        if not era_proprietario_ativo or permanecera_proprietario_ativo:
            return

        proprietarios = list(
            Vinculo.all_objects.using(using)
            .select_for_update()
            .filter(
                organizacao_id=vinculo.organizacao_id,
                papel=Papel.PROPRIETARIO,
                is_active=True,
                is_deleted=False,
            )
            .order_by("pk")
        )
        usuarios_ativos = set(
            Usuario.objects.using(using)
            .filter(
                pk__in=[proprietario.usuario_id for proprietario in proprietarios],
                is_active=True,
                is_deleted=False,
            )
            .values_list("pk", flat=True)
        )
        if vinculo.usuario_id in usuarios_ativos and len(usuarios_ativos) <= 1:
            raise APIError(AccountErrorCode.OWNER_TRANSFER_REQUIRED, status_code=409)

    @staticmethod
    def _bloquear_organizacao_aberta(organizacao_id: int, *, using: str = "default") -> Organizacao:
        organizacao = Organizacao.all_objects.using(using).select_for_update().get(pk=organizacao_id)
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
