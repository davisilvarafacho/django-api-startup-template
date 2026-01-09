from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models
from django.utils.translation import gettext_lazy as _

from auditlog.registry import auditlog

from apps.api.base.models import Base


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


class Usuario(Base, AbstractUser):
    username = None
    owner = None

    first_name = models.CharField(_("nome"), max_length=30)
    last_name = models.CharField(_("sobrenome"), max_length=40)
    email = models.EmailField(_("email"), unique=True)

    EMAIL_FIELD = "email"
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["first_name", "last_name"]

    objects = UsuarioManager()

    def __str__(self) -> str:
        return self.get_full_name()

    class Meta:
        db_table = "usuario"
        ordering = ["-id"]
        verbose_name = _("Usuário")
        verbose_name_plural = _("Usuários")


exclude_fields = settings.BASE_AUDITLOG_EXCLUDE_FIELDS.extend(["password", "last_login"])
auditlog.register(Usuario, exclude_fields=exclude_fields)
