"""Serviços transacionais de emissão de credenciais (`AuthToken`).

Views não devem chamar `AuthToken.objects.create()`/`TokenMetaData.objects.create()`
diretamente: `issue_token()` é o único ponto de entrada, garantindo que token e
metadata nascem juntos ou não nascem.

Toda escrita de credencial passa antes pelo lock da conta (`lock_user_account`).
A ordem global de locks é **`Usuario` → `AuthToken` → `TrustedDevice`**: é ela
que impede uma credencial de nascer válida no intervalo entre a checagem e o
commit de `Usuario.delete()`, e é ela que mantém exclusão, emissão e rotação
livres de inversão.
"""

from dataclasses import dataclass

from django.db import DEFAULT_DB_ALIAS, transaction
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


def resolve_database_alias(instance, using=None) -> str:
    """Alias efetivo de uma escrita: o explícito, o do objeto ou o padrão."""
    if using is not None:
        return using
    state = getattr(instance, "_state", None)
    return getattr(state, "db", None) or DEFAULT_DB_ALIAS


def lock_user_account(user, *, using=None):
    """Bloqueia a linha da conta e devolve o estado recém-lido dela.

    Primeiro elo da ordem global de locks. Aceita a instância ou só o id, e usa
    `all_objects` de propósito: uma conta já excluída precisa ser lida (e não
    sumir) para que quem emite credencial saiba que deve desistir.
    """
    from apps.usuarios.models import Usuario

    user_id = getattr(user, "pk", user)
    database_alias = resolve_database_alias(user, using)
    return Usuario.all_objects.using(database_alias).select_for_update().get(pk=user_id)


def lock_responsible(responsavel, database_alias: str):
    """Bloqueia a conta alvo, recusando somente uma conta já excluída.

    Fluxos administrativos, como reset de MFA, ainda podem atuar sobre contas
    inativas. A emissão de novas credenciais usa ``lock_eligible_responsible``.
    """
    locked = lock_user_account(responsavel, using=database_alias)
    if locked.is_deleted:
        raise APIError(AuthErrorCode.RESPONSIBLE_INACTIVE, status_code=409)
    return locked


def lock_eligible_responsible(responsavel, database_alias: str):
    """Bloqueia uma conta apta a receber novas credenciais."""
    locked = lock_responsible(responsavel, database_alias)
    if not locked.is_active:
        raise APIError(AuthErrorCode.RESPONSIBLE_INACTIVE, status_code=409)
    return locked


def lock_user_accounts(user_ids, *, using):
    """Bloqueia várias contas em ordem de PK e devolve-as indexadas por id."""
    from apps.usuarios.models import Usuario

    ids = sorted({getattr(user_id, "pk", user_id) for user_id in user_ids if user_id is not None})
    contas = list(Usuario.all_objects.using(using).select_for_update().filter(pk__in=ids).order_by("pk"))
    if len(contas) != len(ids):
        raise Usuario.DoesNotExist
    return {conta.pk: conta for conta in contas}


def revoke_all_user_credentials(user, *, actor=None, using=None) -> int:
    """Revoga todas as credenciais ainda utilizáveis de `user`, sem apagar nada.

    Alcança os quatro tipos (sessão, API key, pré-autenticação e reset): a conta
    deixou de existir para a API, então nenhum deles pode sobreviver. Os
    registros permanecem para auditoria, apenas marcados como revogados.

    `actor` é opcional porque `revoked_by` aceita nulo: a revogação disparada
    pela exclusão da própria conta não tem um autor humano a registrar.

    Args:
        user: Dono das credenciais.
        actor: Quem revogou, ou `None` quando a revogação é automática.
        using: Alias do banco; por padrão, o alias do próprio `user`.

    Returns:
        Quantas credenciais estavam utilizáveis e passaram a revogadas.
    """
    database_alias = resolve_database_alias(user, using)
    auth_token_model = get_token_model()

    with transaction.atomic(using=database_alias):
        # Locks tomados em ordem de PK: duas revogações concorrentes de contas
        # com credenciais em comum nunca se cruzam pela metade.
        token_ids = list(
            auth_token_model.objects.using(database_alias)
            .filter(responsavel=user, revoked_at__isnull=True)
            .order_by("pk")
            .select_for_update()
            .values_list("pk", flat=True)
        )
        if not token_ids:
            return 0

        return auth_token_model.objects.using(database_alias).filter(pk__in=token_ids).update(revoked_at=timezone.now(), revoked_by=actor)


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
    using=None,
):
    """Cria o `AuthToken` e seu `TokenMetaData` na mesma transação.

    Se a criação do metadata falhar, o token também não persiste.

    Raises:
        APIError: Se a conta do responsável já tiver sido excluída.
    """
    auth_token_model = get_token_model()

    database_alias = resolve_database_alias(responsavel, using)
    with transaction.atomic(using=database_alias):
        # Segura a conta antes de escrever a credencial: se uma exclusão estiver
        # em curso, esperamos por ela e desistimos em vez de emitir um token que
        # já nasceria órfão.
        conta = lock_user_account(responsavel, using=database_alias)
        if conta.is_deleted or not conta.is_active:
            raise APIError(AuthErrorCode.RESPONSIBLE_INACTIVE, status_code=409)

        token_manager = auth_token_model.objects if database_alias == DEFAULT_DB_ALIAS else auth_token_model.objects.db_manager(database_alias)
        instance, plain_token = token_manager.create(
            responsavel=conta,
            type=token_type,
            created_by=created_by,
            expiry=expiry,
            organization=organization,
            name=name,
            scopes=list(scopes),
        )
        metadata_manager = TokenMetaData.objects if database_alias == DEFAULT_DB_ALIAS else TokenMetaData.objects.db_manager(database_alias)
        metadata_manager.create(token=instance, **metadata_input)

    return IssuedToken(instance=instance, plain_token=plain_token)


