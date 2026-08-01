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
from .services import issue_token

# Tipos que representam "esta pessoa, neste dispositivo". Não inclui API_KEY.
SESSION_TOKEN_TYPES = (TokenType.TOKEN, TokenType.PRE_AUTH, TokenType.RESET_PASSWORD)


def password_reset_lifetime() -> timedelta:
    return timedelta(minutes=settings.PASSWORD_RESET_TIMEOUT_MINUTES)


def issue_password_reset(user):
    """Emite um token de redefinição, invalidando os anteriores do mesmo usuário.

    Só um reset fica válido por vez: dois links ativos dobram a janela em que um
    e-mail interceptado ainda serve.

    Args:
        user: Dono da conta.

    Returns:
        `IssuedToken` — o token puro só existe no retorno, nunca é persistido.
    """
    with transaction.atomic():
        get_token_model().objects.filter(
            responsavel=user,
            type=TokenType.RESET_PASSWORD,
            revoked_at__isnull=True,
        ).update(revoked_at=timezone.now(), revoked_by=user)

        return issue_token(
            responsavel=user,
            token_type=TokenType.RESET_PASSWORD,
            created_by=user,
            expiry=password_reset_lifetime(),
            metadata_input={},
        )


def find_active_password_reset(plain_token):
    """Resolve o token puro no `AuthToken` de reset correspondente, se ainda valer.

    Args:
        plain_token: Token recebido do cliente.

    Returns:
        O `AuthToken` bloqueado para atualização, ou `None` se o token não
        existir, não for de reset, estiver expirado, revogado ou pertencer a um
        usuário inativo.
    """
    try:
        digest = crypto.hash_token(plain_token)
    except (TypeError, ValueError):
        # Token com formato impossível de hashear: mesmo desfecho de token errado.
        return None

    token = (
        get_token_model()
        .objects.select_for_update()
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
    if not token.responsavel.is_active:
        return None
    return token


@transaction.atomic
def consume_password_reset(plain_token, new_password):
    """Troca a senha a partir de um token de redefinição e queima o token.

    A validação da senha, a marcação do token como consumido e a revogação das
    credenciais acontecem na mesma transação: uma senha nova convivendo com
    sessões antigas, ainda que por um instante, é exatamente o que o reset
    existe para evitar.

    Args:
        plain_token: Token puro recebido do cliente.
        new_password: Nova senha em texto puro.

    Returns:
        O usuário com a senha já trocada, ou `None` se o token não for válido.

    Raises:
        django.core.exceptions.ValidationError: Se a senha violar a política.
    """
    token = find_active_password_reset(plain_token)
    if token is None:
        return None

    user = token.responsavel
    set_validated_password(user, new_password)

    token.revoked_at = timezone.now()
    token.revoked_by = user
    token.save(update_fields=["revoked_at", "revoked_by"])

    revoke_credentials_after_password_change(user)
    return user


def revoke_credentials_after_password_change(user):
    """Derruba tudo que autenticava `user` antes da troca de senha.

    Preserva as API keys: elas pertencem à integração, não à sessão humana.
    """
    agora = timezone.now()

    get_token_model().objects.filter(
        responsavel=user,
        type__in=SESSION_TOKEN_TYPES,
        revoked_at__isnull=True,
    ).update(revoked_at=agora, revoked_by=user)

    TrustedDevice.objects.filter(user=user, revoked_at__isnull=True).update(revoked_at=agora)

    MFAChallenge.objects.filter(user=user, consumed_at__isnull=True).update(consumed_at=agora)
