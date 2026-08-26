import copy

from django.conf import settings
from django.contrib.contenttypes.fields import GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils.translation import gettext_lazy as _

from auditlog.models import AuditlogHistoryField
from django_rls.models import RLSModel, RLSQuerySet
from django_rls.policies import TenantPolicy

from apps.api.core.context import request_atual, usuario_atual


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


class SoftDeleteQuerySet(models.QuerySet):
    def delete(self):
        queryset = self.filter(is_deleted=False)
        deleted_count = queryset.update(is_deleted=True)
        return deleted_count, {self.model._meta.label: deleted_count}


class BaseQuerySet(SoftDeleteQuerySet, RLSQuerySet):
    pass


class DeferredFieldsManagerMixin:
    def get_queryset(self):
        queryset = super().get_queryset()
        fields = self.model.get_queryset_deferred_fields()
        return queryset.defer(*fields) if fields else queryset


class ExcludeDeletedManagerMixin:
    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class ActiveManagerMixin:
    def get_queryset(self):
        return super().get_queryset().filter(is_active=True)


BaseQuerySetManager = models.Manager.from_queryset(BaseQuerySet)


class ObjectsManager(ExcludeDeletedManagerMixin, DeferredFieldsManagerMixin, BaseQuerySetManager):
    pass


class AllObjectsManager(DeferredFieldsManagerMixin, BaseQuerySetManager):
    pass


class ActiveObjectsManager(ActiveManagerMixin, ExcludeDeletedManagerMixin, DeferredFieldsManagerMixin, BaseQuerySetManager):
    pass


class CreationTimestampMixin(models.Model):
    created_at = models.DateTimeField(_("criado em"), auto_now_add=True)
    last_modified_at = models.DateTimeField(_("última alteração em"), auto_now=True)

    class Meta:
        abstract = True


class CreatedByMixin(models.Model):
    created_by = models.ForeignKey(
        to=settings.AUTH_USER_MODEL,
        verbose_name=_("criado por"),
        on_delete=models.PROTECT,
        related_name="+",
        blank=True,
        null=True,
    )

    def save(self, *args, **kwargs):
        if self._state.adding and self.created_by_id is None:
            current_user = usuario_atual.get()
            if current_user is None:
                request = request_atual.get()
                current_user = getattr(request, "user", None)
            if current_user and current_user.is_authenticated:
                self.created_by = current_user

        return super().save(*args, **kwargs)

    class Meta:
        abstract = True


class CreationAuditMixin(CreatedByMixin, CreationTimestampMixin):
    """Compatibilidade para modelos que usam apenas autoria e timestamps."""

    class Meta:
        abstract = True


class AuditHistoryMixin(models.Model):
    history = AuditlogHistoryField()

    class Meta:
        abstract = True


class ActivityMixin(models.Model):
    is_active = models.BooleanField(_("ativo"), default=True)

    class Meta:
        abstract = True


class SoftDeleteMixin(models.Model):
    is_deleted = models.BooleanField(_("excluído"), default=False)

    def delete(self, using=None, keep_parents=False):
        self.is_deleted = True
        self.save(using=using, update_fields=["is_deleted"])

    class Meta:
        abstract = True


class FieldIntrospectionMixin(models.Model):
    def as_dict(self, additional_exclude_fields=None, ignore_excluded_fields=None):
        excluded = set(self.get_excluded_fields())

        if additional_exclude_fields:
            excluded.update(additional_exclude_fields)

        if ignore_excluded_fields:
            excluded.difference_update(ignore_excluded_fields)

        return {field.name: getattr(self, field.name) for field in self._meta.get_fields() if field.concrete and field.name not in excluded}

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
    def get_relational_fields(cls):
        return [field.name for field in cls._meta.get_fields() if field.concrete and field.is_relation]

    def __int__(self):
        return self.pk

    class Meta:
        abstract = True


class FieldPolicyMixin(models.Model):
    read_only_fields = ["is_active", "is_deleted", "created_at", "created_by"]
    extra_read_only_fields = []

    write_only_fields = []
    extra_write_only_fields = []

    internal_fields = ["last_modified_at", "is_deleted"]
    extra_internal_fields = []

    queryset_deferred_fields = []

    forbidden_internal_write_fields = ["created_at", "created_by", "last_modified_at", "organizacao"]
    extra_forbidden_internal_write_fields = []

    @classmethod
    def get_internal_fields(cls):
        return cls.internal_fields + cls.extra_internal_fields

    @classmethod
    def get_read_only_fields(cls):
        return cls.read_only_fields + cls.extra_read_only_fields

    @classmethod
    def get_write_only_fields(cls):
        return cls.write_only_fields + cls.extra_write_only_fields

    @classmethod
    def get_queryset_deferred_fields(cls):
        return cls.queryset_deferred_fields

    @classmethod
    def get_forbidden_internal_write_fields(cls):
        return cls.forbidden_internal_write_fields + cls.extra_forbidden_internal_write_fields

    class Meta:
        abstract = True


class CloneMixin(models.Model):
    clone_reset_fields = ["created_at", "last_modified_at"]
    extra_clone_reset_fields = []

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

        clone.created_by = usuario_atual.get()
        clone.modify_before_cloning()

        if commit:
            clone.save()

        return clone

    def modify_before_cloning(self):
        pass

    class Meta:
        abstract = True


