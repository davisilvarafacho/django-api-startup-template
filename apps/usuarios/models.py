from django.contrib.auth.models import AbstractUser, UserManager
from django.db import DEFAULT_DB_ALIAS, models, transaction
from django.db.models.functions import Lower
from django.utils.translation import gettext_lazy as _

from apps.api.base.models import ActiveManagerMixin, BaseQuerySet, BaseTenantless, DeferredFieldsManagerMixin, ExcludeDeletedManagerMixin
from apps.usuarios import passwords
from internal_frameworks.sensitive_fields.fields import encrypt
from utils.logs import register


def revogar_acesso_da_conta(user_id, database_alias, *, actor=None):
    """Protocolo canônico de exclusão de uma conta, dentro de uma transação.

    É o helper compartilhado por `Usuario.delete()` e por
    `UsuarioQuerySet.delete()`: existe um único lugar que sabe o que "excluir
    uma conta" significa. Bloqueia a linha, marca exclusão e inatividade juntas
    e revoga o que ainda autenticava a pessoa, sempre na ordem global de locks
    (`Usuario` → `AuthToken` → `TrustedDevice`).

    Idempotente: uma conta já excluída não é tocada de novo, e as revogações já
    persistidas ficam com a data original.

    Args:
        user_id: Chave primária da conta.
        database_alias: Alias do banco a usar em toda a operação.
        actor: Quem excluiu, ou `None` quando não há autor a registrar.

    Returns:
        A conta já marcada como excluída, ou `None` se ela já estivesse.
    """
    # Imports locais: `autenticacao` importa `usuarios` no carregamento dos
    # models, e `mfa`/`services` são resolvidos aqui como módulo (e não como
    # nome) para que a exclusão sempre chame a função vigente.
    from apps.api.autenticacao import mfa, services

    conta = Usuario.all_objects.using(database_alias).select_for_update().get(pk=user_id)
    if conta.is_deleted:
        return None

    conta.is_deleted = True
    conta.is_active = False
    conta.save(using=database_alias, update_fields=["is_deleted", "is_active"])

    services.revoke_all_user_credentials(conta, actor=actor, using=database_alias)
    mfa.revoke_trusted_devices(conta, using=database_alias)
    return conta


class UsuarioQuerySet(BaseQuerySet):
    """Queryset de contas: a exclusão em lote reusa o protocolo de conta a conta.

    Até `update(is_deleted=True)` é redirecionado ao protocolo canônico para
    que nenhuma API comum do ORM contorne a revogação de credenciais.
    """

    def delete(self):
        """Exclui todas as contas vivas do queryset, tudo ou nada.

        Returns:
            O par `(total, {label: total})` da convenção de `QuerySet.delete()`.
        """
        database_alias = self.db
        # Ordenar por PK dá uma ordem de aquisição estável entre lotes
        # concorrentes, que é o que impede duas exclusões em massa de travarem
        # uma na outra.
        user_ids = sorted(self.filter(is_deleted=False).values_list("pk", flat=True))

        with transaction.atomic(using=database_alias):
            excluidas = [user_id for user_id in user_ids if revogar_acesso_da_conta(user_id, database_alias) is not None]

        total = len(excluidas)
        return total, {self.model._meta.label: total} if total else {}

    def update(self, **kwargs):
        """Protege a exclusão e invalida as demais mutações de autorização."""
        if kwargs.get("is_deleted") is True:
            unsupported = set(kwargs) - {"is_deleted", "is_active"}
            if unsupported or kwargs.get("is_active", False) not in {False, None}:
                raise ValueError("A exclusão em lote de Usuario só aceita is_deleted=True e is_active=False.")
            return self.delete()[0]

        if "email" in kwargs:
            kwargs["email_verificado_em"] = None

        auth_fields = {"is_active", "is_superuser", "is_deleted"}
        affected_user_ids = tuple(sorted(self.values_list("pk", flat=True))) if auth_fields.intersection(kwargs) else ()
        updated = super().update(**kwargs)
        if affected_user_ids:
            from internal_frameworks.permission_cache.invalidation import schedule_epoch_bumps
            from internal_frameworks.permission_cache.keys import user_scope

            for layer in ("django", "tenant", "guardian"):
                schedule_epoch_bumps(
                    tuple(user_scope(layer, user_id) for user_id in affected_user_ids),
                    database_alias=self.db,
                    layer=layer,
                )
        return updated


