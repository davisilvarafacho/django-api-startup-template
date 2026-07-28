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
    REAUTHENTICATION_REQUIRED = "auth.reauthentication_required", _("Reautenticação necessária.")
    SCOPE_NOT_DELEGABLE = "auth.scope_not_delegable", _("Você não pode conceder esse scope.")
