"""Casos de uso transacionais do ciclo de conta."""

import re
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from apps.api.autenticacao.services import lock_user_account, revoke_all_user_credentials
from apps.api.core.errors import APIError

from .emails import (
    ACCOUNT_REACTIVATION_PURPOSE,
    EMAIL_CHANGE_PURPOSE,
    EMAIL_VERIFICATION_PURPOSE,
    carregar_token_email,
    emitir_token_troca_email,
    emitir_token_verificacao,
    normalizar_email,
)
from .errors import AccountErrorCode
from .models import Usuario

_AUDITLOG_PII_KEYS = {
    "actor_email",
    "email",
    "first_name",
    "full_name",
    "identificador",
    "identifier",
    "last_name",
    "name",
    "nome",
    "phone",
    "phone_number",
    "sobrenome",
    "sub",
    "subject",
    "telefone",
    "token",
    "username",
}
_EMAIL_PATTERN = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_PHONE_PATTERN = re.compile(r"(?<!\w)\+?\d(?:[\d ()-]{6,}\d)(?!\w)")
_REDACTED = "[redacted]"


def _pii_key(value) -> bool:
    return str(value).casefold().replace("-", "_") in _AUDITLOG_PII_KEYS


def _collect_string_values(value, output: set[str]) -> None:
    if isinstance(value, dict):
        for nested in value.values():
            _collect_string_values(nested, output)
    elif isinstance(value, (list, tuple, set)):
        for nested in value:
            _collect_string_values(nested, output)
    elif isinstance(value, str) and value.casefold() not in {"", "none", "null"}:
        output.add(value)


def _collect_pii_values(value, output: set[str]) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if _pii_key(key):
                _collect_string_values(nested, output)
            _collect_pii_values(nested, output)
    elif isinstance(value, (list, tuple, set)):
        for nested in value:
            _collect_pii_values(nested, output)


def _redact_text(value: str, pii_values: set[str], *, redact_patterns: bool) -> str:
    redacted = value
    for pii in sorted(pii_values, key=len, reverse=True):
        if len(pii) >= 2:
            escaped = re.escape(pii)
            pattern = rf"(?<!\w){escaped}(?!\w)" if pii.isalnum() else escaped
            redacted = re.sub(pattern, _REDACTED, redacted, flags=re.IGNORECASE)
    if redact_patterns:
        redacted = _EMAIL_PATTERN.sub(_REDACTED, redacted)
        redacted = _PHONE_PATTERN.sub(_REDACTED, redacted)
    return redacted


def _redact_json(value, pii_values: set[str], *, drop_pii_keys: bool, redact_patterns: bool):
    if isinstance(value, dict):
        return {
            key: _redact_json(nested, pii_values, drop_pii_keys=drop_pii_keys, redact_patterns=redact_patterns)
            for key, nested in value.items()
            if not (drop_pii_keys and _pii_key(key))
        }
    if isinstance(value, list):
        return [_redact_json(nested, pii_values, drop_pii_keys=drop_pii_keys, redact_patterns=redact_patterns) for nested in value]
    if isinstance(value, tuple):
        return [_redact_json(nested, pii_values, drop_pii_keys=drop_pii_keys, redact_patterns=redact_patterns) for nested in value]
    if isinstance(value, str):
        return _redact_text(value, pii_values, redact_patterns=redact_patterns)
    return value


