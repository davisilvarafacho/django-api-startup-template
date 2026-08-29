"""Multi-tenancy: organização, times, vínculos e convites.

A `Organizacao` é a raiz do isolamento (o tenant). Os modelos deste app são o
*control plane* da tenancy e por isso herdam de `BaseTenantless`, **sem** RLS: o
middleware precisa consultá-los para descobrir e validar o tenant antes de
existir qualquer contexto. Os modelos de negócio herdam de `Base`, esses sim
isolados por RLS no banco.
"""

import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.api.base.models import BaseTenantless
from utils.logs import register


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


class Organizacao(BaseTenantless):
    api_scope_resource = "organizations"

    nome = models.CharField(_("nome"), max_length=150)
    slug = models.SlugField(_("slug"), max_length=60)
    email_faturamento = models.EmailField(_("e-mail de faturamento"), null=True, blank=True)
    encerramento_solicitado_em = models.DateTimeField(_("encerramento solicitado em"), null=True, blank=True)
    encerramento_agendado_para = models.DateTimeField(_("encerramento agendado para"), null=True, blank=True)

    def __str__(self):
        return self.nome

    class Meta:
        db_table = "organizacao"
        ordering = ["nome"]
        constraints = [
            models.UniqueConstraint(
                fields=["slug"],
                condition=models.Q(is_deleted=False),
                name="organizacao_slug_unico_nao_excluido",
            ),
            models.CheckConstraint(
                condition=models.Q(encerramento_agendado_para__isnull=True) | models.Q(encerramento_solicitado_em__isnull=False),
                name="organizacao_agendamento_exige_solicitacao",
            ),
        ]
        verbose_name = _("Organização")
        verbose_name_plural = _("Organizações")


class Time(BaseTenantless):
    api_scope_resource = "teams"

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
            models.UniqueConstraint(
                fields=["organizacao", "nome"],
                condition=models.Q(is_deleted=False),
                name="time_unico_por_organizacao_nao_excluido",
            ),
        ]
        verbose_name = _("Time")
        verbose_name_plural = _("Times")


class Vinculo(BaseTenantless):
    """Liga um usuário a uma organização com um papel.

    É o modelo consultado para validar o header `X-Organization`, portanto não
    pode ser protegido por RLS (seria um impasse: precisaria do contexto que
    ele próprio define).
    """

    api_scope_resource = "memberships"

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
            models.UniqueConstraint(
                fields=["organizacao", "usuario"],
                condition=models.Q(is_deleted=False),
                name="vinculo_unico_por_organizacao_nao_excluido",
            ),
        ]
        verbose_name = _("Vínculo")
        verbose_name_plural = _("Vínculos")


class Convite(BaseTenantless):
    """Convite para um e-mail entrar numa organização com um papel."""

    api_scope_resource = "invitations"
    api_scope_custom_actions = {"accept": "can_accept_convite"}

    organizacao = models.ForeignKey(
        Organizacao,
        verbose_name=_("organização"),
        on_delete=models.CASCADE,
        related_name="convites",
    )
    email = models.EmailField(_("e-mail"))
    papel = models.PositiveSmallIntegerField(_("papel"), choices=Papel.choices, default=Papel.MEMBRO)
    token = models.CharField(_("token"), max_length=100, default=gerar_token_convite, editable=False)
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
        return self.is_active and not self.is_deleted and self.aceito_em is None and not self.expirado

    def aceitar(self, usuario):
        """Compatibilidade para consumidores legados do aceite de convite."""
        from apps.organizacoes.memberships import Vinculos

        return Vinculos.aceitar_convite(self, usuario)

    def __str__(self):
        return f"Convite #{self.pk or 'novo'} @ {self.organizacao}"

    class Meta:
        db_table = "convite"
        ordering = ["-id"]
        permissions = [
            ("can_accept_convite", "Pode aceitar convite"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["token"],
                condition=models.Q(is_deleted=False),
                name="convite_token_unico_nao_excluido",
            )
        ]
        verbose_name = _("Convite")
        verbose_name_plural = _("Convites")


register(Organizacao, exclude_fields=["email_faturamento"])
register(Time)
register(Vinculo)
register(Convite, exclude_fields=["email", "token"])
