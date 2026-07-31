from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from knox import crypto
from knox.settings import CONSTANTS, knox_settings

from utils.logs import register
from utils.sensitive_fields import encrypt


class TokenType(models.IntegerChoices):
    """Tipos operacionais armazenados em `AuthToken.type`."""

    TOKEN = 1, _("Token")
    RESET_PASSWORD = 2, _("Reset de senha")
    PRE_AUTH = 3, _("Pré-autenticação")
    API_KEY = 999, _("API key")


class MFAFactorType(models.TextChoices):
    """Tipos de segundo fator suportados pela API."""

    EMAIL = "email", _("E-mail")
    SMS = "sms", _("SMS")
    TOTP = "totp", _("TOTP")


class MFAChallengePurpose(models.TextChoices):
    """Fluxos que podem consumir um desafio MFA."""

    LOGIN = "login", _("Login")
    ENROLLMENT = "enrollment", _("Cadastro de fator")
    REAUTHENTICATION = "reauthentication", _("Reautenticação")


class MFAChallengeDeliveryStatus(models.TextChoices):
    """Estado da entrega de um OTP que depende de transporte externo."""

    PENDING = "pending", _("Pendente")
    SENT = "sent", _("Enviado")
    FAILED = "failed", _("Falhou")


class AuthTokenManager(models.Manager):
    """Manager Knox que emite o segredo puro apenas no momento da criação."""

    def create(self, user=None, expiry=knox_settings.TOKEN_TTL, prefix=knox_settings.TOKEN_PREFIX, **kwargs):
        responsavel = kwargs.pop("responsavel", user)
        plain_token = prefix + crypto.create_token_string()
        expires_at = timezone.now() + expiry if isinstance(expiry, timedelta) else expiry

        instance = self.model(
            digest=crypto.hash_token(plain_token),
            token_key=plain_token[: CONSTANTS.TOKEN_KEY_LENGTH],
            responsavel=responsavel,
            expiry=expires_at,
            **kwargs,
        )
        instance.full_clean()
        instance.save(force_insert=True, using=self._db)
        return instance, plain_token


class AuthToken(models.Model):
    """Token swappable compatível com Knox, com estado e expiração tipados.

    Esta é uma exceção arquitetural deliberada à herança de ``Base``: ``Base``
    adiciona tenant, RLS e campos de ciclo de vida incompatíveis com o contrato
    do Knox. Como modelo swappable, ``AuthToken`` deve preservar esse contrato
    e, por isso, herda diretamente de ``models.Model``.

    `EPHEMERAL_TYPES` reúne somente credenciais de curta duração, que podem ser
    removidas assim que expiram.
    """

    EPHEMERAL_TYPES = frozenset({TokenType.PRE_AUTH, TokenType.RESET_PASSWORD})

    objects = AuthTokenManager()

    digest = models.CharField(
        _("Digest"),
        max_length=CONSTANTS.DIGEST_LENGTH,
        primary_key=True,
        help_text=_("Hash criptográfico irreversível do segredo do token."),
        db_comment=_("Hash criptográfico irreversível do segredo do token."),
    )
    token_key = models.CharField(
        _("Prefixo do token"),
        max_length=CONSTANTS.MAXIMUM_TOKEN_PREFIX_LENGTH + CONSTANTS.TOKEN_KEY_LENGTH,
        db_index=True,
        help_text=_("Prefixo não sensível do token usado para identificação rápida."),
        db_comment=_("Prefixo não sensível do token usado para identificação rápida."),
    )
    responsavel = models.ForeignKey(
        verbose_name=_("Responsável"),
        to=settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="auth_token_set",
        help_text=_("Usuário responsável pelo token."),
        db_comment=_("Usuário responsável pelo token."),
    )
    created_at = models.DateTimeField(
        _("Criado em"),
        auto_now_add=True,
        help_text=_("Data e hora de emissão do token."),
        db_comment=_("Data e hora de emissão do token."),
    )
    expiry = models.DateTimeField(
        _("Expira em"),
        blank=True,
        null=True,
        help_text=_("Data e hora de expiração do token, obrigatória para tipos efêmeros."),
        db_comment=_("Data e hora de expiração do token, obrigatória para tipos efêmeros."),
    )
    type = models.PositiveSmallIntegerField(
        _("Tipo"),
        choices=TokenType.choices,
        default=TokenType.TOKEN,
        db_index=True,
        help_text=_("Tipo operacional do token."),
        db_comment=_("Tipo operacional do token."),
    )
    scopes = models.JSONField(
        _("Escopos"),
        blank=True,
        default=list,
        help_text=_("Escopos autorizados para o token, aplicáveis a API keys."),
        db_comment=_("Escopos autorizados para o token, aplicáveis a API keys."),
    )

    class Meta:
        db_table = "auth_token"
        ordering = ("-created_at",)
        swappable = "KNOX_TOKEN_MODEL"
        verbose_name = _("Token de autenticação")
        verbose_name_plural = _("Tokens de autenticação")
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(type__in=(TokenType.PRE_AUTH, TokenType.RESET_PASSWORD)) | models.Q(expiry__isnull=False),
                name="auth_token_ephemeral_requires_expiry",
            ),
        ]

    def clean(self):
        super().clean()
        if self.type in self.EPHEMERAL_TYPES and self.expiry is None:
            raise ValidationError({"expiry": _("Tokens efêmeros exigem uma data de expiração.")})

    @property
    def user(self):
        """Alias interno que preserva o contrato esperado pelo Knox."""
        return self.responsavel

    @user.setter
    def user(self, value):
        self.responsavel = value

    @property
    def created(self):
        """Alias interno que preserva o contrato esperado pelo Knox."""
        return self.created_at

    @property
    def is_expired(self):
        """Indica se o token tem expiração alcançada no horário atual."""
        return self.expiry is not None and self.expiry <= timezone.now()

    def __str__(self):
        return f"{self.get_type_display()} {self.token_key}"


