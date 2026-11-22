"""Encerramento imediato e idempotente de organizações."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic, sleep
from unittest.mock import patch

from django.db import close_old_connections, connections
from django.utils import timezone

import pytest
from celery.exceptions import Retry
from django_checkouts.enums import CancellationTiming, Gateway, SubscriptionStatus
from django_checkouts.exceptions import GatewayTemporaryError
from django_checkouts.gateways.commands import CancelRemoteSubscription, RetrieveSubscription

from apps.api.autenticacao.models import AuthToken, TokenMetaData, TokenType
from apps.api.core.errors import APIError
from apps.billing.members import MemberLimits
from apps.billing.subscriptions import Subscriptions
from apps.billing.tasks import cancel_for_closed_organization
from apps.organizacoes.memberships import Vinculos
from apps.organizacoes.models import Convite, Organizacao, Papel, Vinculo
from apps.organizacoes.organizations import Organizacoes
from tests.support.billing import patch_billing_gateway, remote_subscription, subscribe_organization
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db


def _cenario_encerramento(slug="encerramento"):
    proprietario = criar_usuario(email=f"owner-{slug}@example.com", email_verificado_em=timezone.now())
    membro = criar_usuario(email=f"membro-{slug}@example.com")
    organizacao = Organizacao.objects.create(nome="Organização", slug=slug)
    vinculos = [
        Vinculo.objects.create(organizacao=organizacao, usuario=proprietario, papel=Papel.PROPRIETARIO),
        Vinculo.objects.create(organizacao=organizacao, usuario=membro, papel=Papel.MEMBRO),
    ]
    convite = Convite.objects.create(organizacao=organizacao, email=f"convite-{slug}@example.com")
    api_key, _ = AuthToken.objects.create(
        responsavel=proprietario,
        type=TokenType.API_KEY,
        created_by=proprietario,
        organization=organizacao,
        name="Integração",
        scopes=["organizations:read"],
    )
    sessao, _ = AuthToken.objects.create(responsavel=proprietario, type=TokenType.TOKEN)
    return proprietario, organizacao, vinculos, convite, api_key, sessao


def test_encerramento_revoga_acessos_sem_excluir_usuarios():
    agora = timezone.now()
    proprietario, organizacao, vinculos, convite, api_key, sessao = _cenario_encerramento("imediato")

    encerrada = Organizacoes.solicitar_encerramento(organizacao, ator=proprietario, agora=agora)

    organizacao = Organizacao.all_objects.get(pk=organizacao.pk)
    api_key.refresh_from_db()
    sessao.refresh_from_db()
    convite.refresh_from_db()
    assert encerrada is True
    assert organizacao.encerramento_solicitado_em == agora
    assert organizacao.encerramento_agendado_para is None
    assert organizacao.is_active is False
    assert organizacao.is_deleted is True
    assert Vinculo.all_objects.filter(pk__in=[item.pk for item in vinculos], is_active=True).exists() is False
    assert convite.is_deleted is True
    assert api_key.revoked_at == agora
    assert api_key.revoked_by == proprietario
    assert sessao.revoked_at is None
    assert type(proprietario).objects.filter(pk__in=[item.usuario_id for item in vinculos]).count() == 2


def test_servico_revalida_proprietario_antes_de_encerrar():
    agora = timezone.now()
    _, organizacao, _, _, _, _ = _cenario_encerramento("ator-rebaixado")
    ator = criar_usuario(email="admin-ator-rebaixado@example.com", email_verificado_em=agora)
    Vinculo.objects.create(organizacao=organizacao, usuario=ator, papel=Papel.ADMINISTRADOR)

    with pytest.raises(APIError) as erro:
        Organizacoes.solicitar_encerramento(organizacao, ator=ator, agora=agora)

    assert erro.value.code == "organizations.role_insufficient"
    organizacao.refresh_from_db()
    assert organizacao.encerramento_solicitado_em is None
    assert organizacao.is_active is True


def test_repetir_encerramento_e_idempotente():
    _, organizacao, _, _, _, _ = _cenario_encerramento("idempotente")

    primeiro = Organizacoes.solicitar_encerramento(organizacao)
    segundo = Organizacoes.solicitar_encerramento(organizacao)

    assert primeiro is True
    assert segundo is False


def test_rollback_depois_da_revogacao_nao_publica_auditoria_de_api_key(monkeypatch, django_capture_on_commit_callbacks):
    agora = timezone.now()
    proprietario, organizacao, vinculos, _, api_key, _ = _cenario_encerramento("rollback-auditoria")

    def falhar_cancelamento(self, using=None, keep_parents=False):
        raise RuntimeError("convite")

    monkeypatch.setattr(Convite, "delete", falhar_cancelamento)
    with patch("apps.api.autenticacao.audit.capture") as capture_mock:
        with pytest.raises(RuntimeError, match="convite"), django_capture_on_commit_callbacks(execute=True):
            Organizacoes.solicitar_encerramento(organizacao, ator=proprietario, agora=agora)

    organizacao.refresh_from_db()
    api_key.refresh_from_db()
    assert organizacao.encerramento_solicitado_em is None
    assert organizacao.is_active is True
    assert Vinculo.objects.filter(pk__in=[item.pk for item in vinculos], is_active=True).count() == 2
    assert api_key.revoked_at is None
    capture_mock.assert_not_called()


def test_organizacao_com_encerramento_registrado_nao_cria_nem_aceita_convite():
    proprietario, organizacao, _, convite, _, _ = _cenario_encerramento("bloqueia-convite")
    convidado = criar_usuario(email=convite.email, email_verificado_em=timezone.now())
    Organizacao.objects.filter(pk=organizacao.pk).update(encerramento_solicitado_em=timezone.now())

    with pytest.raises(APIError) as criacao:
        Vinculos.criar_convite(
            organizacao=organizacao,
            email="novo@example.com",
            papel=Papel.MEMBRO,
            convidado_por=proprietario,
        )
    with pytest.raises(APIError) as aceite:
        Vinculos.aceitar_convite(convite, convidado)

    assert criacao.value.code == "organizations.closure_pending"
    assert aceite.value.code == "organizations.closure_pending"
    assert Convite.objects.filter(organizacao=organizacao).count() == 1
    assert Vinculo.objects.filter(organizacao=organizacao, usuario=convidado).exists() is False


@pytest.mark.django_db(transaction=True)
def test_encerramentos_concorrentes_efetivam_uma_unica_vez():
    _, organizacao, _, _, api_key, _ = _cenario_encerramento("concorrente")

    def encerrar():
        close_old_connections()
        try:
            return Organizacoes.solicitar_encerramento(organizacao)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        resultados = list(executor.map(lambda _: encerrar(), range(2)))

    api_key.refresh_from_db()
    assert sorted(resultados) == [False, True]
    assert Organizacao.all_objects.get(pk=organizacao.pk).is_deleted is True
    assert api_key.revoked_at is not None


@pytest.mark.django_db(transaction=True)
def test_efetivacao_concorrente_com_criacao_nao_deixa_api_key_viva(monkeypatch):
    from apps.api.autenticacao import services

    agora = timezone.now()
    proprietario, organizacao, _, _, _, _ = _cenario_encerramento("concorrente-criacao-key")
    criacao_pronta = Event()
    permitir_criacao = Event()
    encerramento_revogou = Event()
    permitir_encerramento = Event()
    original_metadata_create = TokenMetaData.objects.create
    original_revoke = services.revoke_organization_api_keys

    def pausar_apos_criacao_do_metadata(*args, **kwargs):
        resultado = original_metadata_create(*args, **kwargs)
        criacao_pronta.set()
        assert permitir_criacao.wait(timeout=5)
        return resultado

    def pausar_apos_revogacao(*args, **kwargs):
        resultado = original_revoke(*args, **kwargs)
        encerramento_revogou.set()
        assert permitir_encerramento.wait(timeout=5)
        return resultado

    monkeypatch.setattr(TokenMetaData.objects, "create", pausar_apos_criacao_do_metadata)
    monkeypatch.setattr(services, "revoke_organization_api_keys", pausar_apos_revogacao)

    def efetivar():
        close_old_connections()
        try:
            return Organizacoes.solicitar_encerramento(organizacao, agora=agora)
        finally:
            connections.close_all()

    def criar():
        close_old_connections()
        try:
            return services.create_api_key(
                responsavel=proprietario,
                created_by=proprietario,
                name="Criada durante encerramento",
                scopes=["organizations:read"],
                organization=organizacao,
            ).instance.pk
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        criacao = executor.submit(criar)
        assert criacao_pronta.wait(timeout=5)
        efetivacao = executor.submit(efetivar)
        # Com a ordem Usuario -> Organizacao, o encerramento ainda não chega
        # à revogação. Sem o lock da organização, ele revoga apenas as chaves
        # já confirmadas e deixa a criação concorrente sobreviver ao commit.
        encerramento_revogou.wait(timeout=0.5)
        permitir_criacao.set()
        permitir_encerramento.set()
        criacao.result(timeout=10)
        assert efetivacao.result(timeout=10) is True

    assert (
        AuthToken.objects.filter(
            organization=organizacao,
            type=TokenType.API_KEY,
            revoked_at__isnull=True,
        ).exists()
        is False
    )


def _aguardar_query_bloqueada(pid: int, *, trecho: str) -> None:
    limite = monotonic() + 5
    while monotonic() < limite:
        with connections["default"].cursor() as cursor:
            cursor.execute(
                "SELECT wait_event_type, query FROM pg_stat_activity WHERE pid = %s",
                [pid],
            )
            row = cursor.fetchone()
        if row and row[0] == "Lock" and trecho in row[1]:
            return
        sleep(0.01)
    raise AssertionError(f"A query de {trecho} não aguardou o lock esperado.")


@pytest.mark.django_db(transaction=True)
def test_efetivacao_concorrente_com_rotacao_nao_deixa_api_key_viva_nem_deadlock(monkeypatch):
    from apps.api.autenticacao import services

    agora = timezone.now()
    proprietario, organizacao, _, _, api_key, _ = _cenario_encerramento("concorrente-rotacao-key")
    TokenMetaData.objects.create(token=api_key)
    rotacao_pronta = Event()
    permitir_rotacao = Event()
    encerramento_na_revogacao = Event()
    encerramento_pid = []
    original_metadata_create = TokenMetaData.objects.create
    original_revoke = services.revoke_organization_api_keys

    def pausar_metadata(*args, **kwargs):
        resultado = original_metadata_create(*args, **kwargs)
        rotacao_pronta.set()
        assert permitir_rotacao.wait(timeout=5)
        return resultado

    def registrar_revogacao(*args, **kwargs):
        database_connection = connections["default"]
        database_connection.ensure_connection()
        encerramento_pid.append(database_connection.connection.info.backend_pid)
        encerramento_na_revogacao.set()
        return original_revoke(*args, **kwargs)

    monkeypatch.setattr(TokenMetaData.objects, "create", pausar_metadata)
    monkeypatch.setattr(services, "revoke_organization_api_keys", registrar_revogacao)

    def rotacionar():
        close_old_connections()
        try:
            return services.rotate_api_key(api_key, actor=proprietario).instance.pk
        finally:
            connections.close_all()

    def efetivar():
        close_old_connections()
        try:
            return Organizacoes.solicitar_encerramento(organizacao, agora=agora)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        rotacao = executor.submit(rotacionar)
        assert rotacao_pronta.wait(timeout=5)
        efetivacao = executor.submit(efetivar)
        if encerramento_na_revogacao.wait(timeout=0.5):
            _aguardar_query_bloqueada(encerramento_pid[0], trecho='"auth_token"')
        permitir_rotacao.set()
        rotacao.result(timeout=10)
        assert efetivacao.result(timeout=10) is True

    assert (
        AuthToken.objects.filter(
            organization=organizacao,
            type=TokenType.API_KEY,
            revoked_at__isnull=True,
        ).exists()
        is False
    )


def test_closed_organization_cancels_immediately_and_reactivates_nobody(monkeypatch, django_capture_on_commit_callbacks):
    moment = timezone.now()
    owner, organization, _, _, _, _ = _cenario_encerramento("cancel-paid")
    subscribe_organization(organization, member_limit=10)
    Subscriptions.link_external(organization.pk, external_id="sub_1", gateway_variant="stripe")
    suspended = criar_usuario(email="suspended-cancela-pago@example.com")
    Vinculo.objects.create(
        organizacao=organization,
        usuario=suspended,
        papel=Papel.MEMBRO,
        is_active=False,
        plan_limit_suspended_at=moment,
    )
    canceled_remote = remote_subscription(external_id="sub_1", reference_id=str(organization.pk), status=SubscriptionStatus.CANCELED)
    gateway = patch_billing_gateway(monkeypatch, {CancelRemoteSubscription: canceled_remote, RetrieveSubscription: canceled_remote})

    with django_capture_on_commit_callbacks(execute=True):
        Organizacoes.solicitar_encerramento(organization, ator=owner, agora=moment)

    cancellations = [command for command in gateway.commands if isinstance(command, CancelRemoteSubscription)]
    assert len(cancellations) == 1
    assert cancellations[0].timing == CancellationTiming.IMMEDIATELY
    assert cancellations[0].idempotency_key == f"billing:close:{organization.pk}"

    # Nova aplicação do limite depois do encerramento: a organização inativa não pode reativar vínculos.
    MemberLimits.apply(organization.pk)

    assert Vinculo.objects.filter(organizacao=organization, is_active=True).exists() is False
    suspended_membership = Vinculo.all_objects.get(organizacao=organization, usuario=suspended)
    assert suspended_membership.is_active is False
    assert suspended_membership.plan_limit_suspended_at == moment


def test_closure_cancel_retries_after_temporary_gateway_failure(monkeypatch, django_capture_on_commit_callbacks):
    moment = timezone.now()
    owner, organization, _, _, _, _ = _cenario_encerramento("repete-cancelamento")
    subscribe_organization(organization, member_limit=10)
    Subscriptions.link_external(organization.pk, external_id="sub_1", gateway_variant="stripe")
    canceled_remote = remote_subscription(external_id="sub_1", reference_id=str(organization.pk), status=SubscriptionStatus.CANCELED)
    calls = []

    def cancel_fails_once(command):
        calls.append(command)
        if len(calls) == 1:
            raise GatewayTemporaryError("indisponível", gateway=Gateway.STRIPE, variant="stripe")
        return canceled_remote

    gateway = patch_billing_gateway(
        monkeypatch,
        {CancelRemoteSubscription: cancel_fails_once, RetrieveSubscription: canceled_remote},
    )

    # Em modo eager, a repetição não roda sozinha: a `Retry` chega a quem disparou a tarefa.
    with pytest.raises(Retry) as retry_exc, django_capture_on_commit_callbacks(execute=True):
        Organizacoes.solicitar_encerramento(organization, ator=owner, agora=moment)
    assert retry_exc.value.when == 30

    # Segunda tentativa, como o worker faria ao consumir a repetição.
    cancel_for_closed_organization(organization.pk)

    cancellations = [command for command in gateway.commands if isinstance(command, CancelRemoteSubscription)]
    assert len(cancellations) == 2
    assert {command.idempotency_key for command in cancellations} == {f"billing:close:{organization.pk}"}
    assert {command.timing for command in cancellations} == {CancellationTiming.IMMEDIATELY}
