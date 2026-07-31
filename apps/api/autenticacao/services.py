"""Serviços transacionais de emissão de credenciais (`AuthToken`).

Views não devem chamar `AuthToken.objects.create()`/`TokenMetaData.objects.create()`
diretamente: `issue_token()` é o único ponto de entrada, garantindo que token e
metadata nascem juntos ou não nascem.
"""
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from knox.models import get_token_model

from apps.api.core.errors import APIError

from .audit import emit_api_key_event
from .errors import AuthErrorCode
from .models import TokenMetaData, TokenType, validate_token_configuration


@dataclass(frozen=True)
class IssuedToken:
    instance: object
    plain_token: str


def issue_token(
    *,
    responsavel,
    token_type,
    created_by,
    expiry,
    metadata_input,
    organization=None,
    name="",
    scopes=(),
):
    """Cria o `AuthToken` e seu `TokenMetaData` na mesma transação.

    Se a criação do metadata falhar, o token também não persiste.
    """
    auth_token_model = get_token_model()

    with transaction.atomic():
        instance, plain_token = auth_token_model.objects.create(
            responsavel=responsavel,
            type=token_type,
            created_by=created_by,
            expiry=expiry,
            organization=organization,
            name=name,
            scopes=list(scopes),
        )
        TokenMetaData.objects.create(token=instance, **metadata_input)

    return IssuedToken(instance=instance, plain_token=plain_token)


def revoke_session(token, *, actor):
    """Revoga logicamente uma sessão. Nunca apaga: o registro fica para auditoria."""
    token.revoked_at = timezone.now()
    token.revoked_by = actor
    token.save(update_fields=["revoked_at", "revoked_by"])
    return token


def revoke_all_sessions(user, *, actor, exclude_uuid=None):
    """Revoga todas as sessões (nunca API keys/reset) de `user`.

    `exclude_uuid`, quando informado, preserva aquela sessão intacta (ex.:
    logout_all preserva nenhuma; revoke_all_except_current preserva a atual).
    """
    auth_token_model = get_token_model()
    queryset = auth_token_model.objects.filter(
        responsavel=user, type=TokenType.TOKEN, revoked_at__isnull=True
    )
    if exclude_uuid is not None:
        queryset = queryset.exclude(uuid=exclude_uuid)

    return queryset.update(revoked_at=timezone.now(), revoked_by=actor)


def create_api_key(*, responsavel, created_by, name, scopes, organization, expiry=None):
    """Emite uma API key e audita a criação. Único ponto de entrada para o serializer."""
    issued = issue_token(
        responsavel=responsavel,
        token_type=TokenType.API_KEY,
        created_by=created_by,
        # `issue_token`/o manager tratam `expiry` como relativo (`now() + delta`);
        # aqui o cliente manda uma data absoluta opcional, então a aplicamos
        # depois de criado em vez de repassar direto.
        expiry=None,
        metadata_input={},
        organization=organization,
        name=name,
        scopes=list(scopes),
    )
    if expiry is not None:
        issued.instance.expiry = expiry
        issued.instance.save(update_fields=["expiry"])

    emit_api_key_event("create", instance=issued.instance, actor=created_by)
    return issued


def update_api_key(instance, *, actor, name=None, responsavel=None, scopes=None):
    """Altera nome/responsável/scopes; audita mudança de responsável e de scopes."""
    next_name = instance.name if name is None else name.strip()
    next_responsavel = instance.responsavel if responsavel is None else responsavel
    next_scopes = instance.scopes if scopes is None else list(scopes)
    validate_token_configuration(
        responsavel=next_responsavel,
        token_type=instance.type,
        created_by=instance.created_by,
        organization=instance.organization,
        name=next_name,
        scopes=next_scopes,
    )

    update_fields = []
    events = []

    if name is not None and next_name != instance.name:
        instance.name = next_name
        update_fields.append("name")

    if responsavel is not None and responsavel != instance.responsavel:
        events.append(
            (
                "responsible_changed",
                {
                    "previous_responsavel_id": instance.responsavel_id,
                    "new_responsavel_id": responsavel.pk,
                },
            )
        )
        instance.responsavel = responsavel
        update_fields.append("responsavel")

    if scopes is not None and next_scopes != instance.scopes:
        events.append(
            (
                "scopes_changed",
                {
                    "previous_scopes": instance.scopes,
                    "new_scopes": next_scopes,
                },
            )
        )
        instance.scopes = next_scopes
        update_fields.append("scopes")

    if update_fields:
        instance.save(update_fields=update_fields)
        for event, properties in events:
            emit_api_key_event(event, instance=instance, actor=actor, **properties)

    return instance