class TokenMetaData(models.Model):
    """Metadados completos para tokens Knox"""

    # token relacionado
    token = models.OneToOneField(
        verbose_name=_("Token"), to=settings.KNOX_TOKEN_MODEL, on_delete=models.CASCADE, related_name="metadata", primary_key=True
    )
    # informações do dispositivo
    device_name = models.CharField(
        verbose_name=_("Nome do dispositivo"), max_length=255, blank=True, help_text=_("Nome personalizado do dispositivo (ex: 'iPhone de Rafael')")
    )
    device_type = models.CharField(
        verbose_name=_("Tipo de dispositivo"),
        max_length=20,
        choices=[
            ("mobile", _("Celular")),
            ("tablet", _("Tablet")),
            ("desktop", _("Computador")),
            ("unknown", _("Desconhecido")),
        ],
        default="unknown",
    )
    device_brand = models.CharField(
        verbose_name=_("Marca do dispositivo"), max_length=100, blank=True, help_text=_("Marca do dispositivo (ex: Apple, Samsung, etc)")
    )
    device_model = models.CharField(
        verbose_name=_("Modelo do dispositivo"), max_length=100, blank=True, help_text=_("Modelo do dispositivo (ex: iPhone 14 Pro, Galaxy S23)")
    )

    # sistema operacional
    os_name = models.CharField(
        verbose_name=_("Nome do sistema operacional"), max_length=50, blank=True, help_text=_("Nome do SO (iOS, Android, Windows, macOS, Linux)")
    )
    os_version = models.CharField(verbose_name=_("Versão do sistema operacional"), max_length=50, blank=True, help_text=_("Versão do SO"))

    # client
    browser_name = models.CharField(verbose_name=_("Nome do navegador"), max_length=50, blank=True, help_text=_("Nome do navegador ou app"))
    browser_version = models.CharField(verbose_name=_("Versão do navegador"), max_length=50, blank=True)
    user_agent = models.TextField(verbose_name=_("User agent"), blank=True, help_text=_("User agent completo da requisição"))

    # ip da origem
    ip_address = models.GenericIPAddressField(verbose_name=_("Endereço IP"), null=True, blank=True, help_text=_("Endereço IP da origem"))

    # localização geográfica baseado no ip
    country = models.CharField(verbose_name=_("País"), max_length=100, blank=True, help_text=_("País"))
    country_code = models.CharField(verbose_name=_("Código do país"), max_length=2, blank=True, help_text=_("Código do país (ISO 3166-1 alpha-2)"))
    region = models.CharField(verbose_name=_("Estado/Região"), max_length=100, blank=True, help_text=_("Estado/Região"))
    city = models.CharField(verbose_name=_("Cidade"), max_length=100, blank=True, help_text=_("Cidade"))
    latitude = models.DecimalField(verbose_name=_("Latitude"), max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(verbose_name=_("Longitude"), max_digits=9, decimal_places=6, null=True, blank=True)
    timezone = models.CharField(verbose_name=_("Fuso horário"), max_length=50, blank=True, help_text=_("Fuso horário (ex: America/Sao_Paulo)"))
    isp = models.CharField(verbose_name=_("Provedor de internet"), max_length=255, blank=True, help_text=_("Provedor de internet"))

    # informações de uso
    first_used = models.DateTimeField(verbose_name=_("Primeiro uso"), auto_now_add=True, help_text=_("Primeira vez que o token foi usado"))
    last_used = models.DateTimeField(verbose_name=_("Último uso"), auto_now=True, help_text=_("Última vez que o token foi usado"))
    usage_count = models.PositiveIntegerField(verbose_name=_("Contador de uso"), default=0, help_text=_("Número de vezes que o token foi usado"))

    reauthenticated_at = models.DateTimeField(
        _("Reautenticado em"),
        blank=True,
        null=True,
        help_text=_("Data e hora da última confirmação recente de senha da sessão."),
        db_comment=_("Data e hora da última confirmação recente de senha da sessão."),
    )

    # segurança e risco
    is_suspicious = models.BooleanField(
        verbose_name=_("É suspeito"), default=False, help_text=_("Marcado como suspeito por mudança de IP/localização")
    )
    suspicious_reason = models.TextField(verbose_name=_("Motivo da suspeita"), blank=True, help_text=_("Motivo da suspeita"))
    risk_score = models.PositiveSmallIntegerField(verbose_name=_("Pontuação de risco"), default=0, help_text=_("Score de risco (0-100)"))

    # adicionais do app/frontend
    app_version = models.CharField(verbose_name=_("Versão do app"), max_length=20, blank=True, help_text=_("Versão do app/frontend (ex: 1.2.3)"))
    fcm_token = models.TextField(verbose_name=_("Token FCM"), blank=True, help_text=_("Token para push notifications (Firebase Cloud Messaging)"))

    # metadados customizados
    extra_data = models.JSONField(verbose_name=_("Dados extras"), default=dict, blank=True, help_text=_("Dados adicionais em formato JSON"))

    def mark_as_suspicious(self, reason):
        """Marca o token como suspeito"""

        self.is_suspicious = True
        self.suspicious_reason = reason
        self.save(update_fields=["is_suspicious", "suspicious_reason"])

    def increment_usage(self):
        """Incrementa o contador de uso"""

        self.usage_count += 1
        self.save(update_fields=["usage_count", "last_used"])

    def get_location_string(self):
        """Retorna string formatada da localização"""

        parts = [p for p in [self.city, self.region, self.country] if p]
        return ", ".join(parts) if parts else _("Localização desconhecida")

    class Meta:
        db_table = "token_metadata"
        ordering = ["-last_used"]
        verbose_name = _("Metadado de token")
        verbose_name_plural = _("Metadados de tokens")

    def __str__(self):
        return f"{self.device_name or self.device_type} - {self.token.user.username}"


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


class MFAFactor(models.Model):
    """Segundo fator configurado pelo usuário, com no máximo um de cada tipo."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mfa_factors", verbose_name=_("usuário"))
    type = models.CharField(_("tipo"), max_length=16, choices=MFAFactorType.choices)
    secret = encrypt(
        models.CharField(
            _("segredo"),
            max_length=64,
            blank=True,
            null=True,
            default=None,
            help_text=_("Segredo TOTP cifrado."),
            db_comment=_("Segredo TOTP cifrado."),
        )
    )
    confirmed_at = models.DateTimeField(_("confirmado em"), blank=True, null=True)
    enabled_at = models.DateTimeField(_("ativado em"), blank=True, null=True)
    disabled_at = models.DateTimeField(_("desativado em"), blank=True, null=True)
    last_used_at = models.DateTimeField(_("usado por último em"), blank=True, null=True)
    totp_last_counter = models.PositiveBigIntegerField(_("último contador TOTP"), blank=True, null=True)
    totp_algorithm = models.CharField(_("algoritmo TOTP"), max_length=16, default="SHA1")
    totp_digits = models.PositiveSmallIntegerField(_("dígitos TOTP"), default=6)
    totp_period = models.PositiveSmallIntegerField(_("período TOTP"), default=30)

    class Meta:
        db_table = "mfa_factor"
        constraints = [
            models.UniqueConstraint(fields=("user", "type"), name="mfa_factor_unique_user_type"),
            models.CheckConstraint(
                condition=(models.Q(type=MFAFactorType.TOTP) & models.Q(secret__isnull=False))
                | (~models.Q(type=MFAFactorType.TOTP) & models.Q(secret__isnull=True)),
                name="mfa_factor_totp_secret_only",
            ),
        ]


class MFAChallenge(models.Model):
    """Desafio de uso único para login, enrollment ou reautenticação."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mfa_challenges", verbose_name=_("usuário"))
    factor = models.ForeignKey(MFAFactor, on_delete=models.CASCADE, related_name="challenges", verbose_name=_("fator"))
    token = models.ForeignKey(
        AuthToken,
        on_delete=models.CASCADE,
        related_name="mfa_challenges",
        blank=True,
        null=True,
        verbose_name=_("token de autenticação"),
    )
    purpose = models.CharField(_("finalidade"), max_length=24, choices=MFAChallengePurpose.choices)
    otp_digest = models.CharField(_("digest do OTP"), max_length=128, blank=True)
    expires_at = models.DateTimeField(_("expira em"), db_index=True)
    cooldown_until = models.DateTimeField(_("cooldown até"), blank=True, null=True)
    attempts = models.PositiveSmallIntegerField(_("tentativas"), default=0)
    delivery_status = models.CharField(
        _("estado de entrega"), max_length=16, choices=MFAChallengeDeliveryStatus.choices, default=MFAChallengeDeliveryStatus.PENDING
    )
    delivered_at = models.DateTimeField(_("entregue em"), blank=True, null=True)
    consumed_at = models.DateTimeField(_("consumido em"), blank=True, null=True)
    created_at = models.DateTimeField(_("criado em"), auto_now_add=True)

    class Meta:
        db_table = "mfa_challenge"
        indexes = [models.Index(fields=("user", "expires_at"), name="mfa_challenge_user_expiry")]

    @property
    def is_expired(self):
        return self.expires_at <= timezone.now()

    @property
    def is_consumed(self):
        return self.consumed_at is not None