class CapabilityMixin(models.Model):
    @property
    def is_clonable(self):
        return True

    @property
    def is_deletable(self):
        return True

    @property
    def is_editable(self):
        return True

    @property
    def is_viewable(self):
        return True

    class Meta:
        abstract = True


class ApiScopeMixin(models.Model):
    # Interface pública e estável de scopes/permissions (`resource:action`).
    # `None` significa que o model não é exposto pelo registry de scopes.
    api_scope_resource = None

    class Meta:
        abstract = True


class MetadataMixin(models.Model):
    """Acesso de leitura ao metadata genérico do objeto.

    Escrita é responsabilidade de `apps.api.metadata.handlers.aplicar_metadata`,
    o único ponto autorizado a criar linha de `Metadata`.
    """

    metadata_registros = GenericRelation(
        "metadata.Metadata",
        content_type_field="content_type",
        object_id_field="object_id",
        related_query_name="%(app_label)s_%(class)s",
    )

    @classmethod
    def get_content_type(cls):
        """`ContentType` deste model, resolvido pelo cache do Django."""
        return ContentType.objects.get_for_model(cls)

    @property
    def raw_metadata(self):
        """Documento de metadata do objeto, ou `{}` quando não houver.

        Leitura pura: não cria registro. Como `Metadata` está sob RLS, exige
        contexto de organização, igual a qualquer leitura de model de negócio.

        Não troque por `.first()`: ele acrescenta `ORDER BY` e `LIMIT`, força
        uma query nova e ignora o cache do `prefetch_related`, trazendo o N+1
        de volta. A constraint garante no máximo um registro vivo por
        organização e objeto, então materializar a lista é seguro.
        """
        registros = list(self.metadata_registros.all())
        return registros[0].dados if registros else {}

    class Meta:
        abstract = True


class ChangeTrackingMixin(models.Model):
    _original_field_values: dict[str, object]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._original_field_values = {}
        self._reset_original_field_values()

    def _loaded_concrete_fields(self):
        deferred = self.get_deferred_fields()
        return [field for field in self._meta.concrete_fields if field.attname not in deferred]

    def _reset_original_field_values(self, fields=None):
        loaded_fields = self._loaded_concrete_fields()
        if fields is None:
            self._original_field_values = {field.name: copy.deepcopy(getattr(self, field.attname)) for field in loaded_fields}
            return

        field_names = set(fields)
        for field in loaded_fields:
            if field.name in field_names or field.attname in field_names:
                self._original_field_values[field.name] = copy.deepcopy(getattr(self, field.attname))

    def _changed_loaded_field_names(self):
        return [
            field.name
            for field in self._loaded_concrete_fields()
            if field.name not in self._original_field_values or self._original_field_values[field.name] != getattr(self, field.attname)
        ]

    def save(self, *args, **kwargs):
        if self._state.adding:
            result = super().save(*args, **kwargs)
            self._reset_original_field_values()
            return result

        explicit_update_fields = kwargs.get("update_fields")
        if explicit_update_fields is None:
            update_fields = self._changed_loaded_field_names()
            if not update_fields:
                return None

            auto_now_fields = [
                field.name for field in self._meta.concrete_fields if getattr(field, "auto_now", False) and field.name not in update_fields
            ]
            kwargs["update_fields"] = [*update_fields, *auto_now_fields]
        else:
            explicit_update_fields = tuple(explicit_update_fields)
            kwargs["update_fields"] = explicit_update_fields

        result = super().save(*args, **kwargs)
        self._reset_original_field_values(explicit_update_fields)
        return result

    def refresh_from_db(self, using=None, fields=None, from_queryset=None):
        refreshed_fields = tuple(fields) if fields is not None else None
        result = super().refresh_from_db(using=using, fields=refreshed_fields, from_queryset=from_queryset)
        self._reset_original_field_values(refreshed_fields)
        return result

    class Meta:
        abstract = True


class BaseTenantless(
    MetadataMixin,
    ApiScopeMixin,
    CapabilityMixin,
    CloneMixin,
    FieldPolicyMixin,
    FieldIntrospectionMixin,
    SoftDeleteMixin,
    ActivityMixin,
    AuditHistoryMixin,
    ChangeTrackingMixin,
    CreatedByMixin,
    CreationTimestampMixin,
):
    """Base para modelos sem isolamento por organização."""

    objects = ObjectsManager()
    all_objects = AllObjectsManager()
    ativos = ActiveObjectsManager()

    class Meta:
        abstract = True


class TenantMixin(models.Model):
    organizacao = models.ForeignKey(
        "organizacoes.Organizacao",
        verbose_name=_("organização"),
        on_delete=models.PROTECT,
        related_name="+",
    )

    def save(self, *args, **kwargs):
        if self._state.adding and self.organizacao_id is None:
            from django_rls.context import get_active_rls_context

            organizacao_id = get_active_rls_context().get("tenant_id")
            if organizacao_id is not None:
                field = self._meta.get_field("organizacao")
                self.organizacao_id = field.target_field.to_python(organizacao_id)

        return super().save(*args, **kwargs)

    class Meta:
        abstract = True


class Base(TenantMixin, BaseTenantless, RLSModel):
    """Base padrão para modelos de negócio isolados por organização."""

    class Meta:
        abstract = True
        rls_policies = [TenantPolicy(name="isolamento_organizacao", tenant_field="organizacao")]
