"""Emissão e consumo do token de redefinição de senha, e as revogações que a acompanham.

Trocar a senha é o ponto de recuperação de uma conta comprometida: se o atacante
mantiver qualquer credencial viva depois da troca, a recuperação não recupera
nada. Por isso `revoke_credentials_after_password_change()` derruba **tudo** —
sessões, tokens efêmeros, resets pendentes, dispositivos confiáveis e desafios
MFA em aberto — inclusive a credencial de quem está fazendo a troca.

API keys são a exceção: pertencem à integração, não à sessão humana, e derrubá-las
numa troca de senha de rotina quebraria produção sem ganho de segurança.
"""

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from knox import crypto
from knox.models import get_token_model

from apps.usuarios.passwords import set_validated_password

from .models import MFAChallenge, TokenType, TrustedDevice
from .services import issue_token, lock_user_account, resolve_database_alias

# Tipos que representam "esta pessoa, neste dispositivo". Não inclui API_KEY.
SESSION_TOKEN_TYPES = (TokenType.TOKEN, TokenType.PRE_AUTH, TokenType.RESET_PASSWORD)


def password_reset_lifetime() -> timedelta:
    return timedelta(minutes=settings.PASSWORD_RESET_TIMEOUT_MINUTES)


def _reset_token_digest(plain_token):
    """Digest do token puro, ou `None` quando ele nem tem formato hasheável."""
    try:
        return crypto.hash_token(plain_token)
    except (TypeError, ValueError):
        # Token com formato impossível de hashear: mesmo desfecho de token errado.
        return None


def issue_password_reset(user, *, using=None):
    """Emite um token de redefinição, invalidando os anteriores do mesmo usuário.

    Só um reset fica válido por vez: dois links ativos dobram a janela em que um
    e-mail interceptado ainda serve.

    A emissão segura o lock da conta antes de escrever (a mesma linha que
    `Usuario.delete()` bloqueia), de modo que um reset jamais nasce válido para
    uma conta que acabou de ser excluída.

    Args:
        user: Dono da conta.
        using: Alias do banco da operação.

    Returns:
        `IssuedToken` — o token puro só existe no retorno, nunca é persistido —
        ou `None` se a conta já estiver excluída ou inativa.
    """
    database_alias = resolve_database_alias(user, using)
    with transaction.atomic(using=database_alias):
        conta = lock_user_account(user, using=database_alias)
        if conta.is_deleted or not conta.is_active:
            return None

        get_token_model().objects.using(database_alias).filter(
            responsavel=conta,
            type=TokenType.RESET_PASSWORD,
            revoked_at__isnull=True,
        ).update(revoked_at=timezone.now(), revoked_by=conta)

        return issue_token(
            responsavel=conta,
            token_type=TokenType.RESET_PASSWORD,
            created_by=conta,
            expiry=password_reset_lifetime(),
            metadata_input={},
            using=database_alias,
        )


def find_active_password_reset(plain_token, *, using=None):
    """Resolve o token puro no `AuthToken` de reset correspondente, se ainda valer.

    Args:
        plain_token: Token recebido do cliente.
        using: Alias do banco da operação.

    Returns:
        O `AuthToken` bloqueado para atualização, ou `None` se o token não
        existir, não for de reset, estiver expirado, revogado ou pertencer a um
        usuário inativo ou excluído.
    """
    digest = _reset_token_digest(plain_token)
    if digest is None:
        return None

    database_alias = using or "default"
    token = (
        get_token_model()
        .objects.using(database_alias)
        .select_for_update()
        .select_related("responsavel")
        .filter(
            digest=digest,
            type=TokenType.RESET_PASSWORD,
            revoked_at__isnull=True,
        )
        .first()
    )

    if token is None:
        return None
    if token.expiry is not None and token.expiry < timezone.now():
        return None
    if not token.responsavel.is_active or token.responsavel.is_deleted:
        return None
    return token


def consume_password_reset(plain_token, new_password, *, using=None):
    """Troca a senha a partir de um token de redefinição e queima o token.

    A validação da senha, a marcação do token como consumido e a revogação das
    credenciais acontecem na mesma transação: uma senha nova convivendo com
    sessões antigas, ainda que por um instante, é exatamente o que o reset
    existe para evitar.

    A conta é bloqueada antes do token, na ordem global de locks
    (`Usuario` → `AuthToken`), para não inverter a ordem de `Usuario.delete()`.

    Args:
        plain_token: Token puro recebido do cliente.
        new_password: Nova senha em texto puro.
        using: Alias do banco da operação.

    Returns:
        O usuário com a senha já trocada, ou `None` se o token não for válido ou
        a conta não existir mais.

    Raises:
        django.core.exceptions.ValidationError: Se a senha violar a política.
    """
    digest = _reset_token_digest(plain_token)
    if digest is None:
        return None

    database_alias = using or "default"
    with transaction.atomic(using=database_alias):
        # Leitura sem lock só para descobrir de quem é o token: o lock da
        # conta precisa vir antes do lock do próprio token.
        responsavel_id = (
            get_token_model()
            .objects.using(database_alias)
            .filter(digest=digest, type=TokenType.RESET_PASSWORD)
            .values_list("responsavel_id", flat=True)
            .first()
        )
        if responsavel_id is None:
            return None

        conta = lock_user_account(responsavel_id, using=database_alias)
        if conta.is_deleted or not conta.is_active:
            return None

        token = find_active_password_reset(plain_token, using=database_alias)
        if token is None:
            return None

        user = token.responsavel
        set_validated_password(user, new_password)

        token.revoked_at = timezone.now()
        token.revoked_by = user
        token.save(using=database_alias, update_fields=["revoked_at", "revoked_by"])

        revoke_credentials_after_password_change(user, using=database_alias)
        return user


def revoke_credentials_after_password_change(user, *, using=None):
    """Derruba tudo que autenticava `user` antes da troca de senha.

    Preserva as API keys: elas pertencem à integração, não à sessão humana.
    """
    agora = timezone.now()
    database_alias = resolve_database_alias(user, using)

    get_token_model().objects.using(database_alias).filter(
        responsavel=user,
        type__in=SESSION_TOKEN_TYPES,
        revoked_at__isnull=True,
    ).update(revoked_at=agora, revoked_by=user)

    TrustedDevice.objects.using(database_alias).filter(user=user, revoked_at__isnull=True).update(revoked_at=agora)

    MFAChallenge.objects.using(database_alias).filter(user=user, consumed_at__isnull=True).update(consumed_at=agora)
