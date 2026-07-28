import copy

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils.translation import gettext_lazy as _

from auditlog.models import AuditlogHistoryField
from django_rls.models import RLSModel, RLSQuerySet
from django_rls.policies import TenantPolicy
from threadlocals.threadlocals import get_current_user


class Estados(models.IntegerChoices):
    EM_BRANCO = 0, "Em branco"
    RONDONIA = 1, "Rondônia"
    ACRE = 2, "Acre"
    AMAZONAS = 3, "Amazonas"
    RORAIMA = 4, "Roraima"
    PARA = 5, "Pará"
    AMAPA = 6, "Amapá"
    TOCANTINS = 7, "Tocantins"
    MARANHAO = 8, "Maranhão"
    PIAUI = 9, "Piauí"
    CEARA = 10, "Ceará"
    RIO_GRANDE_DO_NORTE = 11, "Rio Grande do Norte"
    PARAIBA = 12, "Paraíba"
    PERNAMBUCO = 13, "Pernambuco"
    ALAGOAS = 14, "Alagoas"
    SERGIPE = 15, "Sergipe"
    BAHIA = 16, "Bahia"
    MINAS_GERAIS = 17, "Minas Gerais"
    ESPIRITO_SANTO = 18, "Espírito Santo"
    RIO_DE_JANEIRO = 19, "Rio de Janeiro"
    SAO_PAULO = 20, "São Paulo"
    PARANA = 21, "Paraná"
    SANTA_CATARINA = 22, "Santa Catarina"
    RIO_GRANDE_DO_SUL = 23, "Rio Grande do Sul"
    MATO_GROSSO_DO_SUL = 24, "Mato Grosso do Sul"
    MATO_GROSSO = 25, "Mato Grosso"
    GOIAS = 26, "Goiás"
    DISTRITO_FEDERAL = 27, "Distrito Federal"
    EXTERIOR = 28, "Exterior"


class BaseQuerySet(RLSQuerySet):
    """QuerySet com o guard de contexto do django-rls.

    Sem isso o `REQUIRE_CONTEXT` não tem efeito: o manager do `BaseGlobal` vence
    o `RLSManager` no MRO, e consultar um modelo isolado fora de um contexto de
    organização passaria batido — devolvendo silenciosamente zero linhas (ou
    todas, se a conexão for de um superusuário, que ignora RLS).

    O guard só atua em modelos que têm policies; os que herdam apenas de
    `BaseGlobal` não são afetados.
    """


class CustomManager(models.Manager.from_queryset(BaseQuerySet)):
    def get_queryset(self):
        queryset = super().get_queryset()
        deferred_fields = self.model.get_queryset_deferred_fields()
        if deferred_fields:
            queryset = queryset.defer(*deferred_fields)
        return queryset


class AtivosManager(models.Manager.from_queryset(BaseQuerySet)):
    def get_queryset(self):
        return super().get_queryset().filter(ativo=True)


class CreationAuditMixin(models.Model):
    """Autoria e timestamps comuns a todo modelo de negócio.

    Não existe `last_modified_by`: só a criação é atribuída a um usuário: quem
    fez a última alteração vive no `AuditlogHistoryField`, não num FK aqui.
    """

    created_by = models.ForeignKey(
        verbose_name=_("criado por"),
        to=settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="+",
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(_("criado em"), auto_now_add=True)
    last_modified_at = models.DateTimeField(_("última alteração em"), auto_now=True)

    class Meta:
        abstract = True


class BaseGlobal(CreationAuditMixin):
    """Campos comuns **sem** isolamento por organização.

    Use somente nos modelos de identidade/bootstrap, que precisam ser lidos
    antes de existir contexto de tenant: `Organizacao`, `Vinculo`, `Convite` e
    `Usuario`. Todo o resto deve herdar de `Base`.
    """

    ativo = models.BooleanField(_("ativo"), default=True)

    objects = CustomManager()
    ativos = AtivosManager()

    history = AuditlogHistoryField()

    internal_fields = [
        "last_modified_at",
    ]
    extra_internal_fields = []

    read_only_fields = [
        "ativo",
        "created_at",
        "created_by",
    ]
    extra_read_only_fields = []

    queryset_deferred_fields = []

    clone_reset_fields = ("created_at", "last_modified_at")
    extra_clone_reset_fields = []

    def save(self, *args, **kwargs):
        # setando o created_by automaticamente
        model_fields = self.get_fields()
        if "created_by" in model_fields:
            if self.pk is None and self.created_by is None:
                current_user = get_current_user()
                if current_user and current_user.is_authenticated:
                    self.created_by = current_user

        return super().save(*args, **kwargs)

    def as_dict(self, additional_exclude_fields=None, ignore_excluded_fields=None):
        excluded = set(self.get_excluded_fields())

        if additional_exclude_fields:
            excluded.update(additional_exclude_fields)

        if ignore_excluded_fields:
            excluded.difference_update(ignore_excluded_fields)

        return {field.name: getattr(self, field.name) for field in self._meta.get_fields() if field.concrete and field.name not in excluded}

    def clonar(self, commit=True, **fields):
        clone = copy.copy(self)
        clone.pk = None
        clone._state.adding = True

        model_fields = self.get_fields()
        control_fields = list(self.clone_reset_fields) + list(self.extra_clone_reset_fields)
        for field in control_fields:
            if field in model_fields:
                setattr(clone, field, None)

        for chave, valor in fields.items():
            setattr(clone, chave, valor)

        clone.created_by = get_current_user()

        clone.modify_before_cloning()

        if commit:
            clone.save()

        return clone

    def modify_before_cloning(self):
        pass

    @classmethod
    def get_fields(cls):
        return [field.name for field in cls._meta.get_fields() if field.concrete]

    @classmethod
    def get_serializable_column_names(cls):
        fields = cls.get_fields()
        excluded_fields = cls.get_internal_fields()
        return [field for field in fields if field not in excluded_fields]

    @classmethod
    def get_excluded_fields(cls):
        return cls.get_internal_fields()

    @classmethod
    def get_internal_fields(cls):
        return cls.internal_fields + cls.extra_internal_fields

    @classmethod
    def get_read_only_fields(cls):
        return cls.read_only_fields + cls.extra_read_only_fields

    @classmethod
    def get_queryset_deferred_fields(cls):
        return cls.queryset_deferred_fields

    @classmethod
    def get_relational_fields(cls):
        return [field.name for field in cls._meta.get_fields() if field.concrete and field.is_relation]

    @classmethod
    def get_content_type(cls):
        return ContentType.objects.get_for_model(cls)

    def __int__(self):
        return self.pk

    class Meta:
        abstract = True


class Base(BaseGlobal, RLSModel):
    """Base padrão: todo modelo de negócio é isolado por organização.

    O FK `organizacao` e a policy de RLS são herdados por toda subclasse
    concreta — o metaclass do django-rls propaga `rls_policies`. Multi-tenancy é
    o **default**: esquecer de configurar algo resulta em ficar protegido, não
    em vazar dados entre clientes.
    """

    organizacao = models.ForeignKey(
        "organizacoes.Organizacao",
        verbose_name=_("organização"),
        on_delete=models.PROTECT,
        related_name="+",
    )

    class Meta:
        abstract = True
        rls_policies = [TenantPolicy(name="isolamento_organizacao", tenant_field="organizacao")]
