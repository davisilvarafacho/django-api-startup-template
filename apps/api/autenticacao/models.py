import uuid as uuid_lib

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from knox import crypto
from knox.settings import CONSTANTS, knox_settings

from apps.api.base.models import CreationAuditMixin
from utils.logs import register


class TokenType(models.IntegerChoices):
    TOKEN = 1, _('Token')
    RESET_PASSWORD = 2, _('Reset de senha')
    API_KEY = 999, _('API key')


class AuthTokenManager(models.Manager):
    """Mantém a assinatura do manager do Knox, mas persiste em `responsavel`."""

    def create(self, user=None, expiry=knox_settings.TOKEN_TTL, prefix=knox_settings.TOKEN_PREFIX, **kwargs):
        responsavel = kwargs.pop("responsavel", user)
        token_type = kwargs.get("type", TokenType.TOKEN)
        name = kwargs.get("name", "")
        if isinstance(name, str):
            name = name.strip()
            kwargs["name"] = name

        validate_token_configuration(
            responsavel=responsavel,
            token_type=token_type,
            created_by=kwargs.get("created_by") or kwargs.get("created_by_id"),
            organization=kwargs.get("organization") or kwargs.get("organization_id"),
            name=name,
            scopes=kwargs.get("scopes", ()),
        )

        plain_token = prefix + crypto.create_token_string()
        digest = crypto.hash_token(plain_token)
        expires_at = timezone.now() + expiry if expiry is not None else None

        instance = super().create(
            digest=digest,
            token_key=plain_token[:CONSTANTS.TOKEN_KEY_LENGTH],
            responsavel=responsavel,
            expiry=expires_at,
            **kwargs,
        )
        return instance, plain_token


def validate_token_configuration(
    *,
    responsavel,
    token_type,
    created_by,
    organization,
    name,
    scopes,
):
    """Valida os campos que diferenciam uma API key dos demais tokens."""
    if token_type != TokenType.API_KEY:
        if organization is not None or name or scopes:
            raise ValidationError(
                "Tokens de sessão/reset não aceitam organization, name ou scopes."
            )
        return

    errors = {}
    if organization is None:
        errors["organization"] = "API key exige uma organização."
    if not name or not str(name).strip():
        errors["name"] = "API key exige um nome."
    if created_by is None:
        errors["created_by"] = "API key exige o usuário que a criou."
    if responsavel is None or not getattr(responsavel, "is_active", False):
        errors["responsavel"] = "API key exige um responsável ativo."

    if not isinstance(scopes, (list, tuple)):
        errors["scopes"] = "Scopes precisam ser uma lista."
    else:
        from apps.api.core.scope_registry import validate_registered_scope

        try:
            for scope in scopes:
                validate_registered_scope(scope)
        except (ImproperlyConfigured, TypeError, ValueError) as exc:
            errors["scopes"] = str(exc)

    if organization is not None and responsavel is not None:
        from apps.organizacoes.models import Vinculo

        if not Vinculo.objects.filter(
            organizacao=organization,
            usuario=responsavel,
            is_active=True,
        ).exists():
            errors["responsavel"] = (
                "O responsável precisa ter vínculo ativo com a organização."
            )

    if errors:
        raise ValidationError(errors)


