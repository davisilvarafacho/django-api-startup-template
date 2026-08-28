"""Casos de uso transacionais do ciclo de conta."""

import uuid
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.api.autenticacao.services import lock_user_account, revoke_all_user_credentials
from apps.api.core.errors import APIError

from .emails import (
    ACCOUNT_REACTIVATION_PURPOSE,
    EMAIL_CHANGE_PURPOSE,
    EMAIL_VERIFICATION_PURPOSE,
    carregar_token_email,
    emitir_token_reativacao,
    emitir_token_troca_email,
    emitir_token_verificacao,
    normalizar_email,
)
from .errors import AccountErrorCode
from .models import Usuario


def _proteger_organizacoes_sem_outro_proprietario(conta, *, using: str) -> None:
    """Serializa a saída de proprietários e preserva um responsável ativo."""
    from apps.organizacoes.models import Organizacao, Papel, Vinculo

    organizacao_ids = list(
        Vinculo.objects.using(using)
        .filter(
            usuario=conta,
            papel=Papel.PROPRIETARIO,
            is_active=True,
            organizacao__is_active=True,
            organizacao__is_deleted=False,
        )
        .order_by("organizacao_id")
        .values_list("organizacao_id", flat=True)
    )
    organizacoes = list(Organizacao.objects.using(using).select_for_update().filter(pk__in=organizacao_ids).order_by("pk"))
    for organizacao in organizacoes:
        proprietarios = list(
            Vinculo.objects.using(using).select_for_update().filter(organizacao=organizacao, papel=Papel.PROPRIETARIO, is_active=True).order_by("pk")
        )
        proprietario_ids = [vinculo.usuario_id for vinculo in proprietarios]
        if conta.pk in proprietario_ids and Usuario.objects.using(using).filter(pk__in=proprietario_ids, is_active=True).count() <= 1:
            raise APIError(AccountErrorCode.OWNER_TRANSFER_REQUIRED, status_code=409)


def _desativar_conta_bloqueada(conta, *, using: str) -> None:
    """Aplica as revogações depois de conta e invariantes já estarem bloqueadas."""
    from apps.api.autenticacao.mfa import revoke_trusted_devices
    from apps.organizacoes.models import Vinculo

    if conta.is_active:
        conta.is_active = False
        conta.save(using=using, update_fields=["is_active"])

    vinculos = list(Vinculo.objects.using(using).select_for_update().filter(usuario=conta, is_active=True).order_by("pk"))
    for vinculo in vinculos:
        vinculo.is_active = False
        vinculo.save(using=using, update_fields=["is_active"])

    revoke_all_user_credentials(conta, using=using)
    revoke_trusted_devices(conta, using=using)


def _anonimizar_conta_vencida(conta_id: int, *, now, using: str) -> bool:
    """Anonimiza uma conta vencida sob seu lock canônico."""
    from auditlog.context import disable_auditlog

    from apps.api.autenticacao.mfa import revoke_trusted_devices
    from apps.api.autenticacao.models import IdentidadeExterna, MFAChallenge, MFAFactor, MFARecoveryCode
    from apps.organizacoes.models import Convite, Vinculo

    with transaction.atomic(using=using):
        conta = Usuario.all_objects.using(using).select_for_update().get(pk=conta_id)
        if conta.is_deleted or conta.exclusao_agendada_para is None or conta.exclusao_agendada_para > now:
            return False

        identidades = list(IdentidadeExterna.all_objects.using(using).select_for_update().filter(usuario=conta).order_by("pk"))
        for identidade in identidades:
            identidade.anonimizar(using=using)

        # Preserva a ordem global Usuario → AuthToken → TrustedDevice.
        revoke_all_user_credentials(conta, using=using)
        revoke_trusted_devices(conta, using=using)

        fator_ids = list(MFAFactor.objects.using(using).select_for_update().filter(user=conta).order_by("pk").values_list("pk", flat=True))
        MFAFactor.objects.using(using).filter(pk__in=fator_ids, disabled_at__isnull=True).update(disabled_at=now)
        desafio_ids = list(MFAChallenge.objects.using(using).select_for_update().filter(user=conta).order_by("pk").values_list("pk", flat=True))
        MFAChallenge.objects.using(using).filter(pk__in=desafio_ids, consumed_at__isnull=True).update(consumed_at=now)
        recuperacao_ids = list(
            MFARecoveryCode.objects.using(using).select_for_update().filter(user=conta).order_by("pk").values_list("pk", flat=True)
        )
        MFARecoveryCode.objects.using(using).filter(pk__in=recuperacao_ids, consumed_at__isnull=True).update(consumed_at=now)

        email_anterior = conta.email
        with disable_auditlog():
            vinculos = list(Vinculo.objects.using(using).select_for_update().filter(usuario=conta).order_by("pk"))
            for vinculo in vinculos:
                vinculo.delete(using=using)

            convites = list(
                Convite.objects.using(using).select_for_update().filter(email__iexact=email_anterior, aceito_em__isnull=True).order_by("pk")
            )
            for convite in convites:
                convite.delete(using=using)

            conta.email = f"deleted-{conta.pk}-{uuid.uuid4()}@invalid.local"
            conta.first_name = ""
            conta.last_name = ""
            conta.phone_number = None
            conta.phone_verified_at = None
            conta.email_verificado_em = None
            conta.is_active = False
            conta.is_deleted = True
            conta.set_unusable_password()
            conta.save(
                using=using,
                update_fields=[
                    "email",
                    "first_name",
                    "last_name",
                    "phone_number",
                    "phone_verified_at",
                    "email_verificado_em",
                    "password",
                    "is_active",
                    "is_deleted",
                ],
            )

    return True


