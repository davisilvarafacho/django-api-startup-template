"""Contrato HTTP do onboarding e encerramento de organizações."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

from django.db import close_old_connections, connections
from django.utils import timezone

from rest_framework.test import APIClient

import pyotp
import pytest

from apps.api.autenticacao.mfa import confirm_enrollment, start_enrollment
from apps.api.autenticacao.models import AuthToken, MFAFactor, MFAFactorType, TokenMetaData, TokenType
from apps.api.autenticacao.services import issue_token
from apps.organizacoes.models import Organizacao, Papel, Vinculo
from apps.organizacoes.organizations import Organizacoes, TermoEncerramentoAgendado, TermoEncerramentoImediato
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db


class CatalogoHTTP:
    @classmethod
    def obter_versao_inicial(cls, *, codigo, periodicidade, moeda):
        return "versao", "preco"


class AssinaturasHTTP:
    termo = TermoEncerramentoAgendado(agendado_para=timezone.now() + timedelta(days=30))
    contratos_criados = []
    encerramentos = []

    @classmethod
    def criar_gratuita(cls, *, organizacao, versao_plano, preco_plano):
        cls.contratos_criados.append(organizacao.pk)
        return object()

    @classmethod
    def criar_trial(cls, *, organizacao, versao_plano, preco_plano):
        cls.contratos_criados.append(organizacao.pk)
        return object()

    @classmethod
    def solicitar_encerramento(cls, organizacao, *, agora):
        return cls.termo

    @classmethod
    def cancelar_encerramento(cls, organizacao):
        return None

    @classmethod
    def encerrar(cls, organizacao, *, encerrada_em):
        cls.encerramentos.append(organizacao.pk)


@pytest.fixture(autouse=True)
def _colaboradores_comerciais(monkeypatch, settings):
    from apps.organizacoes import onboarding, views

    settings.ASSINATURAS_ONBOARDING_MODO = "gratuito"
    settings.ASSINATURAS_ONBOARDING_PLANO = "gratuito"
    settings.ASSINATURAS_ONBOARDING_PERIODICIDADE = "mensal"
    AssinaturasHTTP.termo = TermoEncerramentoAgendado(agendado_para=timezone.now() + timedelta(days=30))
    AssinaturasHTTP.contratos_criados.clear()
    AssinaturasHTTP.encerramentos.clear()
    monkeypatch.setattr(onboarding, "_carregar_colaboradores_comerciais", lambda: (CatalogoHTTP, AssinaturasHTTP))
    monkeypatch.setattr(views, "_carregar_assinaturas", lambda: AssinaturasHTTP)


def _client_com_sessao(usuario, *, recente=True):
    token, plain_token = AuthToken.objects.create(user=usuario)
    TokenMetaData.objects.create(token=token, reauthenticated_at=timezone.now() if recente else None)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {plain_token}")
    return client


def _organizacao_do(usuario, *, slug="ciclo-http", papel=Papel.PROPRIETARIO):
    organizacao = Organizacao.objects.create(nome="Organização", slug=slug)
    Vinculo.objects.create(organizacao=organizacao, usuario=usuario, papel=papel)
    return organizacao


def test_post_organizacoes_exige_email_verificado_antes_do_comercial():
    usuario = criar_usuario(email_verificado_em=None)

    response = _client_com_sessao(usuario).post(
        "/organizacoes/",
        {"nome": "Sem verificação", "slug": "sem-verificacao"},
        format="json",
    )

    assert response.status_code == 403
    assert response.json()["errors"][0]["code"] == "account.email_not_verified"
    assert Organizacao.objects.filter(slug="sem-verificacao").exists() is False
    assert AssinaturasHTTP.contratos_criados == []


def test_post_organizacoes_executa_onboarding_e_devolve_organizacao():
    usuario = criar_usuario(email="owner-http@example.com", email_verificado_em=timezone.now())

    response = _client_com_sessao(usuario).post(
        "/organizacoes/",
        {"nome": "Empresa HTTP", "slug": "empresa-http"},
        format="json",
    )

    organizacao = Organizacao.objects.get(slug="empresa-http")
    assert response.status_code == 201
    assert response.json()["id"] == organizacao.pk
    assert organizacao.email_faturamento == usuario.email
    assert Vinculo.objects.get(organizacao=organizacao, usuario=usuario).papel == Papel.PROPRIETARIO
    assert AssinaturasHTTP.contratos_criados == [organizacao.pk]


def test_solicitar_encerramento_exige_sessao_recente():
    proprietario = criar_usuario()
    organizacao = _organizacao_do(proprietario, slug="sem-recent")

    response = _client_com_sessao(proprietario, recente=False).post(f"/organizacoes/{organizacao.pk}/encerramento/")

    assert response.status_code == 401
    assert response.json()["errors"][0]["code"] == "auth.reauthentication_required"
    organizacao.refresh_from_db()
    assert organizacao.encerramento_solicitado_em is None


def test_somente_proprietario_pode_solicitar_encerramento():
    membro = criar_usuario()
    organizacao = _organizacao_do(membro, slug="membro-http", papel=Papel.ADMINISTRADOR)

    response = _client_com_sessao(membro).post(f"/organizacoes/{organizacao.pk}/encerramento/")

    assert response.status_code == 403
    assert response.json()["errors"][0]["code"] == "organizations.role_insufficient"


def test_organizacao_alheia_e_id_inexistente_tem_a_mesma_resposta():
    usuario = criar_usuario()
    proprietario_alheio = criar_usuario()
    alheia = _organizacao_do(proprietario_alheio, slug="alheia-http")
    client = _client_com_sessao(usuario)

    alheia_response = client.post(f"/organizacoes/{alheia.pk}/encerramento/")
    ausente_response = client.post("/organizacoes/999999999/encerramento/")

    assert alheia_response.status_code == ausente_response.status_code == 404
    assert alheia_response.json()["errors"][0]["code"] == ausente_response.json()["errors"][0]["code"] == "core.not_found"


def test_encerramento_agendado_e_cancelado_pelas_rotas():
    proprietario = criar_usuario()
    organizacao = _organizacao_do(proprietario, slug="agenda-http")
    client = _client_com_sessao(proprietario)

    solicitacao = client.post(f"/organizacoes/{organizacao.pk}/encerramento/")
    repeticao = client.post(f"/organizacoes/{organizacao.pk}/encerramento/")
    cancelamento = client.delete(f"/organizacoes/{organizacao.pk}/encerramento/")

    organizacao.refresh_from_db()
    assert solicitacao.status_code == 202
    assert solicitacao.json()["scheduled_for"] == AssinaturasHTTP.termo.agendado_para.isoformat()
    assert repeticao.status_code == 202
    assert repeticao.json() == solicitacao.json()
    assert cancelamento.status_code == 204
    assert organizacao.encerramento_solicitado_em is None
    assert organizacao.encerramento_agendado_para is None


def test_encerramento_imediato_retorna_sem_conteudo():
    proprietario = criar_usuario()
    organizacao = _organizacao_do(proprietario, slug="imediato-http")
    AssinaturasHTTP.termo = TermoEncerramentoImediato()

    response = _client_com_sessao(proprietario).post(f"/organizacoes/{organizacao.pk}/encerramento/")

    assert response.status_code == 204
    assert Organizacao.all_objects.get(pk=organizacao.pk).is_deleted is True
    assert AssinaturasHTTP.encerramentos == [organizacao.pk]


@pytest.mark.django_db(transaction=True)
def test_duas_solicitacoes_imediatas_concorrentes_retornam_sem_conteudo(monkeypatch):
    proprietario = criar_usuario()
    organizacao = _organizacao_do(proprietario, slug="imediato-http-concorrente")
    AssinaturasHTTP.termo = TermoEncerramentoImediato()
    plain_tokens = []
    for _ in range(2):
        token, plain_token = AuthToken.objects.create(user=proprietario)
        TokenMetaData.objects.create(token=token, reauthenticated_at=timezone.now())
        plain_tokens.append(plain_token)

    barreira = Barrier(2)
    original = Organizacoes.solicitar_encerramento.__func__

    def solicitar_sincronizado(cls, *args, **kwargs):
        barreira.wait(timeout=5)
        return original(cls, *args, **kwargs)

    monkeypatch.setattr(Organizacoes, "solicitar_encerramento", classmethod(solicitar_sincronizado))

    def solicitar(plain_token):
        close_old_connections()
        try:
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION=f"Bearer {plain_token}")
            return client.post(f"/organizacoes/{organizacao.pk}/encerramento/").status_code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        status_codes = list(executor.map(solicitar, plain_tokens))

    assert status_codes == [204, 204]
    assert Organizacao.all_objects.get(pk=organizacao.pk).is_deleted is True
    assert AssinaturasHTTP.encerramentos == [organizacao.pk]


def test_encerramento_com_mfa_exige_reautenticacao_com_segundo_fator(django_capture_on_commit_callbacks):
    proprietario = criar_usuario()
    organizacao = _organizacao_do(proprietario, slug="mfa-http")
    enrollment = start_enrollment(proprietario, MFAFactorType.TOTP)
    confirm_enrollment(proprietario, MFAFactorType.TOTP, pyotp.TOTP(enrollment.plain_secret).now())
    MFAFactor.objects.filter(pk=enrollment.factor.pk).update(totp_last_counter=None)
    issued = issue_token(
        responsavel=proprietario,
        token_type=TokenType.TOKEN,
        created_by=proprietario,
        expiry=None,
        metadata_input={},
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {issued.plain_token}")

    assert client.post("/auth/reauthenticate/", {"password": "senha-de-teste"}, format="json").status_code == 202
    assert client.post(f"/organizacoes/{organizacao.pk}/encerramento/").status_code == 401
    assert client.post("/auth/reauthenticate/challenge/start/", {"type": "totp"}, format="json").status_code == 201
    with django_capture_on_commit_callbacks(execute=True):
        confirmacao = client.post(
            "/auth/reauthenticate/challenge/verify/",
            {"type": "totp", "code": pyotp.TOTP(enrollment.plain_secret).now()},
            format="json",
        )

    assert confirmacao.status_code == 204
    assert client.post(f"/organizacoes/{organizacao.pk}/encerramento/").status_code == 202