class AuthToken(CreationAuditMixin):
    """Token próprio, compatível com o contrato do `knox.AuthToken` (swappable).

    Cobre sessão (`TokenType.TOKEN`), reset de senha (`RESET_PASSWORD`) e API
    key (`API_KEY`) num único modelo. `responsavel` é o usuário autenticável
    (para o Knox e para `request.user`); `created_by` — herdado do mixin — é
    quem emitiu a credencial, útil quando um admin cria uma API key para
    outra pessoa.
    """

    objects = AuthTokenManager()

    uuid = models.UUIDField(
        verbose_name=_("UUID"), default=uuid_lib.uuid4, unique=True, editable=False, db_index=True
    )

    digest = models.CharField(verbose_name=_("digest"), max_length=CONSTANTS.DIGEST_LENGTH, primary_key=True)
    token_key = models.CharField(
        verbose_name=_("chave do token"),
        max_length=CONSTANTS.MAXIMUM_TOKEN_PREFIX_LENGTH + CONSTANTS.TOKEN_KEY_LENGTH,
        db_index=True,
    )

    responsavel = models.ForeignKey(
        verbose_name=_("responsável"),
        to=settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="auth_token_set",
    )

    expiry = models.DateTimeField(verbose_name=_("expira em"), null=True, blank=True)

    type = models.PositiveSmallIntegerField(
        verbose_name=_("tipo"),
        choices=TokenType.choices,
        default=TokenType.TOKEN,
        db_index=True,
    )
    name = models.CharField(verbose_name=_("nome"), max_length=100, blank=True)
    organization = models.ForeignKey(
        verbose_name=_("organização"),
        to="organizacoes.Organizacao",
        on_delete=models.PROTECT,
        related_name="api_keys",
        null=True,
        blank=True,
    )
    scopes = models.JSONField(
        verbose_name=_("escopos"),
        default=list,
        blank=True,
        help_text=_("Escopos `resource:action` concedidos a uma API key."),
    )

    revoked_at = models.DateTimeField(verbose_name=_("revogado em"), null=True, blank=True)
    revoked_by = models.ForeignKey(
        verbose_name=_("revogado por"),
        to=settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="+",
        null=True,
        blank=True,
    )

    suspended_at = models.DateTimeField(verbose_name=_("suspenso em"), null=True, blank=True)
    suspended_by = models.ForeignKey(
        verbose_name=_("suspenso por"),
        to=settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="+",
        null=True,
        blank=True,
    )
    suspension_reason = models.CharField(verbose_name=_("motivo da suspensão"), max_length=255, blank=True)

    replaced_by = models.OneToOneField(
        verbose_name=_("substituído por"),
        to="self",
        on_delete=models.SET_NULL,
        related_name="replaces",
        null=True,
        blank=True,
    )

    class Meta:
        swappable = "KNOX_TOKEN_MODEL"
        db_table = "auth_token"
        ordering = ("-created_at",)
        verbose_name = _("Token de autenticação")
        verbose_name_plural = _("Tokens de autenticação")
        constraints = [
            models.CheckConstraint(
                condition=(
                    (models.Q(type=TokenType.API_KEY) & models.Q(organization__isnull=False))
                    | (~models.Q(type=TokenType.API_KEY) & models.Q(organization__isnull=True))
                ),
                name="auth_token_api_key_exige_organizacao",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(type=TokenType.API_KEY)
                    | (
                        models.Q(created_by__isnull=False)
                        & ~models.Q(name="")
                    )
                ),
                name="auth_token_api_key_exige_nome_e_criador",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(type=TokenType.API_KEY)
                    | (models.Q(name="") & models.Q(scopes=[]))
                ),
                name="auth_token_sessao_sem_campos_de_api_key",
            ),
        ]
        # Permissions humanas explícitas de gerenciamento de API key. Não são os
        # defaults do model (`view_authtoken`/`add_authtoken`/...): ser criador ou
        # responsável de uma key não concede autoridade administrativa sobre ela.
        permissions = [
            ("view_apikey", "Pode ver API keys"),
            ("add_apikey", "Pode criar API keys"),
            ("change_apikey", "Pode alterar API keys"),
            ("delete_apikey", "Pode revogar API keys"),
            ("rotate_apikey", "Pode rotacionar API keys"),
        ]

    def __str__(self):
        return f"{self.get_type_display()} {self.uuid}"

    # ---- compatibilidade interna com o Knox (nunca públicos na API) ----
    @property
    def user(self):
        return self.responsavel

    @user.setter
    def user(self, value):
        self.responsavel = value

    @property
    def created(self):
        return self.created_at


