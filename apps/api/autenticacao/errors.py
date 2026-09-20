from django.db import models
from django.utils.translation import gettext_lazy as _


class AuthErrorCode(models.TextChoices):
    NOT_AUTHENTICATED = "auth.not_authenticated", _("Autenticação necessária.")
    TOKEN_NOT_PROVIDED = "auth.token_not_provided", _("Token não fornecido.")
    INVALID_TOKEN = "auth.invalid_token", _("Token inválido.")
    EXPIRED_TOKEN = "auth.expired_token", _("Token expirado.")
    INVALID_CREDENTIALS = "auth.invalid_credentials", _("E-mail ou senha inválidos.")
    USER_INACTIVE = "auth.user_inactive", _("Usuário inativo.")
    PERMISSION_DENIED = "auth.permission_denied", _("Você não tem permissão para executar essa ação.")
    INSUFFICIENT_SCOPE = "auth.insufficient_scope", _("Escopo insuficiente para essa operação.")
    INVALID_SCOPE = "auth.invalid_scope", _("Scope inválido.")
    REAUTHENTICATION_REQUIRED = "auth.reauthentication_required", _("Reautenticação necessária.")
    SCOPE_NOT_AVAILABLE = "auth.scope_not_available", _("Scope indisponível para API key.")
    SCOPE_NOT_DELEGABLE = "auth.scope_not_delegable", _("Você não pode conceder esse scope.")
    REVOKED_TOKEN = "auth.revoked_token", _("Token revogado.")
    API_KEY_SUSPENDED = "auth.api_key_suspended", _("Esta API key está suspensa.")
    RESPONSIBLE_INACTIVE = "auth.responsible_inactive", _("O responsável por esta credencial está inativo.")
    TOKEN_LIMIT_EXCEEDED = "auth.token_limit_exceeded", _("Limite de sessões ativas excedido.")
    GOOGLE_TOKEN_INVALID = "auth.google_token_invalid", _("Não foi possível validar a identidade do Google.")

    # MFA / 2FA
    INVALID_CHALLENGE = "auth.invalid_challenge", _("Desafio de verificação inválido ou expirado.")
    INVALID_OTP = "auth.invalid_otp", _("Código de verificação inválido.")
    OTP_COOLDOWN = "auth.otp_cooldown", _("Aguarde antes de solicitar um novo código.")
    TOO_MANY_ATTEMPTS = "auth.too_many_attempts", _("Tentativas demais. Tente novamente mais tarde.")
    PWNED_PASSWORD = "auth.pwned_password", _("Esta senha apareceu em vazamentos públicos. Escolha outra.")
    DELIVERY_UNAVAILABLE = "auth.delivery_unavailable", _("Não foi possível enviar o código de verificação.")
