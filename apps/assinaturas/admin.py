from functools import wraps
from typing import cast

from django.contrib import admin, messages
from django.contrib.admin.helpers import ActionForm
from django.core.exceptions import PermissionDenied
from django.forms import CharField, ChoiceField, PasswordInput, Textarea
from django.shortcuts import redirect

from apps.api.base.admin import BaseModelAdmin
from apps.assinaturas.proposals import ConflitoPropostaComercial, Propostas
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Organizacao

from .models import Plano, PrecoPlano, PropostaComercial, VersaoPlano

CHAVE_SESSAO_ORGANIZACAO_PROPOSTA = "proposal_admin_organizacao_id"
PARAMETRO_ORGANIZACAO = "organizacao__id__exact"


class PropostaComercialActionForm(ActionForm):
    codigo_mfa = CharField(label="Código MFA", required=False, widget=PasswordInput(render_value=False))
    justificativa = CharField(label="Justificativa", required=False, widget=Textarea(attrs={"rows": 3}))


@admin.register(Plano)
class PlanoAdmin(BaseModelAdmin):
    list_display = ("codigo", "nome", "visivel", "is_active")
    list_filter = ("visivel", "is_active", "is_deleted")
    search_fields = ("codigo", "nome")


@admin.register(VersaoPlano)
class VersaoPlanoAdmin(BaseModelAdmin):
    list_display = ("plano", "numero", "atual", "is_active", "publicada_em")
    list_filter = ("atual", "is_active", "is_deleted")
    search_fields = ("plano__codigo", "plano__nome")

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.publicada_em is not None:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PrecoPlano)
class PrecoPlanoAdmin(BaseModelAdmin):
    list_display = ("versao_plano", "periodicidade", "moeda", "valor_base_centavos", "valor_seat_centavos", "is_active")
    list_filter = ("periodicidade", "moeda", "is_active", "is_deleted")
    search_fields = ("versao_plano__plano__codigo", "versao_plano__plano__nome")

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.versao_plano.publicada_em is not None:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PropostaComercial)
class PropostaComercialAdmin(BaseModelAdmin):
    """Consulta segura e ativação nominal; nenhum save genérico é permitido."""

    action_form = PropostaComercialActionForm
    actions = ("ativar_contratual",)  # type: ignore[assignment]
    list_display = ("id", "organizacao", "status", "modo_ativacao", "revisao", "valida_ate")
    list_filter = ("organizacao", "status", "modo_ativacao", "periodicidade")
    search_fields = ("organizacao__nome", "organizacao__slug", "moeda")

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        organizacao_id = request.session.get(CHAVE_SESSAO_ORGANIZACAO_PROPOSTA)
        if organizacao_id is not None:
            queryset = queryset.filter(organizacao_id=organizacao_id)
        return queryset

    def get_urls(self):
        urls = super().get_urls()
        for url in urls:
            url.callback = self._com_organizacao(url.callback)
        return urls

    def _com_organizacao(self, view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            organizacao_id = self._resolver_organizacao(request)
            if organizacao_id is None:
                self.message_user(request, "Cadastre uma organização antes de acessar as propostas.", level=messages.WARNING)
                return redirect("admin:index")

            with organizacao_atual_privilegiada(organizacao_id):
                response = view(request, *args, **kwargs)
                if hasattr(response, "render") and callable(response.render):
                    response.render()
                return response

        return wrapper

    @staticmethod
    def _resolver_organizacao(request):
        do_filtro = request.GET.get(PARAMETRO_ORGANIZACAO, "")
        if do_filtro.isdigit():
            organizacao_id = int(do_filtro)
            request.session[CHAVE_SESSAO_ORGANIZACAO_PROPOSTA] = organizacao_id
            return organizacao_id

        da_sessao = request.session.get(CHAVE_SESSAO_ORGANIZACAO_PROPOSTA)
        if da_sessao is not None and Organizacao.objects.filter(pk=da_sessao).exists():
            return da_sessao

        primeira = Organizacao.objects.values_list("pk", flat=True).first()
        if primeira is not None:
            request.session[CHAVE_SESSAO_ORGANIZACAO_PROPOSTA] = primeira
        return primeira

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        raise PermissionDenied("Use os casos de uso nominais de proposta.")

    def has_activate_contractual_permission(self, request):
        user = getattr(request, "user", None)
        return bool(user and user.is_active and user.is_staff and user.has_perm("assinaturas.activate_contractual_propostacomercial"))

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not self.has_activate_contractual_permission(request):
            actions.pop("ativar_contratual", None)
        return actions

    @admin.action(description="Ativar proposta contratual")
    def ativar_contratual(self, request, queryset):
        if not self.has_activate_contractual_permission(request):
            raise PermissionDenied("Operador sem permissão para ativação contratual.")
        if queryset.count() != 1:
            self.message_user(
                request,
                "Selecione exatamente uma proposta para ativação contratual.",
                level="error",
            )
            return

        form = self.action_form(request.POST)
        cast(ChoiceField, form.fields["action"]).choices = (("ativar_contratual", "Ativar proposta contratual"),)
        if not form.is_valid() or not form.cleaned_data["codigo_mfa"] or not form.cleaned_data["justificativa"].strip():
            self.message_user(
                request,
                "Código MFA e justificativa são obrigatórios para ativação contratual.",
                level="error",
            )
            return

        proposta = queryset.first()
        try:
            Propostas.ativar_contratual(
                proposta,
                operador=request.user,
                revisao_esperada=proposta.revisao,
                codigo_mfa=form.cleaned_data["codigo_mfa"],
                justificativa=form.cleaned_data["justificativa"],
            )
        except ConflitoPropostaComercial as exc:
            self.message_user(request, f"Ativação recusada: {exc}", level="error")
            return
        self.message_user(request, "Proposta contratual ativada com sucesso.", level="success")
