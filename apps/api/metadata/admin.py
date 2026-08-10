"""Admin dos metadados genéricos.

`Metadata` herda de `Base`, então é isolado por organização e o RLS exige
contexto de tenant em **toda** query (`DJANGO_RLS["REQUIRE_CONTEXT"]`). As rotas
`/admin/` não passam pelo `OrganizacaoMiddleware`, logo um `ModelAdmin` comum
levantaria `RLSContextRequiredError` já na listagem. Por isso as views deste
admin são embrulhadas em `organizacao_atual_privilegiada`: o staff escolhe a
organização pelo filtro da listagem e ela é lembrada na sessão para as demais
telas (edição, histórico, exclusão).
"""

from functools import wraps

from django.contrib import admin, messages
from django.shortcuts import redirect
from django.utils.translation import gettext_lazy as _

from apps.api.base.admin import BaseModelAdmin
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Organizacao

from .models import Metadata

CHAVE_SESSAO_ORGANIZACAO = "metadata_admin_organizacao_id"
PARAMETRO_ORGANIZACAO = "organizacao__id__exact"
LIMITE_CHAVES_RESUMO = 5


@admin.register(Metadata)
class MetadataAdmin(BaseModelAdmin):
    """Inspeção dos metadados de qualquer modelo, uma organização por vez."""

    list_display = ("id", "content_type", "object_id", "objeto", "organizacao", "resumo_dados", "is_active", "created_at")
    list_display_links = ("id", "content_type")
    list_filter = ("organizacao", "content_type", "is_active")
    search_fields = ("=object_id", "content_type__app_label", "content_type__model")
    list_select_related = ("content_type", "organizacao")
    ordering = ("-id",)

    # `clone_records` do BaseModelAdmin não se aplica: a dupla
    # content_type + object_id é única, então todo clone violaria a constraint.
    actions = ("ativar_registros", "inativar_registros")

    readonly_fields = ("objeto", "organizacao", "created_by", "created_at", "last_modified_at")
    fieldsets = (
        (_("Alvo"), {"fields": ("content_type", "object_id", "objeto")}),
        (_("Dados"), {"fields": ("dados",)}),
        (_("Controle"), {"fields": ("is_active", "organizacao")}),
        (
            _("Auditoria"),
            {"fields": ("created_by", "created_at", "last_modified_at"), "classes": ("collapse",)},
        ),
    )

    def get_queryset(self, request):
        """Restringe à organização vigente e resolve as generic FKs em lote.

        O RLS já restringe no banco, mas só quando o papel do Postgres não o
        ignora — um superusuário ignora, e é o papel usado em desenvolvimento.
        O filtro explícito garante "uma organização por vez" em qualquer
        ambiente; o `prefetch` evita o N+1 do `objeto` na listagem.
        """
        queryset = super().get_queryset(request).prefetch_related("object")
        organizacao_id = request.session.get(CHAVE_SESSAO_ORGANIZACAO)
        if organizacao_id is not None:
            queryset = queryset.filter(organizacao_id=organizacao_id)
        return queryset

    def get_readonly_fields(self, request, obj=None):
        campos = super().get_readonly_fields(request, obj)
        if obj is not None:
            # content_type + object_id é a identidade do registro; trocá-la
            # equivale a mover os dados para outro objeto.
            campos = tuple(campos) + ("content_type", "object_id")
        return campos

    @admin.display(description=_("objeto"))
    def objeto(self, obj):
        """Representação do objeto apontado pela generic FK."""
        if obj.content_type_id is None or obj.object_id is None:
            return "—"
        alvo = obj.object
        return str(alvo) if alvo is not None else _("(objeto removido)")

    @admin.display(description=_("dados"))
    def resumo_dados(self, obj):
        """Chaves do JSON, para dar noção do conteúdo sem abrir o registro."""
        dados = obj.dados
        if not dados:
            return "—"
        if not isinstance(dados, dict):
            return _("%(total)d item(ns)") % {"total": len(dados)}
        chaves = sorted(dados)
        resumo = ", ".join(chaves[:LIMITE_CHAVES_RESUMO])
        restantes = len(chaves) - LIMITE_CHAVES_RESUMO
        return f"{resumo} (+{restantes})" if restantes > 0 else resumo

    def get_urls(self):
        """Embrulha todas as views do admin no contexto de tenant.

        É aqui, e não em `get_queryset`, porque o queryset é preguiçoso: quem
        dispara a query é a renderização do template, já fora do escopo de
        qualquer gerenciador de contexto aberto na view.
        """
        urls = super().get_urls()
        for url in urls:
            url.callback = self._com_organizacao(url.callback)
        return urls

    def _com_organizacao(self, view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            organizacao_id = self._resolver_organizacao(request)
            if organizacao_id is None:
                self.message_user(
                    request,
                    _("Cadastre uma organização antes de acessar os metadados."),
                    level=messages.WARNING,
                )
                return redirect("admin:index")

            with organizacao_atual_privilegiada(organizacao_id):
                response = view(request, *args, **kwargs)
                # As views do admin devolvem `TemplateResponse`, renderizado só
                # depois pelo middleware — ou seja, fora do contexto de tenant.
                # Forçar a renderização aqui mantém a query dentro dele.
                if hasattr(response, "render") and callable(response.render):
                    response.render()
                return response

        return wrapper

    def _resolver_organizacao(self, request):
        """Organização vigente: filtro da listagem > sessão > primeira cadastrada."""
        do_filtro = request.GET.get(PARAMETRO_ORGANIZACAO, "")
        if do_filtro.isdigit():
            organizacao_id = int(do_filtro)
            request.session[CHAVE_SESSAO_ORGANIZACAO] = organizacao_id
            return organizacao_id

        da_sessao = request.session.get(CHAVE_SESSAO_ORGANIZACAO)
        if da_sessao is not None and Organizacao.objects.filter(pk=da_sessao).exists():
            return da_sessao

        primeira = Organizacao.objects.values_list("pk", flat=True).first()
        if primeira is not None:
            request.session[CHAVE_SESSAO_ORGANIZACAO] = primeira
        return primeira
