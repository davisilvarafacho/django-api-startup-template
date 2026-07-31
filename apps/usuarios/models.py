from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models, transaction
from django.utils.translation import gettext_lazy as _

from apps.api.base.models import BaseGlobal
from utils.logs import register
from utils.sensitive_fields import encrypt


class UsuarioManager(UserManager):
    def _create_user(self, email, password, **extra_fields):
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.password = make_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self._create_user(email, password, **extra_fields)


class Usuario(BaseGlobal, AbstractUser):
    username = None
    owner = None

    extra_write_only_fields = ["password"]

    first_name = models.CharField(_("nome"), max_length=30)
    last_name = models.CharField(_("sobrenome"), max_length=40)
    email = models.EmailField(_("email"), unique=True)
    phone_number = encrypt(
        models.CharField(
            _("telefone"),
            max_length=16,
            blank=True,
            null=True,
            default=None,
            help_text=_("Número de telefone E.164 cifrado."),
            db_comment=_("Número de telefone E.164 cifrado."),
        )
    )
    phone_verified_at = models.DateTimeField(
        _("telefone verificado em"),
        blank=True,
        null=True,
        help_text=_("Data e hora da confirmação do telefone para MFA."),
        db_comment=_("Data e hora da confirmação do telefone para MFA."),
    )

    EMAIL_FIELD = "email"
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["first_name", "last_name"]

    objects = UsuarioManager()

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        tracks_contact = update_fields is None or bool({"email", "phone_number"} & set(update_fields))
        contact_changed = False
        if self.pk and tracks_contact:
            previous = type(self).objects.only("email", "phone_number").get(pk=self.pk)
            contact_changed = previous.email != self.email or previous.phone_number != self.phone_number

        result = super().save(*args, **kwargs)
        if contact_changed:
            from apps.api.autenticacao.mfa import revoke_trusted_devices

            transaction.on_commit(lambda: revoke_trusted_devices(self))
        return result

    def __str__(self) -> str:
        return self.get_full_name()

    class Meta:
        db_table = "usuario"
        ordering = ["-id"]
        verbose_name = _("Usuário")
        verbose_name_plural = _("Usuários")
        permissions = [("can_reset_mfa_usuario", "Pode resetar MFA de usuários")]


register(
    Usuario,
    exclude_fields=["password", "last_login"],
)
