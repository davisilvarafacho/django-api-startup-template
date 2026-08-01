from django.contrib import admin, messages
from django.db import transaction
from django.utils.translation import gettext_lazy as _

# from unfold.admin import ModelAdmin

ADMIN_PAGE_SIZE = 50


class BaseModelAdmin(admin.ModelAdmin):
    list_per_page = ADMIN_PAGE_SIZE
    readonly_fields = ("created_at", "last_modified_at", "created_by")
    actions = ("clone_records", "ativar_registros", "inativar_registros")

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not self._model_has_is_active_field():
            actions.pop("ativar_registros", None)
            actions.pop("inativar_registros", None)
        return actions

    def _model_has_is_active_field(self):
        return any(field.name == "is_active" for field in self.model._meta.concrete_fields)

    @admin.action(description=_("Clonar registros selecionados"))
    def clone_records(self, request, queryset):
        cloned = 0
        errors = []
        for obj in queryset:
            try:
                with transaction.atomic():
                    self._clone_instance(obj)
                cloned += 1
            except Exception as exc:  # noqa: BLE001
                errors.append((obj, exc))
        if cloned:
            self.message_user(
                request,
                _("%(count)d registro(s) clonados com sucesso.") % {"count": cloned},
                level=messages.SUCCESS,
            )
        for obj, exc in errors:
            self.message_user(
                request,
                _('Não foi possível clonar "%(obj)s": %(erro)s') % {"obj": obj, "erro": exc},
                level=messages.ERROR,
            )

    def _clone_instance(self, obj):
        clone = obj.__class__()
        for field in obj._meta.concrete_fields:
            if field.primary_key or getattr(field, "auto_created", False):
                continue
            if field.is_relation and field.remote_field and field.remote_field.parent_link:
                continue
            setattr(clone, field.attname, getattr(obj, field.attname))
        for attr, value in self.get_clone_field_overrides(obj).items():
            setattr(clone, attr, value)
        self.alter_unique_fields(clone, obj)
        clone.save()
        for field in obj._meta.many_to_many:
            getattr(clone, field.name).set(getattr(obj, field.name).all())
        return clone

    def get_clone_field_overrides(self, obj):
        return {}

    def alter_unique_fields(self, clone, original):
        pass

    @admin.action(description=_("Ativar registros selecionados"))
    def ativar_registros(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(
            request,
            _("%(count)d registro(s) ativado(s) com sucesso.") % {"count": updated},
            level=messages.SUCCESS,
        )

    @admin.action(description=_("Inativar registros selecionados"))
    def inativar_registros(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(
            request,
            _("%(count)d registro(s) inativado(s) com sucesso.") % {"count": updated},
            level=messages.SUCCESS,
        )