def anonimizar_contas_vencidas(*, now=None, batch_size=None, using="default") -> int:
    """Processa no máximo um lote de contas vencidas, de forma idempotente."""
    now = now or timezone.now()
    if batch_size is None:
        batch_size = settings.ACCOUNT_DELETION_BATCH_SIZE
    if batch_size < 1:
        raise ValueError("batch_size precisa ser positivo.")

    conta_ids = list(Usuario.objects.using(using).filter(exclusao_agendada_para__lte=now).order_by("pk").values_list("pk", flat=True)[:batch_size])
    return sum(_anonimizar_conta_vencida(conta_id, now=now, using=using) for conta_id in conta_ids)


class Contas:
    """Orquestra mudanças de conta que precisam preservar invariantes."""

    @classmethod
    def desativar(cls, usuario):
        """Desativa a conta e suspende seu acesso sem apagar vínculos."""
        database_alias = usuario._state.db or "default"
        with transaction.atomic(using=database_alias):
            conta = lock_user_account(usuario, using=database_alias)
            _proteger_organizacoes_sem_outro_proprietario(conta, using=database_alias)
            _desativar_conta_bloqueada(conta, using=database_alias)

        return conta

    @classmethod
    def agendar_exclusao(cls, usuario):
        """Agenda a anonimização definitiva e desativa a conta imediatamente."""
        database_alias = usuario._state.db or "default"
        with transaction.atomic(using=database_alias):
            conta = lock_user_account(usuario, using=database_alias)
            if conta.exclusao_agendada_para is not None:
                raise APIError(
                    AccountErrorCode.DELETION_ALREADY_SCHEDULED,
                    status_code=409,
                    context={"scheduled_for": conta.exclusao_agendada_para.isoformat()},
                )
            _proteger_organizacoes_sem_outro_proprietario(conta, using=database_alias)
            agora = timezone.now()
            conta.exclusao_solicitada_em = agora
            conta.exclusao_agendada_para = agora + timedelta(days=settings.ACCOUNT_DELETION_GRACE_DAYS)
            conta.save(
                using=database_alias,
                update_fields=["exclusao_solicitada_em", "exclusao_agendada_para"],
            )
            _desativar_conta_bloqueada(conta, using=database_alias)

        return conta

    @classmethod
    def solicitar_reativacao(cls, email: str) -> str | None:
        """Emite token somente para conta reversivelmente inativa, sem enumerá-la."""
        email = Usuario.objects.normalize_email(email)
        cache_key = f"account-reactivation-request:{email.casefold()}"
        conta = Usuario.objects.filter(email__iexact=email, is_active=False).first()
        if (
            conta is None
            or (conta.exclusao_agendada_para is not None and conta.exclusao_agendada_para <= timezone.now())
            or not cache.add(cache_key, True, timeout=60)
        ):
            return None
        return emitir_token_reativacao(conta)

    @classmethod
    def confirmar_reativacao(cls, token: str):
        """Reativa a conta dentro do prazo sem ressuscitar acessos anteriores."""
        signed_token = carregar_token_email(token, purpose=ACCOUNT_REACTIVATION_PURPOSE)
        if signed_token is None:
            raise APIError(AccountErrorCode.REACTIVATION_INVALID, status_code=400)

        try:
            with transaction.atomic():
                conta = lock_user_account(signed_token.usuario_id)
                prazo_expirado = conta.exclusao_agendada_para is not None and conta.exclusao_agendada_para <= timezone.now()
                if conta.is_deleted or conta.is_active or prazo_expirado or normalizar_email(conta.email) != signed_token.email:
                    raise APIError(AccountErrorCode.REACTIVATION_INVALID, status_code=400)
                conta.is_active = True
                conta.exclusao_solicitada_em = None
                conta.exclusao_agendada_para = None
                conta.save(update_fields=["is_active", "exclusao_solicitada_em", "exclusao_agendada_para"])
        except Usuario.DoesNotExist as exc:
            raise APIError(AccountErrorCode.REACTIVATION_INVALID, status_code=400) from exc

        return conta

    @classmethod
    def verificar_email(cls, token: str):
        signed_token = carregar_token_email(token, purpose=EMAIL_VERIFICATION_PURPOSE)
        if signed_token is None:
            raise APIError(AccountErrorCode.EMAIL_VERIFICATION_INVALID, status_code=400)

        try:
            with transaction.atomic():
                usuario = lock_user_account(signed_token.usuario_id)
                if usuario.is_deleted or not usuario.is_active or normalizar_email(usuario.email) != signed_token.email:
                    raise APIError(AccountErrorCode.EMAIL_VERIFICATION_INVALID, status_code=400)
                if usuario.email_verificado_em is not None:
                    raise APIError(AccountErrorCode.EMAIL_ALREADY_VERIFIED, status_code=409)
                usuario.email_verificado_em = timezone.now()
                usuario.save(update_fields=["email_verificado_em"])
        except Usuario.DoesNotExist as exc:
            raise APIError(AccountErrorCode.EMAIL_VERIFICATION_INVALID, status_code=400) from exc

        return usuario

    @classmethod
    def solicitar_verificacao_email(cls, email: str) -> str | None:
        """Emite um token para uma conta pendente sem tornar o e-mail um oráculo."""
        email = Usuario.objects.normalize_email(email)
        cache_key = f"email-verification-resend:{email.casefold()}"
        usuario = Usuario.objects.filter(email__iexact=email, is_active=True, email_verificado_em__isnull=True).first()
        if usuario is None or not cache.add(cache_key, True, timeout=60):
            return None
        return emitir_token_verificacao(usuario)

    @classmethod
    def solicitar_troca_email(cls, usuario, email: str) -> str:
        email = Usuario.objects.normalize_email(email)
        with transaction.atomic():
            conta = lock_user_account(usuario)
            if Usuario.objects.filter(email__iexact=email).exclude(pk=conta.pk).exists() or conta.email.casefold() == email.casefold():
                raise APIError(AccountErrorCode.EMAIL_ALREADY_IN_USE, status_code=409)
            return emitir_token_troca_email(conta, email)

    @classmethod
    def confirmar_troca_email(cls, token: str):
        signed_token = carregar_token_email(token, purpose=EMAIL_CHANGE_PURPOSE)
        if signed_token is None:
            raise APIError(AccountErrorCode.EMAIL_VERIFICATION_INVALID, status_code=400)

        try:
            with transaction.atomic():
                usuario = lock_user_account(signed_token.usuario_id)
                if usuario.is_deleted or not usuario.is_active:
                    raise APIError(AccountErrorCode.EMAIL_VERIFICATION_INVALID, status_code=400)
                if Usuario.objects.filter(email__iexact=signed_token.email).exclude(pk=usuario.pk).exists():
                    raise APIError(AccountErrorCode.EMAIL_ALREADY_IN_USE, status_code=409)
                previous_email = usuario.email
                try:
                    with transaction.atomic():
                        usuario.confirmar_email_assinado(signed_token.email, verified_at=timezone.now())
                except IntegrityError as exc:
                    raise APIError(AccountErrorCode.EMAIL_ALREADY_IN_USE, status_code=409) from exc
                revoke_all_user_credentials(usuario, using=usuario._state.db)
        except Usuario.DoesNotExist as exc:
            raise APIError(AccountErrorCode.EMAIL_VERIFICATION_INVALID, status_code=400) from exc

        return usuario, previous_email