class MFARecoveryCode(models.Model):
    """Código de recuperação armazenado somente como hash lento e salgado."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mfa_recovery_codes", verbose_name=_("usuário"))
    digest = models.CharField(_("digest"), max_length=128)
    created_at = models.DateTimeField(_("criado em"), auto_now_add=True)
    consumed_at = models.DateTimeField(_("consumido em"), blank=True, null=True)

    class Meta:
        db_table = "mfa_recovery_code"
        constraints = [models.CheckConstraint(condition=~models.Q(digest=""), name="mfa_recovery_code_requires_digest")]


class TrustedDevice(models.Model):
    """Dispositivo confiável identificado por uma credencial rotacionável."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="trusted_devices", verbose_name=_("usuário"))
    digest = models.CharField(_("digest"), max_length=128, unique=True)
    name = models.CharField(_("nome"), max_length=255, blank=True)
    user_agent = models.TextField(_("user agent"), blank=True)
    ip_address = models.GenericIPAddressField(_("IP"), blank=True, null=True)
    created_at = models.DateTimeField(_("criado em"), auto_now_add=True)
    expires_at = models.DateTimeField(_("expira em"), db_index=True)
    last_used_at = models.DateTimeField(_("usado por último em"), blank=True, null=True)
    revoked_at = models.DateTimeField(_("revogado em"), blank=True, null=True)

    class Meta:
        db_table = "trusted_device"
        constraints = [models.CheckConstraint(condition=~models.Q(digest=""), name="trusted_device_requires_digest")]
        indexes = [models.Index(fields=("user", "expires_at"), name="trusted_device_user_expiry")]


class MFAResetAudit(models.Model):
    """Registro não sensível de um reset administrativo de MFA."""

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="mfa_resets_performed")
    target = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mfa_resets_received")
    reason = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "mfa_reset_audit"


register(TokenMetaData)
register(MFAFactor)
register(MFAChallenge, exclude_fields=["otp_digest"])
register(MFARecoveryCode, exclude_fields=["digest"])
register(TrustedDevice, exclude_fields=["digest"])
register(MFAResetAudit)