def revoke_session(token, *, actor):
    """Revoga logicamente uma sessão. Nunca apaga: o registro fica para auditoria."""
    token.revoked_at = timezone.now()
    token.revoked_by = actor
    token.save(update_fields=["revoked_at", "revoked_by"])
    return token


def revoke_all_sessions(user, *, actor, exclude_uuid=None, using=None):
    """Revoga todas as sessões (nunca API keys/reset) de `user`.

    `exclude_uuid`, quando informado, preserva aquela sessão intacta (ex.:
    logout_all preserva nenhuma; revoke_all_except_current preserva a atual).
    """
    auth_token_model = get_token_model()
    database_alias = resolve_database_alias(user, using)
    queryset = auth_token_model.objects.using(database_alias).filter(responsavel=user, type=TokenType.TOKEN, revoked_at__isnull=True)
    if exclude_uuid is not None:
        queryset = queryset.exclude(uuid=exclude_uuid)

    return queryset.update(revoked_at=timezone.now(), revoked_by=actor)


def create_api_key(*, responsavel, created_by, name, scopes, organization, expiry=None, using=None):
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
        using=using,
    )
    if expiry is not None:
        issued.instance.expiry = expiry
        issued.instance.save(using=resolve_database_alias(issued.instance, using), update_fields=["expiry"])

    emit_api_key_event("create", instance=issued.instance, actor=created_by)
    return issued


def update_api_key(instance, *, actor, name=None, responsavel=None, scopes=None, using=None):
    """Altera nome/responsável/scopes; audita mudança de responsável e de scopes."""
    database_alias = resolve_database_alias(instance, using)
    auth_token_model = get_token_model()
    known_user_ids = {instance.responsavel_id}
    if responsavel is not None:
        known_user_ids.add(responsavel.pk)

    while True:
        retry_user_id = None
        with transaction.atomic(using=database_alias):
            locked_users = lock_user_accounts(known_user_ids, using=database_alias)
            current = auth_token_model.objects.using(database_alias).select_for_update().select_related("responsavel").get(pk=instance.pk)
            if current.responsavel_id not in locked_users:
                retry_user_id = current.responsavel_id
            else:
                next_name = current.name if name is None else name.strip()
                next_responsavel = locked_users[current.responsavel_id] if responsavel is None else locked_users[responsavel.pk]
                next_scopes = current.scopes if scopes is None else list(scopes)
                validate_token_configuration(
                    responsavel=next_responsavel,
                    token_type=current.type,
                    created_by=current.created_by,
                    organization=current.organization,
                    name=next_name,
                    scopes=next_scopes,
                    using=database_alias,
                )

                update_fields = []
                events = []
                if name is not None and next_name != current.name:
                    current.name = next_name
                    update_fields.append("name")
                if responsavel is not None and responsavel.pk != current.responsavel_id:
                    events.append(
                        (
                            "responsible_changed",
                            {"previous_responsavel_id": current.responsavel_id, "new_responsavel_id": responsavel.pk},
                        )
                    )
                    current.responsavel = next_responsavel
                    update_fields.append("responsavel")
                if scopes is not None and next_scopes != current.scopes:
                    events.append(("scopes_changed", {"previous_scopes": current.scopes, "new_scopes": next_scopes}))
                    current.scopes = next_scopes
                    update_fields.append("scopes")
                if update_fields:
                    # Persista pela instância recebida para manter o contrato
                    # observável do serviço, mas apenas com o estado recém-lido
                    # e os campos explicitamente alterados.
                    for field in update_fields:
                        setattr(instance, field, getattr(current, field))
                    if "responsavel" not in update_fields:
                        instance.responsavel = current.responsavel
                    instance.save(using=database_alias, update_fields=update_fields)
                    for event, properties in events:
                        emit_api_key_event(event, instance=instance, actor=actor, **properties)
                return instance

        # O responsável mudou entre a leitura obsoleta e o lock do token.
        # Solte tudo e tente de novo incluindo a conta recém-observada, para
        # nunca adquirir Usuario depois de AuthToken.
        known_user_ids.add(retry_user_id)


