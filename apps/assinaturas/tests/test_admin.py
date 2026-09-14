from datetime import timedelta

from django.contrib import admin
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

import pyotp
import pytest

from apps.api.autenticacao.mfa import confirm_enrollment, start_enrollment
from apps.api.autenticacao.models import MFAFactor, MFAFactorType
from apps.assinaturas.admin import PropostaComercialActionForm
from apps.assinaturas.models import (
    AssinaturaOrganizacao,
    ModoAtivacaoProposta,
    Periodicidade,
    Plano,
    PrecoPlano,
    PropostaComercial,
    StatusPropostaComercial,
    VersaoPlano,
)
from apps.assinaturas.proposals import Propostas
from apps.assinaturas.tests.test_proposals import _criar_rascunho, _enviar
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Organizacao, Papel, Vinculo
from tests.support.usuarios import criar_usuario as _criar_usuario

pytestmark = pytest.mark.django_db


def criar_usuario(**campos):
    campos.setdefault("email_verificado_em", timezone.now())
    return _criar_usuario(**campos)


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


def _proposta_contratual_aceita(organizacao: Organizacao) -> PropostaComercial:
    proprietario = criar_usuario(email=f"owner-admin-{organizacao.slug}@example.com", email_verificado_em=timezone.now())
    Vinculo.objects.create(organizacao=organizacao, usuario=proprietario, papel=Papel.PROPRIETARIO)
    proposta = _enviar(_criar_rascunho(organizacao, modo=ModoAtivacaoProposta.CONTRATUAL))
    return Propostas.aceitar(proposta, ator=proprietario, revisao_esperada=2).proposta


def _habilitar_totp(operador):
    enrollment = start_enrollment(operador, MFAFactorType.TOTP)
    codigo = pyotp.TOTP(enrollment.plain_secret).now()
    confirm_enrollment(operador, MFAFactorType.TOTP, codigo)
    MFAFactor.objects.filter(pk=enrollment.factor.pk).update(totp_last_counter=None)
    return enrollment.plain_secret


def _postar_ativacao_admin(client, organizacao, proposta, *, codigo_mfa, justificativa):
    url = reverse("admin:assinaturas_propostacomercial_changelist")
    return client.post(
        f"{url}?organizacao__id__exact={organizacao.pk}",
        {
            "action": "ativar_contratual",
            "_selected_action": [str(proposta.pk)],
            "select_across": "0",
            "index": "0",
            "codigo_mfa": codigo_mfa,
            "justificativa": justificativa,
        },
    )


def test_action_admin_http_ativa_apenas_no_tenant_filtrado_com_totp_e_justificativa(client):
    operador = criar_usuario(email="operador-http-admin-proposta@example.com", is_staff=True, is_superuser=True)
    organizacao = Organizacao.objects.create(nome="Admin HTTP", slug="admin-http-proposta")
    proposta = _proposta_contratual_aceita(organizacao)
    segredo = _habilitar_totp(operador)
    client.force_login(operador)

    response = _postar_ativacao_admin(
        client,
        organizacao,
        proposta,
        codigo_mfa=pyotp.TOTP(segredo).now(),
        justificativa="Contrato validado por jurídico e financeiro",
    )

    assert response.status_code == 302
    with organizacao_atual_privilegiada(organizacao.pk):
        proposta.refresh_from_db()
        assinatura = AssinaturaOrganizacao.objects.get(proposta_comercial=proposta)
    assert proposta.status == StatusPropostaComercial.ATIVADA
    assert assinatura.organizacao_id == organizacao.pk


def test_action_admin_http_nao_aceita_selecao_de_outro_tenant(client):
    operador = criar_usuario(email="operador-cross-admin-proposta@example.com", is_staff=True, is_superuser=True)
    organizacao = Organizacao.objects.create(nome="Admin tenant A", slug="admin-tenant-a")
    outra = Organizacao.objects.create(nome="Admin tenant B", slug="admin-tenant-b")
    proposta_alheia = _proposta_contratual_aceita(outra)
    segredo = _habilitar_totp(operador)
    client.force_login(operador)

    response = _postar_ativacao_admin(
        client,
        organizacao,
        proposta_alheia,
        codigo_mfa=pyotp.TOTP(segredo).now(),
        justificativa="Tentativa cross tenant",
    )

    assert response.status_code == 302
    with organizacao_atual_privilegiada(outra.pk):
        proposta_alheia.refresh_from_db()
        assert not AssinaturaOrganizacao.objects.filter(proposta_comercial=proposta_alheia).exists()
    assert proposta_alheia.status == StatusPropostaComercial.ACEITA


def test_action_admin_http_exige_permissao_especifica(client):
    operador = criar_usuario(email="operador-sem-permissao-http@example.com", is_staff=True)
    organizacao = Organizacao.objects.create(nome="Admin sem permissão", slug="admin-sem-permissao")
    proposta = _proposta_contratual_aceita(organizacao)
    content_type = ContentType.objects.get_for_model(PropostaComercial)
    operador.user_permissions.add(Permission.objects.get(content_type=content_type, codename="view_propostacomercial"))
    segredo = _habilitar_totp(operador)
    client.force_login(operador)

    response = _postar_ativacao_admin(
        client,
        organizacao,
        proposta,
        codigo_mfa=pyotp.TOTP(segredo).now(),
        justificativa="Sem permissão específica",
    )

    assert response.status_code in (200, 302)
    with organizacao_atual_privilegiada(organizacao.pk):
        proposta.refresh_from_db()
        assert not AssinaturaOrganizacao.objects.filter(proposta_comercial=proposta).exists()
    assert proposta.status == StatusPropostaComercial.ACEITA


@pytest.mark.parametrize(
    ("codigo_mfa", "justificativa"),
    [("000000", "Contrato válido"), ("codigo-correto", "   ")],
)
def test_action_admin_http_exige_totp_valido_e_justificativa(client, codigo_mfa, justificativa):
    operador = criar_usuario(email=f"operador-campos-{codigo_mfa}@example.com", is_staff=True, is_superuser=True)
    organizacao = Organizacao.objects.create(nome=f"Admin campos {codigo_mfa}", slug=f"admin-campos-{codigo_mfa}")
    proposta = _proposta_contratual_aceita(organizacao)
    segredo = _habilitar_totp(operador)
    client.force_login(operador)
    codigo = pyotp.TOTP(segredo).now() if codigo_mfa == "codigo-correto" else codigo_mfa

    response = _postar_ativacao_admin(client, organizacao, proposta, codigo_mfa=codigo, justificativa=justificativa)

    assert response.status_code == 302
    with organizacao_atual_privilegiada(organizacao.pk):
        proposta.refresh_from_db()
        assert not AssinaturaOrganizacao.objects.filter(proposta_comercial=proposta).exists()
    assert proposta.status == StatusPropostaComercial.ACEITA