class TokenMetaData(models.Model):
    """Metadados operacionais do uso de uma credencial (1:1 com o token)."""

    # token relacionado
    token = models.OneToOneField(
        verbose_name=_('Token'),
        to=settings.KNOX_TOKEN_MODEL,
        on_delete=models.CASCADE,
        related_name='metadata',
        primary_key=True
    )

    # Verificação recente de identidade (step-up auth). Preenchido só quando a
    # sessão passa por `POST /auth/reauthenticate/`; usado por
    # `RecentAuthenticationPermission`/`@require_recent_auth`.
    reauthenticated_at = models.DateTimeField(
        verbose_name=_('Reautenticado em'),
        null=True,
        blank=True,
        help_text=_('Última vez que esta sessão confirmou a identidade (senha/MFA).'),
    )

    # informações do dispositivo
    device_name = models.CharField(
        verbose_name=_('Nome do dispositivo'),
        max_length=255,
        blank=True,
        help_text=_("Nome personalizado do dispositivo (ex: 'iPhone de Rafael')")
    )
    device_type = models.CharField(
        verbose_name=_('Tipo de dispositivo'),
        max_length=20,
        choices=[
            ('mobile', _('Celular')),
            ('tablet', _('Tablet')),
            ('desktop', _('Computador')),
            ('unknown', _('Desconhecido')),
        ],
        default='unknown'
    )
    device_brand = models.CharField(
        verbose_name=_('Marca do dispositivo'),
        max_length=100,
        blank=True,
        help_text=_("Marca do dispositivo (ex: Apple, Samsung, etc)")
    )
    device_model = models.CharField(
        verbose_name=_('Modelo do dispositivo'),
        max_length=100,
        blank=True,
        help_text=_("Modelo do dispositivo (ex: iPhone 14 Pro, Galaxy S23)")
    )

    # sistema operacional
    os_name = models.CharField(
        verbose_name=_('Nome do sistema operacional'),
        max_length=50,
        blank=True,
        help_text=_("Nome do SO (iOS, Android, Windows, macOS, Linux)")
    )
    os_version = models.CharField(
        verbose_name=_('Versão do sistema operacional'),
        max_length=50,
        blank=True,
        help_text=_("Versão do SO")
    )

    # client
    browser_name = models.CharField(
        verbose_name=_('Nome do navegador'),
        max_length=50,
        blank=True,
        help_text=_("Nome do navegador ou app")
    )
    browser_version = models.CharField(
        verbose_name=_('Versão do navegador'),
        max_length=50,
        blank=True
    )
    user_agent = models.TextField(
        verbose_name=_('User agent'),
        blank=True,
        help_text=_("User agent completo da requisição")
    )

    # ip da origem
    ip_address = models.GenericIPAddressField(
        verbose_name=_('Endereço IP'),
        null=True,
        blank=True,
        help_text=_("Endereço IP da origem")
    )

    # localização geográfica baseado no ip
    country = models.CharField(
        verbose_name=_('País'),
        max_length=100,
        blank=True,
        help_text=_("País")
    )
    country_code = models.CharField(
        verbose_name=_('Código do país'),
        max_length=2,
        blank=True,
        help_text=_("Código do país (ISO 3166-1 alpha-2)")
    )
    region = models.CharField(
        verbose_name=_('Estado/Região'),
        max_length=100,
        blank=True,
        help_text=_("Estado/Região")
    )
    city = models.CharField(
        verbose_name=_('Cidade'),
        max_length=100,
        blank=True,
        help_text=_("Cidade")
    )
    latitude = models.DecimalField(
        verbose_name=_('Latitude'),
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True
    )
    longitude = models.DecimalField(
        verbose_name=_('Longitude'),
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True
    )
    timezone = models.CharField(
        verbose_name=_('Fuso horário'),
        max_length=50,
        blank=True,
        help_text=_("Fuso horário (ex: America/Sao_Paulo)")
    )
    isp = models.CharField(
        verbose_name=_('Provedor de internet'),
        max_length=255,
        blank=True,
        help_text=_("Provedor de internet")
    )

    # informações de uso
    first_used = models.DateTimeField(
        verbose_name=_('Primeiro uso'),
        auto_now_add=True,
        help_text=_("Primeira vez que o token foi usado")
    )
    last_used = models.DateTimeField(
        verbose_name=_('Último uso'),
        auto_now=True,
        help_text=_("Última vez que o token foi usado")
    )
    usage_count = models.PositiveIntegerField(
        verbose_name=_('Contador de uso'),
        default=0,
        help_text=_("Número de vezes que o token foi usado")
    )

    # segurança e risco
    is_suspicious = models.BooleanField(
        verbose_name=_('É suspeito'),
        default=False,
        help_text=_("Marcado como suspeito por mudança de IP/localização")
    )
    suspicious_reason = models.TextField(
        verbose_name=_('Motivo da suspeita'),
        blank=True,
        help_text=_("Motivo da suspeita")
    )
    risk_score = models.PositiveSmallIntegerField(
        verbose_name=_('Pontuação de risco'),
        default=0,
        help_text=_("Score de risco (0-100)")
    )

    # adicionais do app/frontend
    app_version = models.CharField(
        verbose_name=_('Versão do app'),
        max_length=20,
        blank=True,
        help_text=_("Versão do app/frontend (ex: 1.2.3)")
    )
    fcm_token = models.TextField(
        verbose_name=_('Token FCM'),
        blank=True,
        help_text=_("Token para push notifications (Firebase Cloud Messaging)")
    )

    # metadados customizados
    extra_data = models.JSONField(
        verbose_name=_('Dados extras'),
        default=dict,
        blank=True,
        help_text=_("Dados adicionais em formato JSON")
    )

    def mark_as_suspicious(self, reason):
        """Marca o token como suspeito"""

        self.is_suspicious = True
        self.suspicious_reason = reason
        self.save(update_fields=['is_suspicious', 'suspicious_reason'])

    def increment_usage(self):
        """Incrementa o contador de uso"""

        self.usage_count += 1
        self.save(update_fields=['usage_count', 'last_used'])

    def get_location_string(self):
        """Retorna string formatada da localização"""

        parts = [p for p in [self.city, self.region, self.country] if p]
        return ', '.join(parts) if parts else _('Localização desconhecida')

    class Meta:
        db_table = 'token_metadata'
        ordering = ['-last_used']
        verbose_name = _('Metadado de token')
        verbose_name_plural = _('Metadados de tokens')
        permissions = [
            ("grant_unrestricted_apikey", _("Pode conceder API keys com scope irrestrito (*)")),
        ]

    def __str__(self):
        return f"{self.device_name or self.device_type} - {self.token.responsavel}"


