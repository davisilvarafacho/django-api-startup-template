"""Casos de uso concretos da identidade Google."""

from typing import NoReturn, cast

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.utils import timezone

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token as google_id_token

from apps.api.core.errors import APIError
from apps.usuarios.errors import AccountErrorCode
from apps.usuarios.models import Usuario

from .errors import AuthErrorCode
from .models import IdentidadeExterna, ProvedorIdentidade
from .services import lock_user_account


class AutenticacaoGoogle:
    """Valida ID tokens e preserva os vínculos locais com o Google."""

    @classmethod
    def autenticar(cls, token: str) -> Usuario:
        """Resolve uma conta existente ou cadastra uma nova pelo ``sub``."""
        claims = cls._verificar_token(token)
        sub = cls._obter_sub(claims)
        identidade = cls._buscar_identidade(sub)
        if identidade is not None:
            cls._exigir_conta_disponivel(identidade.usuario)
            return identidade.usuario

        email = cls._obter_email_de_cadastro(claims)
        try:
            with transaction.atomic():
                identidade = cls._buscar_identidade(sub, bloquear=True)
                if identidade is not None:
                    cls._exigir_conta_disponivel(identidade.usuario)
                    return identidade.usuario

                if Usuario.all_objects.filter(email__iexact=email).exists():
                    raise APIError(AccountErrorCode.EXTERNAL_IDENTITY_CONFLICT, status_code=409)

                usuario = cls._criar_usuario(claims, email)
                IdentidadeExterna.objects.create(
                    usuario=usuario,
                    provedor=ProvedorIdentidade.GOOGLE,
                    identificador=sub,
                )
                return usuario
        except IntegrityError as exc:
            # Duas requisições válidas para o mesmo ``sub`` podem observar a
            # ausência juntas. A constraint decide a vencedora; a outra passa
            # a usar o vínculo já confirmado depois do rollback local.
            identidade = cls._buscar_identidade(sub)
            if identidade is None:
                raise APIError(AccountErrorCode.EXTERNAL_IDENTITY_CONFLICT, status_code=409) from exc
            cls._exigir_conta_disponivel(identidade.usuario)
            return identidade.usuario

    @classmethod
    def vincular(cls, usuario: Usuario, token: str) -> IdentidadeExterna:
        """Vincula explicitamente o ``sub`` à conta local verificada."""
        claims = cls._verificar_token(token)
        sub = cls._obter_sub(claims)
        database_alias = usuario._state.db or "default"

        try:
            with transaction.atomic(using=database_alias):
                conta = lock_user_account(usuario, using=database_alias)
                cls._exigir_conta_disponivel(conta)
                if conta.email_verificado_em is None:
                    raise APIError(AccountErrorCode.EMAIL_NOT_VERIFIED, status_code=403)

                por_sub = (
                    IdentidadeExterna.objects.using(database_alias)
                    .select_for_update()
                    .filter(provedor=ProvedorIdentidade.GOOGLE, identificador=sub)
                    .first()
                )
                if por_sub is not None:
                    if por_sub.usuario_id == conta.pk:
                        return por_sub
                    raise APIError(AccountErrorCode.EXTERNAL_IDENTITY_CONFLICT, status_code=409)

                por_usuario = (
                    IdentidadeExterna.objects.using(database_alias)
                    .select_for_update()
                    .filter(provedor=ProvedorIdentidade.GOOGLE, usuario=conta)
                    .first()
                )
                if por_usuario is not None:
                    raise APIError(AccountErrorCode.EXTERNAL_IDENTITY_CONFLICT, status_code=409)

                return IdentidadeExterna.objects.using(database_alias).create(
                    usuario=conta,
                    provedor=ProvedorIdentidade.GOOGLE,
                    identificador=sub,
                )
        except IntegrityError as exc:
            raise APIError(AccountErrorCode.EXTERNAL_IDENTITY_CONFLICT, status_code=409) from exc

    @classmethod
    def desvincular(cls, usuario: Usuario) -> None:
        """Remove o Google somente quando uma senha local ainda permite login."""
        database_alias = usuario._state.db or "default"
        with transaction.atomic(using=database_alias):
            conta = lock_user_account(usuario, using=database_alias)
            cls._exigir_conta_disponivel(conta)
            identidade = (
                IdentidadeExterna.objects.using(database_alias).select_for_update().filter(usuario=conta, provedor=ProvedorIdentidade.GOOGLE).first()
            )
            if identidade is None:
                return
            if not conta.has_usable_password():
                raise APIError(AccountErrorCode.EXTERNAL_IDENTITY_LAST_LOGIN, status_code=409)
            identidade.delete(using=database_alias)

    @classmethod
    def _verificar_token(cls, token: str) -> dict[str, object]:
        client_ids = settings.GOOGLE_OAUTH_CLIENT_IDS
        requisicao_google = GoogleRequest()
        for client_id in client_ids:
            try:
                return dict(google_id_token.verify_oauth2_token(token, requisicao_google, audience=client_id))
            except (GoogleAuthError, ValueError):
                continue
        return cls._token_invalido()

    @classmethod
    def _obter_sub(cls, claims: dict[str, object]) -> str:
        sub = claims.get("sub")
        max_length = cast(int, IdentidadeExterna._meta.get_field("identificador").max_length)
        if not isinstance(sub, str) or not sub.strip() or len(sub) > max_length:
            cls._token_invalido()
        return sub

    @classmethod
    def _obter_email_de_cadastro(cls, claims: dict[str, object]) -> str:
        email = claims.get("email")
        if claims.get("email_verified") is not True or not isinstance(email, str):
            cls._token_invalido()
        email = Usuario.objects.normalize_email(email.strip())
        try:
            validate_email(email)
        except ValidationError:
            cls._token_invalido()
        if len(email) > cast(int, Usuario._meta.get_field("email").max_length):
            cls._token_invalido()
        return email

    @classmethod
    def _criar_usuario(cls, claims: dict[str, object], email: str) -> Usuario:
        given_name = claims.get("given_name")
        family_name = claims.get("family_name")
        first_name = given_name if isinstance(given_name, str) else ""
        last_name = family_name if isinstance(family_name, str) else ""
        usuario = Usuario.objects.create_user(
            email=email,
            password=None,
            first_name=first_name[: cast(int, Usuario._meta.get_field("first_name").max_length)],
            last_name=last_name[: cast(int, Usuario._meta.get_field("last_name").max_length)],
        )
        dominio = email.rsplit("@", 1)[-1].casefold()
        if dominio == "gmail.com" or bool(claims.get("hd")):
            usuario.email_verificado_em = timezone.now()
            usuario.save(update_fields=["email_verificado_em"])
        return usuario

    @staticmethod
    def _buscar_identidade(sub: str, *, bloquear=False) -> IdentidadeExterna | None:
        queryset = IdentidadeExterna.objects.select_related("usuario").filter(
            provedor=ProvedorIdentidade.GOOGLE,
            identificador=sub,
        )
        if bloquear:
            queryset = queryset.select_for_update()
        return queryset.first()

    @staticmethod
    def _exigir_conta_disponivel(usuario: Usuario) -> None:
        if not usuario.is_active or usuario.is_deleted or usuario.exclusao_agendada_para is not None:
            raise APIError(AuthErrorCode.USER_INACTIVE, status_code=401)

    @staticmethod
    def _token_invalido() -> NoReturn:
        raise APIError(AuthErrorCode.GOOGLE_TOKEN_INVALID, status_code=401)
