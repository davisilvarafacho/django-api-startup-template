from datetime import timedelta

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

import pytest

from apps.assinaturas.admin import PropostaComercialActionForm
from apps.assinaturas.models import ModoAtivacaoProposta, Periodicidade, Plano, PrecoPlano, PropostaComercial, VersaoPlano
from apps.assinaturas.proposals import Propostas
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Organizacao
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db


def _criar_proposta(organizacao: Organizacao) -> PropostaComercial:
    with organizacao_atual_privilegiada(organizacao.pk):
        return PropostaComercial.objects.create(
            organizacao=organizacao,
            modo_ativacao=ModoAtivacaoProposta.CONTRATUAL,
            periodicidade=Periodicidade.ANUAL,
            moeda="BRL",
            valor_base_centavos=120_000,
            valor_seat_centavos=5_000,
            recursos={},
            valida_ate=timezone.now() + timedelta(days=30),
        )


def test_admin_nao_edita_versao_publicada_nem_seu_preco():
    plano = Plano(codigo="gratuito", nome="Gratuito")
    versao = VersaoPlano(plano=plano, numero=1, publicada_em=timezone.now())
    preco = PrecoPlano(versao_plano=versao)

    assert admin.site._registry[VersaoPlano].has_change_permission(None, versao) is False
    assert admin.site._registry[PrecoPlano].has_change_permission(None, preco) is False


def test_admin_nao_exclui_versao_nem_preco_do_historico():
    assert admin.site._registry[VersaoPlano].has_delete_permission(None) is False
    assert admin.site._registry[PrecoPlano].has_delete_permission(None) is False


def test_admin_proposta_bloqueia_crud_generico_e_expoe_form_de_ativacao():
    proposta_admin = admin.site._registry[PropostaComercial]

    assert proposta_admin.has_add_permission(None) is False
    assert proposta_admin.has_change_permission(None, PropostaComercial()) is False
    assert proposta_admin.has_delete_permission(None) is False
    assert {"codigo_mfa", "justificativa"} <= set(PropostaComercialActionForm.base_fields)
    with pytest.raises(PermissionDenied, match="casos de uso"):
        proposta_admin.save_model(None, PropostaComercial(), form=None, change=True)


def test_changelist_admin_proposta_aplica_contexto_rls(client):
    operador = criar_usuario(email="operador-changelist-proposta@example.com", is_staff=True, is_superuser=True)
    organizacao = Organizacao.objects.create(nome="Admin proposta", slug="admin-proposta")
    outra_organizacao = Organizacao.objects.create(nome="Outra proposta", slug="outra-proposta")
    proposta = _criar_proposta(organizacao)
    _criar_proposta(outra_organizacao)
    client.force_login(operador)

    response = client.get(
        reverse("admin:assinaturas_propostacomercial_changelist"),
        {"organizacao__id__exact": organizacao.pk},
    )

    assert response.status_code == 200
    assert {item.pk for item in response.context["cl"].result_list} == {proposta.pk}


def test_acao_admin_ativa_uma_proposta_via_caso_de_uso_auditado(monkeypatch):
    proposta_admin = admin.site._registry[PropostaComercial]
    operador = criar_usuario(email="operador-admin-proposta@example.com", is_staff=True, is_superuser=True)
    proposta = PropostaComercial(id=7, revisao=3)
    queryset = type(
        "QuerysetProposta",
        (),
        {"count": lambda self: 1, "first": lambda self: proposta},
    )()
    observado = {}

    def ativar(proposta_recebida, **kwargs):
        observado.update(proposta=proposta_recebida, **kwargs)

    monkeypatch.setattr(Propostas, "ativar_contratual", ativar)
    monkeypatch.setattr(proposta_admin, "message_user", lambda *args, **kwargs: None)
    request = RequestFactory().post(
        "/admin/assinaturas/propostacomercial/",
        {
            "action": "ativar_contratual",
            "codigo_mfa": "123456",
            "justificativa": "Contrato conferido pelo jurídico",
        },
    )
    request.user = operador

    assert "ativar_contratual" in proposta_admin.get_actions(request)
    proposta_admin.ativar_contratual(request, queryset)

    assert observado == {
        "proposta": proposta,
        "operador": operador,
        "revisao_esperada": 3,
        "codigo_mfa": "123456",
        "justificativa": "Contrato conferido pelo jurídico",
    }


def test_acao_admin_rejeita_lote_e_operador_sem_permissao(monkeypatch):
    proposta_admin = admin.site._registry[PropostaComercial]
    operador = criar_usuario(email="operador-lote-admin-proposta@example.com", is_staff=True, is_superuser=True)
    operador_sem_permissao = criar_usuario(email="operador-sem-perm-admin-proposta@example.com", is_staff=True)
    request = RequestFactory().post(
        "/admin/assinaturas/propostacomercial/",
        {
            "action": "ativar_contratual",
            "codigo_mfa": "123456",
            "justificativa": "Contrato conferido",
        },
    )
    request.user = operador
    mensagens = []
    monkeypatch.setattr(proposta_admin, "message_user", lambda request, message, **kwargs: mensagens.append(message))

    proposta_admin.ativar_contratual(request, type("Lote", (), {"count": lambda self: 2})())

    request.user = operador_sem_permissao
    assert proposta_admin.has_activate_contractual_permission(request) is False
    assert mensagens == ["Selecione exatamente uma proposta para ativação contratual."]