# class PasswordResetToken(models.Model):
#     """
#     Token para redefinição de senha
#     """
#     user = models.ForeignKey(
#         User,
#         on_delete=models.CASCADE,
#         related_name='password_reset_tokens'
#     )
#     token_hash = models.CharField(
#         max_length=128,
#         unique=True,
#         db_index=True,
#         help_text="Hash SHA-256 do token"
#     )
#     token_key = models.CharField(
#         max_length=8,
#         db_index=True,
#         help_text="Primeiros 8 caracteres para busca rápida"
#     )
#     created_at = models.DateTimeField(auto_now_add=True)
#     expires_at = models.DateTimeField()
#     used_at = models.DateTimeField(null=True, blank=True)
#     is_used = models.BooleanField(default=False)
#
#     # Informações de segurança
#     ip_address = models.GenericIPAddressField(
#         null=True,
#         blank=True,
#         help_text="IP que solicitou a redefinição"
#     )
#     user_agent = models.TextField(blank=True)
#
#     # Informações de uso
#     reset_ip_address = models.GenericIPAddressField(
#         null=True,
#         blank=True,
#         help_text="IP que usou o token para redefinir"
#     )
#     reset_user_agent = models.TextField(blank=True)
#
#     class Meta:
#         verbose_name = 'Password Reset Token'
#         verbose_name_plural = 'Password Reset Tokens'
#         ordering = ['-created_at']
#         indexes = [
#             models.Index(fields=['token_hash']),
#             models.Index(fields=['user', '-created_at']),
#         ]
#
#     def __str__(self):
#         return f"Reset token for {self.user.username} - {self.created_at}"
#
#     @classmethod
#     def generate_token(cls):
#         """Gera um token seguro de 64 caracteres"""
#         return secrets.token_urlsafe(48)  # Gera ~64 caracteres
#
#     @classmethod
#     def create_for_user(cls, user, request=None, expiry_hours=24):
#         """
#         Cria um novo token de redefinição para o usuário
#         Invalida tokens anteriores não usados
#         """
#         # Invalida tokens anteriores não usados
#         cls.objects.filter(
#             user=user,
#             is_used=False,
#             expires_at__gt=timezone.now()
#         ).update(is_used=True, used_at=timezone.now())
#
#         # Gera novo token
#         raw_token = cls.generate_token()
#         token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
#
#         # Extrai informações da requisição
#         ip_address = None
#         user_agent = ''
#         if request:
#             from .utils.ip_geolocation import get_client_ip
#             ip_address = get_client_ip(request)
#             user_agent = request.META.get('HTTP_USER_AGENT', '')
#
#         # Cria o token
#         reset_token = cls.objects.create(
#             user=user,
#             token_hash=token_hash,
#             token_key=raw_token[:8],
#             expires_at=timezone.now() + timedelta(hours=expiry_hours),
#             ip_address=ip_address,
#             user_agent=user_agent,
#         )
#
#         # Retorna o token em texto plano (só é visível aqui!)
#         return raw_token, reset_token
#
#     @classmethod
#     def get_by_token(cls, raw_token):
#         """
#         Busca um token válido pelo valor em texto plano
#         """
#         token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
#
#         try:
#             reset_token = cls.objects.get(
#                 token_hash=token_hash,
#                 is_used=False,
#                 expires_at__gt=timezone.now()
#             )
#             return reset_token
#         except cls.DoesNotExist:
#             return None
#
#     def is_valid(self):
#         """Verifica se o token ainda é válido"""
#         return (
#                 not self.is_used and
#                 self.expires_at > timezone.now()
#         )
#
#     def mark_as_used(self, request=None):
#         """Marca o token como usado"""
#         self.is_used = True
#         self.used_at = timezone.now()
#
#         if request:
#             from .utils import get_client_ip
#             self.reset_ip_address = get_client_ip(request)
#             self.reset_user_agent = request.META.get('HTTP_USER_AGENT', '')
#
#         self.save()
#
#     def get_time_until_expiry(self):
#         """Retorna tempo até expiração"""
#         if self.expires_at <= timezone.now():
#             return timedelta(0)
#         return self.expires_at - timezone.now()


