"""Multi-tenancy: organização, times, vínculos e convites.

A `Organizacao` é a raiz do isolamento (o tenant). Os modelos deste app são o
*control plane* da tenancy e por isso herdam de `BaseGlobal`, **sem** RLS: o
middleware precisa consultá-los para descobrir e validar o tenant antes de
existir qualquer contexto. Os modelos de negócio herdam de `Base`, esses sim
isolados por RLS no banco.
"""
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.api.base.models import BaseGlobal


def gerar_token_convite():
    return secrets.token_urlsafe(48)


def expiracao_padrao_convite():
    return timezone.now() + timedelta(days=7)


class Papel(models.IntegerChoices):
    """Níveis de acesso dentro de uma organização, em ordem crescente.

    A comparação é numérica (`papel >= Papel.GESTOR`), então novos níveis devem
    ser inseridos com folga entre os valores existentes.
    """

    VISUALIZADOR = 10, _("Visualizador")
    MEMBRO = 20, _("Membro")
    GESTOR = 30, _("Gestor")
    ADMINISTRADOR = 40, _("Administrador")
    PROPRIETARIO = 50, _("Proprietário")


class Organizacao(BaseGlobal):
    owner = None

    nome = models.CharField(_("nome"), max_length=150)
    slug = models.SlugField(_("slug"), max_length=60, unique=True)

    def __str__(self):
        return self.nome

    class Meta:
        db_table = "organizacao"
        ordering = ["nome"]
        verbose_name = _("Organização")
        verbose_name_plural = _("Organizações")


class Time(BaseGlobal):
    organizacao = models.ForeignKey(
        Organizacao,
        verbose_name=_("organização"),
        on_delete=models.CASCADE,
        related_name="times",
    )
    nome = models.CharField(_("nome"), max_length=100)

    def __str__(self):
        return self.nome

    class Meta:
        db_table = "time"
        ordering = ["nome"]
        constraints = [
            models.UniqueConstraint(fields=["organizacao", "nome"], name="time_unico_por_organizacao"),
        ]
        verbose_name = _("Time")
        verbose_name_plural = _("Times")


class Vinculo(BaseGlobal):
    """Liga um usuário a uma organização com um papel.

    É o modelo consultado para validar o header `X-Organization`, portanto não
    pode ser protegido por RLS (seria um impasse: precisaria do contexto que
    ele próprio define).
    """

    owner = None

    organizacao = models.ForeignKey(
        Organizacao,
        verbose_name=_("organização"),
        on_delete=models.CASCADE,
        related_name="vinculos",
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("usuário"),
        on_delete=models.CASCADE,
        related_name="vinculos",
    )
    papel = models.PositiveSmallIntegerField(_("papel"), choices=Papel.choices, default=Papel.MEMBRO)
    times = models.ManyToManyField(Time, verbose_name=_("times"), blank=True, related_name="vinculos")

    def tem_papel_minimo(self, papel):
        """Informa se este vínculo alcança o nível `papel`."""
        return self.papel >= papel

    def __str__(self):
        return f"{self.usuario} @ {self.organizacao}"

    class Meta:
        db_table = "vinculo"
        ordering = ["-papel"]
        constraints = [
            models.UniqueConstraint(fields=["organizacao", "usuario"], name="vinculo_unico_por_organizacao"),
        ]
        verbose_name = _("Vínculo")
        verbose_name_plural = _("Vínculos")


class Convite(BaseGlobal):
    """Convite para um e-mail entrar numa organização com um papel."""

    owner = None

    organizacao = models.ForeignKey(
        Organizacao,
        verbose_name=_("organização"),
        on_delete=models.CASCADE,
        related_name="convites",
    )
    email = models.EmailField(_("e-mail"))
    papel = models.PositiveSmallIntegerField(_("papel"), choices=Papel.choices, default=Papel.MEMBRO)
    token = models.CharField(_("token"), max_length=100, unique=True, default=gerar_token_convite, editable=False)
    expira_em = models.DateTimeField(_("expira em"), default=expiracao_padrao_convite)
    convidado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("convidado por"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="convites_enviados",
    )
    aceito_em = models.DateTimeField(_("aceito em"), null=True, blank=True)

    @property
    def expirado(self):
        return timezone.now() >= self.expira_em

    @property
    def pendente(self):
        return self.aceito_em is None and not self.expirado

    def aceitar(self, usuario):
        """Efetiva o convite, transformando-o em um `Vinculo`.

        É idempotente por (organização, usuário): se o vínculo já existir, o
        papel só é elevado quando o convite concede um nível maior — aceitar um
        convite nunca rebaixa alguém.

        Args:
            usuario: Usuário que está aceitando o convite.

        Returns:
            O `Vinculo` criado ou atualizado.

        Raises:
            ValidationError: Se o convite estiver expirado ou já utilizado.
        """
        if not self.pendente:
            raise ValidationError(_("Convite expirado ou já utilizado."))

        with transaction.atomic():
            vinculo, criado = Vinculo.objects.get_or_create(
                organizacao=self.organizacao,
                usuario=usuario,
                defaults={"papel": self.papel},
            )

            if not criado and vinculo.papel < self.papel:
                vinculo.papel = self.papel
                vinculo.save(update_fields=["papel"])

            self.aceito_em = timezone.now()
            self.save(update_fields=["aceito_em"])

        return vinculo

    def __str__(self):
        return f"{self.email} @ {self.organizacao}"

    class Meta:
        db_table = "convite"
        ordering = ["-id"]
        verbose_name = _("Convite")
        verbose_name_plural = _("Convites")
