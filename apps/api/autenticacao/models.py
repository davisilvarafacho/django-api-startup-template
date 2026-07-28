from django.db import models
from django.utils.translation import gettext_lazy as _

from utils.logs import register


class TokenType(models.IntegerChoices):
    TOKEN = 1, _('Token')
    RESET_PASSWORD = 2, _('Reset de senha')
    API_KEY = 999, _('API key')


class TokenMetaData(models.Model):
    """Metadados completos para tokens Knox"""

    # token relacionado
    token = models.OneToOneField(
        verbose_name=_('Token'),
        to="knox.AuthToken",
        on_delete=models.CASCADE,
        related_name='metadata',
        primary_key=True
    )
    type = models.PositiveSmallIntegerField(
        verbose_name=_('Tipo'),
        choices=TokenType.choices,
        default=TokenType.TOKEN,
        db_index=True,
        help_text=_('Tipo de uso do token Knox.'),
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
    scopes = models.JSONField(
        verbose_name=_('Escopos'),
        default=list,
        blank=True,
        help_text=_("Escopos concedidos ao token API key, como 'org:read'."),
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


register(TokenMetaData)