def rotate_api_key(current, *, actor, using=None):
    """Rotaciona uma API key: cria uma linha nova e revoga a atual, atomicamente.

    O segredo antigo perde validade imediatamente; a linha revogada permanece
    para auditoria, apontando `replaced_by` para a nova.
    """
    auth_token_model = get_token_model()

    database_alias = resolve_database_alias(current, using)
    known_user_ids = {current.responsavel_id}

    while True:
        retry_user_id = None
        with transaction.atomic(using=database_alias):
            contas = lock_user_accounts(known_user_ids, using=database_alias)
            locked_current = auth_token_model.objects.using(database_alias).select_for_update().select_related("responsavel").get(pk=current.pk)
            if locked_current.responsavel_id not in contas:
                retry_user_id = locked_current.responsavel_id
            else:
                current = locked_current
                conta = contas[current.responsavel_id]

                if current.type != TokenType.API_KEY:
                    raise APIError(AuthErrorCode.INVALID_TOKEN, status_code=409)
                if current.revoked_at is not None:
                    raise APIError(AuthErrorCode.REVOKED_TOKEN, status_code=409)
                if current.suspended_at is not None:
                    raise APIError(AuthErrorCode.API_KEY_SUSPENDED, status_code=409)
                if current.expiry is not None and current.expiry <= timezone.now():
                    raise APIError(AuthErrorCode.EXPIRED_TOKEN, status_code=409)

                from apps.organizacoes.models import Vinculo

                vinculo_ativo = (
                    Vinculo.objects.using(database_alias)
                    .filter(
                        organizacao=current.organization,
                        usuario=conta,
                        is_active=True,
                    )
                    .exists()
                )
                if conta.is_deleted or not conta.is_active or not vinculo_ativo:
                    raise APIError(AuthErrorCode.RESPONSIBLE_INACTIVE, status_code=409)

                validate_token_configuration(
                    responsavel=conta,
                    token_type=current.type,
                    created_by=current.created_by,
                    organization=current.organization,
                    name=current.name,
                    scopes=current.scopes,
                    using=database_alias,
                )

                issued = issue_token(
                    responsavel=conta,
                    token_type=TokenType.API_KEY,
                    created_by=actor,
                    expiry=None,
                    metadata_input={},
                    organization=current.organization,
                    name=current.name,
                    scopes=current.scopes,
                    using=database_alias,
                )
                # `expiry` do manager é relativo; preserve o prazo absoluto.
                issued.instance.expiry = current.expiry
                issued.instance.save(using=database_alias, update_fields=["expiry"])

                current.revoked_at = timezone.now()
                current.revoked_by = actor
                current.replaced_by = issued.instance
                current.save(using=database_alias, update_fields=["revoked_at", "revoked_by", "replaced_by"])
                break

        known_user_ids.add(retry_user_id)

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

    vinculo_ativo = Vinculo.objects.filter(organizacao=instance.organization, usuario=instance.responsavel, is_active=True).exists()

    if not instance.responsavel.is_active or instance.responsavel.is_deleted or not vinculo_ativo:
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

    vinculo_ativo = Vinculo.objects.filter(organizacao=token.organization, usuario=token.responsavel, is_active=True).exists()

    if not token.responsavel.is_active or token.responsavel.is_deleted or not vinculo_ativo:
        token.suspended_at = timezone.now()
        token.suspension_reason = "Responsável inativo ou sem vínculo na organização."
        token.save(update_fields=["suspended_at", "suspension_reason"])
        emit_api_key_event("suspend", instance=token, actor=None, reason=token.suspension_reason, automatic=True)

    return token