# class PasswordResetAttempt(models.Model):
#     """
#     Registra tentativas de redefinição para prevenção de abuso
#     """
#     email = models.EmailField()
#     ip_address = models.GenericIPAddressField()
#     attempted_at = models.DateTimeField(auto_now_add=True)
#     success = models.BooleanField(default=False)
#
#     class Meta:
#         verbose_name = 'Password Reset Attempt'
#         verbose_name_plural = 'Password Reset Attempts'
#         ordering = ['-attempted_at']
#         indexes = [
#             models.Index(fields=['email', '-attempted_at']),
#             models.Index(fields=['ip_address', '-attempted_at']),
#         ]
#
#     @classmethod
#     def check_rate_limit(cls, email=None, ip_address=None, limit=5, window_minutes=60):
#         """
#         Verifica se o email ou IP excedeu o limite de tentativas
#
#         Returns:
#             tuple: (is_allowed, attempts_count, time_until_reset)
#         """
#         from datetime import timedelta
#
#         window_start = timezone.now() - timedelta(minutes=window_minutes)
#
#         # Conta tentativas por email
#         if email:
#             email_attempts = cls.objects.filter(
#                 email=email,
#                 attempted_at__gte=window_start
#             ).count()
#
#             if email_attempts >= limit:
#                 oldest_attempt = cls.objects.filter(
#                     email=email,
#                     attempted_at__gte=window_start
#                 ).earliest('attempted_at')
#
#                 time_until_reset = (
#                         oldest_attempt.attempted_at +
#                         timedelta(minutes=window_minutes) -
#                         timezone.now()
#                 )
#
#                 return False, email_attempts, time_until_reset
#
#         # Conta tentativas por IP
#         if ip_address:
#             ip_attempts = cls.objects.filter(
#                 ip_address=ip_address,
#                 attempted_at__gte=window_start
#             ).count()
#
#             if ip_attempts >= limit * 2:  # IP pode ter limite maior
#                 oldest_attempt = cls.objects.filter(
#                     ip_address=ip_address,
#                     attempted_at__gte=window_start
#                 ).earliest('attempted_at')
#
#                 time_until_reset = (
#                         oldest_attempt.attempted_at +
#                         timedelta(minutes=window_minutes) -
#                         timezone.now()
#                 )
#
#                 return False, ip_attempts, time_until_reset
#
#         return True, 0, None
#
#     @classmethod
#     def record_attempt(cls, email, ip_address, success=False):
#         """Registra uma tentativa de redefinição"""
#         return cls.objects.create(
#             email=email,
#             ip_address=ip_address,
#             success=success
#         )


register(TokenMetaData)