class UsuarioManager(UserManager.from_queryset(UsuarioQuerySet)):
    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)

    def _create_user(self, email, password, *, validate=True, **extra_fields):
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.password = passwords.build_password(user, password, validate=validate)
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

        # O superusuário é o único caminho que pula a política de senha: o
        # bootstrap de um ambiente não pode depender do HaveIBeenPwned estar no ar.
        return self._create_user(email, password, validate=False, **extra_fields)


UsuarioQuerySetManager = models.Manager.from_queryset(UsuarioQuerySet)


class TodasAsContasManager(DeferredFieldsManagerMixin, UsuarioQuerySetManager):
    """`all_objects` de `Usuario`, com o mesmo protocolo de exclusão."""


class ContasAtivasManager(ActiveManagerMixin, ExcludeDeletedManagerMixin, DeferredFieldsManagerMixin, UsuarioQuerySetManager):
    """`ativos` de `Usuario`, com o mesmo protocolo de exclusão."""


class Usuario(BaseTenantless, AbstractUser):
    username = None
    api_scope_resource = "users"

    extra_write_only_fields = ["password"]

    first_name = models.CharField(_("nome"), max_length=30)
    last_name = models.CharField(_("sobrenome"), max_length=40)
    # sem `unique=True`: a unicidade é parcial (`usuario_email_unico_nao_excluido`,
    # em `Meta.constraints`) para que o e-mail volte a ficar livre após a exclusão
    # lógica. Um índice único incondicional aqui quebra esse reuso.
    email = models.EmailField(_("email"), help_text=_("email do usuário"), db_comment="email do usuário")
    phone_number = encrypt(
        models.CharField(
            _("telefone"),
            max_length=16,
            blank=True,
            null=True,
            default=None,
            help_text=_("Número de telefone E.164 cifrado."),
            db_comment="Número de telefone E.164 cifrado.",
        )
    )
    phone_verified_at = models.DateTimeField(
        _("telefone verificado em"),
        blank=True,
        null=True,
        help_text=_("Data e hora da confirmação do telefone para MFA."),
        db_comment="Data e hora da confirmação do telefone para MFA.",
    )
    email_verificado_em = models.DateTimeField(
        _("e-mail verificado em"),
        blank=True,
        null=True,
        help_text=_("Data e hora da confirmação do e-mail."),
        db_comment="Data e hora da confirmação do e-mail.",
    )
    exclusao_solicitada_em = models.DateTimeField(
        _("exclusão solicitada em"),
        blank=True,
        null=True,
        help_text=_("Data e hora em que a exclusão da conta foi solicitada."),
        db_comment="Data e hora em que a exclusão da conta foi solicitada.",
    )
    exclusao_agendada_para = models.DateTimeField(
        _("exclusão agendada para"),
        blank=True,
        null=True,
        help_text=_("Data e hora programada para a anonimização definitiva da conta."),
        db_comment="Data e hora programada para a anonimização definitiva da conta.",
    )

    EMAIL_FIELD = "email"
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["first_name", "last_name"]

    objects = UsuarioManager()
    # Redeclarados para que os três managers compartilhem o `UsuarioQuerySet`:
    # os de `BaseTenantless` fariam `update(is_deleted=True)` e contornariam a
    # revogação. A ordem de declaração preserva `objects` como manager padrão.
    all_objects = TodasAsContasManager()
    ativos = ContasAtivasManager()

    def _pode_autorizar(self) -> bool:
        """Barreira comum anterior ao atalho de superuser e aos backends."""
        return self.is_active and not self.is_deleted

    def has_perm(self, perm, obj=None):
        if not self._pode_autorizar():
            return False
        return super().has_perm(perm, obj)

    async def ahas_perm(self, perm, obj=None):
        if not self._pode_autorizar():
            return False
        return await super().ahas_perm(perm, obj)

    def has_perms(self, perm_list, obj=None):
        if not self._pode_autorizar():
            return False
        return super().has_perms(perm_list, obj)

    async def ahas_perms(self, perm_list, obj=None):
        if not self._pode_autorizar():
            return False
        return await super().ahas_perms(perm_list, obj)

    def has_module_perms(self, app_label):
        if not self._pode_autorizar():
            return False
        return super().has_module_perms(app_label)

    async def ahas_module_perms(self, app_label):
        if not self._pode_autorizar():
            return False
        return await super().ahas_module_perms(app_label)

    def save(self, *args, **kwargs):
        database_alias = kwargs.get("using") or self._state.db or DEFAULT_DB_ALIAS
        update_fields = kwargs.get("update_fields")
        tracks_contact = update_fields is None or bool({"email", "phone_number"} & set(update_fields))
        contact_changed = False
        if self.pk and tracks_contact:
            previous = type(self).all_objects.using(database_alias).only("email", "phone_number").get(pk=self.pk)
            email_changed = previous.email != self.email
            contact_changed = email_changed or previous.phone_number != self.phone_number
            if email_changed:
                if not getattr(self, "_email_assinado_confirmado", False):
                    self.email_verificado_em = None
                    if update_fields is not None:
                        kwargs["update_fields"] = [*update_fields, "email_verificado_em"]

        result = super().save(*args, **kwargs)
        if contact_changed:
            from apps.api.autenticacao.mfa import revoke_trusted_devices

            transaction.on_commit(
                lambda: revoke_trusted_devices(self, using=database_alias),
                using=database_alias,
            )
        return result

    def confirmar_email_assinado(self, email, *, verified_at):
        """Altera e confirma e-mail no único caminho deliberadamente autorizado.

        A confirmação assinada já provou posse do endereço novo e é chamada
        somente dentro da transação de `Contas.confirmar_troca_email`. Todos os
        demais saves que alteram `email` continuam limpando a confirmação.
        """
        self.email = email
        self.email_verificado_em = verified_at
        self._email_assinado_confirmado = True
        try:
            self.save(update_fields=["email", "email_verificado_em"])
        finally:
            del self._email_assinado_confirmado

    def delete(self, using=None, keep_parents=False):
        """Exclui logicamente a conta e derruba todo acesso que ela ainda tinha.

        Entrada canônica da revogação: marca `is_deleted` e `is_active` juntos,
        revoga credenciais e dispositivos confiáveis e deixa os sinais de
        `post_save` invalidarem os epochs de autorização no commit. Nada é
        apagado — a auditoria continua enxergando tudo.

        Args:
            using: Alias do banco; por padrão, o alias da própria instância.
            keep_parents: Ignorado; não há exclusão física de tabela pai.

        Returns:
            O par `(total, {label: total})` da convenção de `Model.delete()`,
            com `(0, {})` quando a conta já estava excluída.
        """
        del keep_parents
        database_alias = using or self._state.db or DEFAULT_DB_ALIAS

        with transaction.atomic(using=database_alias):
            conta = revogar_acesso_da_conta(self.pk, database_alias)

        if conta is None:
            # A instância pode estar obsoleta enquanto a linha no banco já foi
            # excluída por outro worker; mantenha também o objeto em memória coerente.
            self.is_deleted = True
            self.is_active = False
            return 0, {}

        self.is_deleted = True
        self.is_active = False
        return 1, {self._meta.label: 1}

    def __str__(self) -> str:
        return self.get_full_name()

    class Meta:
        base_manager_name = "all_objects"
        db_table = "usuario"
        ordering = ["-id"]
        verbose_name = _("Usuário")
        verbose_name_plural = _("Usuários")
        permissions = [("can_reset_mfa_usuario", "Pode resetar MFA de usuários")]
        constraints = [
            models.UniqueConstraint(
                Lower("email"),
                condition=models.Q(is_deleted=False),
                name="usuario_email_unico_nao_excluido",
            )
        ]


register(
    Usuario,
    exclude_fields=["password", "last_login", "email"],
)