def _sanitize_account_auditlog(
    conta,
    *,
    identidades,
    vinculos,
    convites,
    replacement_email: str,
    using: str,
) -> None:
    """Remove PII da trilha ligada à conta sem apagar eventos nem IDs."""
    from apps.api.autenticacao.models import IdentidadeExterna
    from apps.logs.models import LogAlteracao
    from apps.organizacoes.models import Convite, Vinculo

    related_objects = {
        Usuario: {conta.pk},
        IdentidadeExterna: {identidade.pk for identidade in identidades},
        Vinculo: {vinculo.pk for vinculo in vinculos},
        Convite: {convite.pk for convite in convites},
    }
    content_types = ContentType.objects.db_manager(using).get_for_models(*related_objects)
    related_keys = {(content_types[model].pk, str(object_id)) for model, object_ids in related_objects.items() for object_id in object_ids}
    related_query = Q(pk__in=[])
    for content_type_id, object_pk in related_keys:
        related_query |= Q(content_type_id=content_type_id, object_pk=object_pk)

    logs = list(
        LogAlteracao.objects.using(using)
        .select_for_update()
        .filter(related_query | Q(actor_id=conta.pk) | Q(actor_email__iexact=conta.email))
        .order_by("pk")
    )
    pii_values = {
        value
        for value in (
            conta.email,
            replacement_email,
            conta.first_name,
            conta.last_name,
            conta.get_full_name(),
            conta.phone_number,
            *(identidade.identificador for identidade in identidades),
            *(convite.email for convite in convites),
            *(convite.token for convite in convites),
        )
        if isinstance(value, str) and value
    }
    for log in logs:
        if log.actor_email:
            pii_values.add(log.actor_email)
        if (log.content_type_id, log.object_pk) in related_keys:
            for payload in (log.changes, log.serialized_data, log.additional_data):
                _collect_pii_values(payload, pii_values)

    for log in logs:
        related = (log.content_type_id, log.object_pk) in related_keys
        if related:
            log.object_repr = f"{log.content_type.app_label}.{log.content_type.model}#{log.object_pk}"
        else:
            log.object_repr = _redact_text(log.object_repr, pii_values, redact_patterns=False)
        log.changes = _redact_json(log.changes, pii_values, drop_pii_keys=related, redact_patterns=related)
        log.serialized_data = _redact_json(log.serialized_data, pii_values, drop_pii_keys=related, redact_patterns=related)
        log.additional_data = _redact_json(log.additional_data, pii_values, drop_pii_keys=related, redact_patterns=related)
        log.changes_text = _redact_text(log.changes_text, pii_values, redact_patterns=related)
        log.actor_email = None

    if logs:
        LogAlteracao.objects.using(using).bulk_update(
            logs,
            ["object_repr", "changes", "serialized_data", "additional_data", "changes_text", "actor_email"],
        )


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
        identificadores_anteriores = [identidade.identificador for identidade in identidades]
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
        email_substituto = f"deleted-{conta.pk}-{uuid.uuid4()}@invalid.local"
        vinculos = list(Vinculo.all_objects.using(using).select_for_update().filter(usuario=conta).order_by("pk"))
        convites = list(
            Convite.all_objects.using(using).select_for_update().filter(Q(email__iexact=email_anterior) | Q(convidado_por=conta)).order_by("pk")
        )
        for identidade, identificador in zip(identidades, identificadores_anteriores, strict=True):
            identidade.identificador = identificador
        _sanitize_account_auditlog(
            conta,
            identidades=identidades,
            vinculos=vinculos,
            convites=convites,
            replacement_email=email_substituto,
            using=using,
        )
        with disable_auditlog():
            for vinculo in vinculos:
                if not vinculo.is_deleted:
                    vinculo.delete(using=using)

            for convite in convites:
                if convite.email.casefold() == email_anterior.casefold() and convite.aceito_em is None and not convite.is_deleted:
                    convite.delete(using=using)

            conta.email = email_substituto
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
    def solicitar_reativacao(cls, email: str) -> int | None:
        """Seleciona a conta reversivelmente inativa sem expor segredo ao broker."""
        email = Usuario.objects.normalize_email(email)
        cache_key = f"account-reactivation-request:{email.casefold()}"
        conta = Usuario.objects.filter(email__iexact=email, is_active=False).first()
        if (
            conta is None
            or (conta.exclusao_agendada_para is not None and conta.exclusao_agendada_para <= timezone.now())
            or not cache.add(cache_key, True, timeout=60)
        ):
            return None
        return conta.pk

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
