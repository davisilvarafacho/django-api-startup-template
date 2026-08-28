"""Casos de uso transacionais do ciclo de conta."""

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.api.autenticacao.services import lock_user_account, revoke_all_user_credentials
from apps.api.core.errors import APIError

from .emails import (
    EMAIL_CHANGE_PURPOSE,
    EMAIL_VERIFICATION_PURPOSE,
    carregar_token_email,
    emitir_token_troca_email,
    emitir_token_verificacao,
    normalizar_email,
)
from .errors import AccountErrorCode
from .models import Usuario


class Contas:
    """Orquestra mudanças de conta que precisam preservar invariantes."""

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
