from django.contrib import admin
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory
from django.urls import reverse

import pytest

from apps.assinaturas.subapps.faturamento.models import EventoCobranca, ReaberturaEventoCobranca, StatusEventoCobranca
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Organizacao
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db


def test_trilha_de_reabertura_esta_disponivel_no_admin_somente_para_consulta():
    reabertura_admin = admin.site._registry[ReaberturaEventoCobranca]

    assert reabertura_admin.has_add_permission(None) is False
    assert reabertura_admin.has_change_permission(None) is False
    assert reabertura_admin.has_delete_permission(None) is False


def _evento(organizacao, sufixo):
    with organizacao_atual_privilegiada(organizacao.pk):
        return EventoCobranca.objects.create(
            organizacao=organizacao,
            variante="stripe",
            identificador_evento=f"evt_admin_{sufixo}",
            tipo="invoice.paid",
            identificador_assinatura=f"sub_admin_{sufixo}",
            identificador_fatura=f"in_admin_{sufixo}",
            status=StatusEventoCobranca.FALHOU,
            hash_payload="a" * 64,
        )


def test_changelist_admin_faturamento_renderiza_sob_tenant_e_isola_resultados(client):
    operador = criar_usuario(email="admin-faturamento@example.com", is_staff=True, is_superuser=True)
    organizacao = Organizacao.objects.create(nome="Admin faturamento A", slug="admin-faturamento-a")
    outra = Organizacao.objects.create(nome="Admin faturamento B", slug="admin-faturamento-b")
    evento = _evento(organizacao, "a")
    _evento(outra, "b")
    client.force_login(operador)

    response = client.get(
        reverse("admin:faturamento_eventocobranca_changelist"),
        {"organizacao__id__exact": organizacao.pk},
    )

    assert response.status_code == 200
    assert {item.pk for item in response.context["cl"].result_list} == {evento.pk}


def test_actions_financeiras_exigem_permissoes_operacionais_especificas(monkeypatch):
    evento_admin = admin.site._registry[EventoCobranca]
    permissoes_modelo = dict(EventoCobranca._meta.permissions)
    assert {"retry_failed_eventocobranca", "reconcile_eventocobranca"} <= set(permissoes_modelo)
    content_type = ContentType.objects.get_for_model(EventoCobranca)
    operador = criar_usuario(email="admin-change-only@example.com", is_staff=True)
    operador.user_permissions.add(Permission.objects.get(content_type=content_type, codename="view_eventocobranca"))
    operador.user_permissions.add(Permission.objects.get(content_type=content_type, codename="change_eventocobranca"))
    request = RequestFactory().post("/admin/faturamento/eventocobranca/")
    request.user = operador

    assert {"reabrir_falhos", "reconciliar_variantes"}.isdisjoint(evento_admin.get_actions(request))
    with pytest.raises(PermissionDenied, match="retry financeiro"):
        evento_admin.reabrir_falhos(request, EventoCobranca.objects.none())

    operador_retry = criar_usuario(email="admin-retry-only@example.com", is_staff=True)
    operador_retry.user_permissions.add(Permission.objects.get(content_type=content_type, codename="view_eventocobranca"))
    operador_retry.user_permissions.add(Permission.objects.get(content_type=content_type, codename="retry_failed_eventocobranca"))
    request.user = operador_retry
    monkeypatch.setattr(evento_admin, "message_user", lambda *args, **kwargs: None)

    assert "reabrir_falhos" in evento_admin.get_actions(request)
    assert "reconciliar_variantes" not in evento_admin.get_actions(request)