def rotate_api_key(current, *, actor):
    """Rotaciona uma API key: cria uma linha nova e revoga a atual, atomicamente.

    O segredo antigo perde validade imediatamente; a linha revogada permanece
    para auditoria, apontando `replaced_by` para a nova.
    """
    auth_token_model = get_token_model()

    with transaction.atomic():
        current = auth_token_model.objects.select_for_update().get(pk=current.pk)

        if current.type != TokenType.API_KEY:
            raise APIError(AuthErrorCode.INVALID_TOKEN, status_code=409)
        if current.revoked_at is not None:
            raise APIError(AuthErrorCode.REVOKED_TOKEN, status_code=409)
        if current.suspended_at is not None:
            raise APIError(AuthErrorCode.API_KEY_SUSPENDED, status_code=409)
        if current.expiry is not None and current.expiry <= timezone.now():
            raise APIError(AuthErrorCode.EXPIRED_TOKEN, status_code=409)

        from apps.organizacoes.models import Vinculo

        vinculo_ativo = Vinculo.objects.filter(
            organizacao=current.organization,
            usuario=current.responsavel,
            is_active=True,
        ).exists()
        if not current.responsavel.is_active or not vinculo_ativo:
            raise APIError(AuthErrorCode.RESPONSIBLE_INACTIVE, status_code=409)

        validate_token_configuration(
            responsavel=current.responsavel,
            token_type=current.type,
            created_by=current.created_by,
            organization=current.organization,
            name=current.name,
            scopes=current.scopes,
        )

        issued = issue_token(
            responsavel=current.responsavel,
            token_type=TokenType.API_KEY,
            created_by=actor,
            expiry=None,
            metadata_input={},
            organization=current.organization,
            name=current.name,
            scopes=current.scopes,
        )
        # `expiry` do manager é relativo (soma a `timezone.now()`); aqui
        # preservamos o mesmo prazo absoluto da credencial anterior.
        issued.instance.expiry = current.expiry
        issued.instance.save(update_fields=["expiry"])

        current.revoked_at = timezone.now()
        current.revoked_by = actor
        current.replaced_by = issued.instance
        current.save(update_fields=["revoked_at", "revoked_by", "replaced_by"])

    emit_api_key_event("rotate", instance=issued.instance, actor=actor, replaces_uuid=str(current.uuid))
    return issued


def suspend_api_key(instance, *, actor, reason=""):
    """Suspende (reversível) uma API key. Manual ou automática (ver `ensure_api_key_still_valid`)."""
    instance.suspended_at = timezone.now()
    instance.suspended_by = actor
    instance.suspension_reason = reason
    instance.save(update_fields=["suspended_at", "suspended_by", "suspension_reason"])
    emit_api_key_event("suspend", instance=instance, actor=actor, reason=reason)
    return instance


def resume_api_key(instance, *, actor):
    """Retoma uma API key suspensa; exige responsável ativo e vinculado. Nunca retoma revogada."""
    if instance.revoked_at is not None:
        raise APIError(AuthErrorCode.REVOKED_TOKEN, status_code=409)

    from apps.organizacoes.models import Vinculo

    vinculo_ativo = Vinculo.objects.filter(
        organizacao=instance.organization, usuario=instance.responsavel, is_active=True
    ).exists()

    if not instance.responsavel.is_active or not vinculo_ativo:
        raise APIError(AuthErrorCode.RESPONSIBLE_INACTIVE, status_code=409)

    instance.suspended_at = None
    instance.suspended_by = None
    instance.suspension_reason = ""
    instance.save(update_fields=["suspended_at", "suspended_by", "suspension_reason"])
    emit_api_key_event("resume", instance=instance, actor=actor)
    return instance


def revoke_api_key(instance, *, actor):
    """Revoga permanentemente uma API key. O registro permanece para auditoria."""
    instance.revoked_at = timezone.now()
    instance.revoked_by = actor
    instance.save(update_fields=["revoked_at", "revoked_by"])
    emit_api_key_event("revoke", instance=instance, actor=actor)
    return instance


def ensure_api_key_still_valid(token):
    """Suspende automática e idempotentemente uma API key sem responsável ativo/vinculado.

    Chamado a cada request tenant-scoped (`TenantPermission`, onde o vínculo já
    é resolvido): fail-closed materializado, não só recusado na hora.
    """
    if token.type != TokenType.API_KEY or token.suspended_at is not None or token.revoked_at is not None:
        return token

    from apps.organizacoes.models import Vinculo

    vinculo_ativo = Vinculo.objects.filter(
        organizacao=token.organization, usuario=token.responsavel, is_active=True
    ).exists()

    if not token.responsavel.is_active or not vinculo_ativo:
        token.suspended_at = timezone.now()
        token.suspension_reason = "Responsável inativo ou sem vínculo na organização."
        token.save(update_fields=["suspended_at", "suspension_reason"])
        emit_api_key_event("suspend", instance=token, actor=None, reason=token.suspension_reason, automatic=True)

    return token
