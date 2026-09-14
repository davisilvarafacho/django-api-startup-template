"""Encerramento imediato, agendado e idempotente de organizações."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event, Lock
from time import monotonic, sleep
from unittest.mock import patch

from django.db import close_old_connections, connections
from django.utils import timezone

import pytest
from django_rls.context import get_active_rls_context

from apps.api.autenticacao.models import AuthToken, TokenMetaData, TokenType
from apps.api.core.errors import APIError
from apps.organizacoes.memberships import Vinculos
from apps.organizacoes.models import Convite, Organizacao, Papel, Vinculo
from apps.organizacoes.organizations import (
    EncerramentoAgendado,
    EncerramentoEfetivado,
    EncerramentoSemAlteracao,
    Organizacoes,
    TermoEncerramentoAgendado,
    TermoEncerramentoImediato,
)
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db


class AssinaturasEncerramentoTeste:
    termo = TermoEncerramentoImediato()
    chamadas = []
    lock = Lock()

    @classmethod
    def solicitar_encerramento(cls, organizacao, *, agora):
        return cls.termo

    @classmethod
    def cancelar_encerramento(cls, organizacao):
        return None

    @classmethod
    def encerrar(cls, organizacao, *, encerrada_em):
        with cls.lock:
            cls.chamadas.append(
                {
                    "organizacao_id": organizacao.pk,
                    "encerrada_em": encerrada_em,
                    "contexto_rls": dict(get_active_rls_context()),
                }
            )


@pytest.fixture(autouse=True)
def _limpar_chamadas():
    AssinaturasEncerramentoTeste.termo = TermoEncerramentoImediato()
    AssinaturasEncerramentoTeste.chamadas.clear()


def _solicitar_encerramento(organizacao, termo, *, assinaturas=AssinaturasEncerramentoTeste, **kwargs):
    assinaturas.termo = termo
    return Organizacoes.solicitar_encerramento(organizacao, assinaturas=assinaturas, **kwargs)


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


def test_termo_imediato_encerra_contrato_e_revoga_acessos_sem_excluir_usuarios():
    agora = timezone.now()
    proprietario, organizacao, vinculos, convite, api_key, sessao = _cenario_encerramento("imediato")

    resultado = _solicitar_encerramento(
        organizacao,
        TermoEncerramentoImediato(),
        assinaturas=AssinaturasEncerramentoTeste,
        ator=proprietario,
        agora=agora,
    )

    organizacao = Organizacao.all_objects.get(pk=organizacao.pk)
    api_key.refresh_from_db()
    sessao.refresh_from_db()
    convite.refresh_from_db()
    assert isinstance(resultado, EncerramentoEfetivado)
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
    assert AssinaturasEncerramentoTeste.chamadas == [
        {
            "organizacao_id": organizacao.pk,
            "encerrada_em": agora,
            "contexto_rls": {"tenant_id": str(organizacao.pk)},
        }
    ]


def test_termo_agendado_preserva_acesso_ate_o_fim_do_periodo():
    agora = timezone.now()
    fim_periodo = agora + timedelta(days=20)
    _, organizacao, vinculos, convite, api_key, _ = _cenario_encerramento("agendado")

    resultado = _solicitar_encerramento(
        organizacao,
        TermoEncerramentoAgendado(agendado_para=fim_periodo),
        assinaturas=AssinaturasEncerramentoTeste,
        agora=agora,
    )

    organizacao.refresh_from_db()
    api_key.refresh_from_db()
    convite.refresh_from_db()
    assert resultado == EncerramentoAgendado(agendado_para=fim_periodo)
    assert organizacao.encerramento_solicitado_em == agora
    assert organizacao.encerramento_agendado_para == fim_periodo
    assert organizacao.is_active is True
    assert organizacao.is_deleted is False
    assert Vinculo.objects.filter(pk__in=[item.pk for item in vinculos], is_active=True).count() == 2
    assert convite.is_deleted is False
    assert api_key.revoked_at is None
    assert AssinaturasEncerramentoTeste.chamadas == []


def test_servico_revalida_proprietario_antes_de_solicitar_encerramento():
    agora = timezone.now()
    _, organizacao, _, _, _, _ = _cenario_encerramento("ator-rebaixado")
    ator = criar_usuario(email="admin-ator-rebaixado@example.com", email_verificado_em=agora)
    Vinculo.objects.create(organizacao=organizacao, usuario=ator, papel=Papel.ADMINISTRADOR)

    with pytest.raises(APIError) as erro:
        _solicitar_encerramento(
            organizacao,
            TermoEncerramentoAgendado(agendado_para=agora + timedelta(days=10)),
            ator=ator,
            agora=agora,
        )

    assert erro.value.code == "organizations.role_insufficient"
    organizacao.refresh_from_db()
    assert organizacao.encerramento_solicitado_em is None


def test_repetir_agendamento_devolve_resultado_idempotente_com_a_data_existente():
    agora = timezone.now()
    fim_periodo = agora + timedelta(days=20)
    _, organizacao, _, _, _, _ = _cenario_encerramento("agendamento-idempotente")

    primeiro = _solicitar_encerramento(
        organizacao,
        TermoEncerramentoAgendado(agendado_para=fim_periodo),
        assinaturas=AssinaturasEncerramentoTeste,
        agora=agora,
    )
    segundo = _solicitar_encerramento(
        organizacao,
        TermoEncerramentoAgendado(agendado_para=fim_periodo + timedelta(days=10)),
        assinaturas=AssinaturasEncerramentoTeste,
        agora=agora + timedelta(minutes=1),
    )

    assert primeiro == EncerramentoAgendado(agendado_para=fim_periodo)
    assert segundo == EncerramentoSemAlteracao(agendado_para=fim_periodo)


def test_repetir_encerramento_imediato_devolve_resultado_idempotente_sem_data():
    _, organizacao, _, _, _, _ = _cenario_encerramento("imediato-idempotente")

    primeiro = _solicitar_encerramento(
        organizacao,
        TermoEncerramentoImediato(),
        assinaturas=AssinaturasEncerramentoTeste,
    )
    segundo = _solicitar_encerramento(
        organizacao,
        TermoEncerramentoImediato(),
        assinaturas=AssinaturasEncerramentoTeste,
    )

    assert isinstance(primeiro, EncerramentoEfetivado)
    assert segundo == EncerramentoSemAlteracao(agendado_para=None)


def test_termo_desconhecido_falha_fechado_sem_registrar_encerramento():
    _, organizacao, _, _, _, _ = _cenario_encerramento("termo-desconhecido")

    with pytest.raises(TypeError, match="TermoEncerramento"):
        _solicitar_encerramento(
            organizacao,
            object(),
            assinaturas=AssinaturasEncerramentoTeste,
        )

    organizacao.refresh_from_db()
    assert organizacao.encerramento_solicitado_em is None
    assert organizacao.encerramento_agendado_para is None
    assert AssinaturasEncerramentoTeste.chamadas == []


def test_cancelar_remove_agendamento_e_impede_efetivacao_posterior():
    agora = timezone.now()
    fim_periodo = agora + timedelta(days=20)
    _, organizacao, _, _, api_key, _ = _cenario_encerramento("cancelado")
    _solicitar_encerramento(
        organizacao,
        TermoEncerramentoAgendado(agendado_para=fim_periodo),
        assinaturas=AssinaturasEncerramentoTeste,
        agora=agora,
    )

    cancelado = Organizacoes.cancelar_encerramento(organizacao, assinaturas=AssinaturasEncerramentoTeste)
    efetivado = Organizacoes.efetivar_encerramento(
        organizacao.pk,
        assinaturas=AssinaturasEncerramentoTeste,
        agora=fim_periodo,
    )

    organizacao.refresh_from_db()
    api_key.refresh_from_db()
    assert cancelado is True
    assert efetivado is False
    assert organizacao.encerramento_solicitado_em is None
    assert organizacao.encerramento_agendado_para is None
    assert organizacao.is_active is True
    assert api_key.revoked_at is None
    assert AssinaturasEncerramentoTeste.chamadas == []


def test_falha_ao_encerrar_assinatura_reverte_toda_a_efetivacao():
    agora = timezone.now()
    proprietario, organizacao, vinculos, convite, api_key, _ = _cenario_encerramento("rollback")

    class AssinaturasComFalha(AssinaturasEncerramentoTeste):
        @classmethod
        def encerrar(cls, organizacao, *, encerrada_em):
            raise RuntimeError("assinatura")

    with pytest.raises(RuntimeError, match="assinatura"):
        _solicitar_encerramento(
            organizacao,
            TermoEncerramentoImediato(),
            assinaturas=AssinaturasComFalha,
            ator=proprietario,
            agora=agora,
        )

    organizacao.refresh_from_db()
    api_key.refresh_from_db()
    convite.refresh_from_db()
    assert organizacao.encerramento_solicitado_em is None
    assert organizacao.encerramento_agendado_para is None
    assert organizacao.is_active is True
    assert organizacao.is_deleted is False
    assert Vinculo.objects.filter(pk__in=[item.pk for item in vinculos], is_active=True).count() == 2
    assert convite.is_deleted is False
    assert api_key.revoked_at is None


def test_rollback_depois_da_revogacao_nao_publica_auditoria_de_api_key(monkeypatch, django_capture_on_commit_callbacks):
    agora = timezone.now()
    proprietario, organizacao, _, _, api_key, _ = _cenario_encerramento("rollback-auditoria")

    def falhar_cancelamento(self, using=None, keep_parents=False):
        raise RuntimeError("convite")

    monkeypatch.setattr(Convite, "delete", falhar_cancelamento)
    with patch("apps.api.autenticacao.audit.capture") as capture_mock:
        with pytest.raises(RuntimeError, match="convite"), django_capture_on_commit_callbacks(execute=True):
            _solicitar_encerramento(
                organizacao,
                TermoEncerramentoImediato(),
                assinaturas=AssinaturasEncerramentoTeste,
                ator=proprietario,
                agora=agora,
            )

    api_key.refresh_from_db()
    assert api_key.revoked_at is None
    capture_mock.assert_not_called()


def test_organizacao_pendente_nao_cria_nem_aceita_convite():
    agora = timezone.now()
    proprietario, organizacao, _, convite, _, _ = _cenario_encerramento("bloqueia-convite")
    convidado = criar_usuario(email=convite.email, email_verificado_em=timezone.now())
    _solicitar_encerramento(
        organizacao,
        TermoEncerramentoAgendado(agendado_para=agora + timedelta(days=20)),
        assinaturas=AssinaturasEncerramentoTeste,
        agora=agora,
    )

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
def test_efetivacoes_concorrentes_encerram_uma_unica_vez():
    agora = timezone.now()
    _, organizacao, _, _, api_key, _ = _cenario_encerramento("concorrente")
    _solicitar_encerramento(
        organizacao,
        TermoEncerramentoAgendado(agendado_para=agora),
        assinaturas=AssinaturasEncerramentoTeste,
        agora=agora - timedelta(days=1),
    )

    def efetivar():
        close_old_connections()
        try:
            return Organizacoes.efetivar_encerramento(
                organizacao.pk,
                assinaturas=AssinaturasEncerramentoTeste,
                agora=agora,
            )
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        resultados = list(executor.map(lambda _: efetivar(), range(2)))

    api_key.refresh_from_db()
    assert sorted(resultados) == [False, True]
    assert len(AssinaturasEncerramentoTeste.chamadas) == 1
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
            return _solicitar_encerramento(
                organizacao,
                TermoEncerramentoImediato(),
                assinaturas=AssinaturasEncerramentoTeste,
                agora=agora,
            )
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
        assert isinstance(efetivacao.result(timeout=10), EncerramentoEfetivado)

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
            return _solicitar_encerramento(
                organizacao,
                TermoEncerramentoImediato(),
                assinaturas=AssinaturasEncerramentoTeste,
                agora=agora,
            )
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
        assert isinstance(efetivacao.result(timeout=10), EncerramentoEfetivado)

    assert (
        AuthToken.objects.filter(
            organization=organizacao,
            type=TokenType.API_KEY,
            revoked_at__isnull=True,
        ).exists()
        is False
    )
