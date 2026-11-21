"""Casos de uso de vínculos e convites."""

from django.core.exceptions import ValidationError
from django.db import transaction
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


class Vinculos:
    """Mutações de vínculo e convite."""

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
            dados = {
                "organizacao": organizacao,
                "email": email,
                "papel": papel,
                "convidado_por": convidado_por_bloqueado,
            }
            if expira_em is not None:
                dados["expira_em"] = expira_em
            return Convite.objects.using(using).create(**dados)

    @classmethod
    def aceitar_convite(cls, convite: Convite, usuario: Usuario) -> Vinculo:
        """Aceita um convite vivo e cria ou eleva o vínculo do usuário."""
        using = convite._state.db or "default"
        with transaction.atomic(using=using):
            usuario = lock_user_accounts((usuario,), using=using)[usuario.pk]
            if usuario.is_deleted or not usuario.is_active or usuario.exclusao_agendada_para is not None:
                raise APIError(AuthErrorCode.USER_INACTIVE, status_code=401)
            exigir_email_verificado(usuario)
            cls._bloquear_organizacao_aberta(convite.organizacao_id, using=using)
            convite_bloqueado = Convite.all_objects.using(using).select_for_update().get(pk=convite.pk)
            if not convite_bloqueado.pendente:
                raise ValidationError(_("Convite expirado ou já utilizado."))
            if convite_bloqueado.email.casefold() != usuario.email.casefold():
                raise APIError(
                    OrganizationErrorCode.INVITATION_EMAIL_MISMATCH,
                    status_code=422,
                    field="token",
                )

            vinculo, criado = (
                Vinculo.objects.using(using)
                .select_for_update()
                .get_or_create(
                    organizacao_id=convite_bloqueado.organizacao_id,
                    usuario=usuario,
                    defaults={"papel": convite_bloqueado.papel},
                )
            )
            if not criado and vinculo.papel < convite_bloqueado.papel:
                vinculo.papel = convite_bloqueado.papel
                vinculo.save(update_fields=["papel"])

            convite_bloqueado.aceito_em = timezone.now()
            convite_bloqueado.save(update_fields=["aceito_em"])
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
        """Atualiza convite revalidando o ator e o papel concedível sob lock."""
        if {"convidado_por", "convidado_por_id"} & dados.keys():
            raise ValueError("O campo convidado_por é imutável.")
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
            convite_bloqueado = Convite.all_objects.using(using).select_for_update().get(pk=convite.pk, organizacao=organizacao)
            for campo, valor in novos_dados.items():
                setattr(convite_bloqueado, campo, valor)
            convite_bloqueado.save(using=using)
            return convite_bloqueado

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
        using = vinculo._state.db or "default"
        with transaction.atomic(using=using):
            usuarios_bloqueados = lock_user_accounts((ator,), using=using)
            ator_bloqueado = usuarios_bloqueados.get(ator.pk) if ator is not None else None
            organizacao = cls._bloquear_organizacao_aberta(vinculo.organizacao_id, using=using)
            # Membership mutations follow the same global order as Workspace
            # services: lock every distinct affected membership by PK before
            # validating the actor or touching workspace rows.
            vinculo_ids = {vinculo.pk}
            if ator_bloqueado is not None:
                ator_vinculo_id = (
                    Vinculo.all_objects.using(using)
                    .filter(organizacao_id=organizacao.pk, usuario_id=ator_bloqueado.pk)
                    .values_list("pk", flat=True)
                    .first()
                )
                if ator_vinculo_id is not None:
                    vinculo_ids.add(ator_vinculo_id)
            vinculos_bloqueados = {
                bloqueado.pk: bloqueado
                for bloqueado in Vinculo.all_objects.using(using)
                .select_for_update()
                .filter(organizacao_id=organizacao.pk, pk__in=vinculo_ids)
                .order_by("pk")
            }
            vinculo_bloqueado = vinculos_bloqueados.get(vinculo.pk)
            if vinculo_bloqueado is None:
                raise Vinculo.DoesNotExist
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
            times = novos_dados.pop("times", None)
            papel_pretendido = novos_dados.get("papel", vinculo_bloqueado.papel)
            cls._proteger_ultimo_proprietario(
                vinculo_bloqueado,
                papel_pretendido=papel_pretendido,
                is_active_pretendido=novos_dados.get("is_active", vinculo_bloqueado.is_active),
                is_deleted_pretendido=novos_dados.get("is_deleted", vinculo_bloqueado.is_deleted),
                using=using,
            )

            for campo, valor in novos_dados.items():
                setattr(vinculo_bloqueado, campo, valor)
            if novos_dados:
                vinculo_bloqueado.save()
            if times is not None:
                vinculo_bloqueado.times.set(times)
            if papel_pretendido >= Papel.ADMINISTRADOR and vinculo_bloqueado.is_active and not vinculo_bloqueado.is_deleted:
                from apps.workspaces.accesses import AcessosWorkspace

                AcessosWorkspace.garantir_obrigatorios(vinculo_bloqueado, using=using)
            return vinculo_bloqueado

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
