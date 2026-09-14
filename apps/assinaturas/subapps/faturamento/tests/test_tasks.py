import inspect
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event, Lock
from types import SimpleNamespace

from django.db import close_old_connections, connection, connections, transaction
from django.utils import timezone

import pytest

from apps.assinaturas.subapps.faturamento import tasks as billing_tasks
from apps.assinaturas.subapps.faturamento.models import (
    AssinaturaGateway,
    EventoCobranca,
    SolicitacaoReconciliacaoCobranca,
    StatusEventoCobranca,
)
from apps.assinaturas.subapps.faturamento.tasks import processar_evento_cobranca, recuperar_eventos_cobranca
from apps.assinaturas.tests.test_subscription_models import _criar_assinatura, _criar_versao
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Organizacao


def _evento_recebido_sem_tenant(*, identificador_assinatura: str, tentativas_roteamento: int = 0) -> int:
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL ROLE billing_functions_owner")
        cursor.execute(
            """INSERT INTO evento_cobranca
               (created_at,last_modified_at,is_active,is_deleted,variante,identificador_evento,tipo,
                identificador_assinatura,identificador_checkout,identificador_fatura,status,exige_tenant,
                tentativas_roteamento,tentativas_processamento,tentativas_automaticas_ciclo,
                payload_normalizado,hash_payload,erro,ocorrido_em)
               VALUES (%s,%s,true,false,'stripe',%s,'subscription.updated',%s,'','',10,true,%s,0,0,
                       '{}'::jsonb,%s,'',%s) RETURNING id""",
            [
                timezone.now(),
                timezone.now(),
                f"evt_recovery_{identificador_assinatura}",
                identificador_assinatura,
                tentativas_roteamento,
                "e" * 64,
                timezone.now(),
            ],
        )
        return cursor.fetchone()[0]


def test_worker_financeiro_nunca_assume_role_owner_e_exige_tenant_na_mensagem():
    fonte = inspect.getsource(processar_evento_cobranca.run)
    assert "billing_functions_owner" not in fonte
    assert processar_evento_cobranca.run(1, "stripe", None) is False


@pytest.mark.django_db(transaction=True)
def test_recovery_rerouteia_recebido_e_set_local_nao_vaza_role(monkeypatch):
    organizacao = Organizacao.objects.create(nome="Recovery", slug="recovery")
    assinatura = _criar_assinatura(organizacao, _criar_versao(codigo="recovery"))
    AssinaturaGateway.objects.create(
        organizacao=organizacao,
        assinatura=assinatura,
        variante="stripe",
        identificador_externo="sub_recovery",
    )
    evento_id = _evento_recebido_sem_tenant(identificador_assinatura="sub_recovery")
    enviados = []
    monkeypatch.setattr(
        "apps.assinaturas.subapps.faturamento.tasks.processar_evento_cobranca.delay",
        lambda *args: enviados.append(args),
    )
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('role', true), current_setting('rls.tenant_id', true)")
        antes = cursor.fetchone()

    assert recuperar_eventos_cobranca.run(limite=10) == 1

    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('role', true), current_setting('rls.tenant_id', true)")
        depois = cursor.fetchone()
    assert depois == antes
    assert enviados == [(evento_id, "stripe", organizacao.pk)]
    with organizacao_atual_privilegiada(organizacao.pk):
        evento = EventoCobranca.objects.get(pk=evento_id)
    assert evento.status == StatusEventoCobranca.ROTEADO
    assert evento.organizacao_id == organizacao.pk


@pytest.mark.django_db(transaction=True)
def test_recovery_sem_destino_mantem_recebido_sem_efeitos(monkeypatch):
    evento_id = _evento_recebido_sem_tenant(identificador_assinatura="sub_desconhecida")
    enviados = []
    monkeypatch.setattr(
        "apps.assinaturas.subapps.faturamento.tasks.processar_evento_cobranca.delay",
        lambda *args: enviados.append(args),
    )

    assert recuperar_eventos_cobranca.run(limite=10) == 0
    assert enviados == []
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL ROLE billing_functions_owner")
        cursor.execute("SELECT status,organizacao_id,tentativas_roteamento,proxima_tentativa_em FROM evento_cobranca WHERE id=%s", [evento_id])
        status, organizacao_id, tentativas, proxima = cursor.fetchone()
        assert (status, organizacao_id, tentativas) == (StatusEventoCobranca.RECEBIDO, None, 1)
        assert proxima is not None


@pytest.mark.django_db(transaction=True)
def test_recovery_arrenda_roteado_antes_do_enqueue_e_nao_duplica_mensagem(monkeypatch):
    organizacao = Organizacao.objects.create(nome="Recovery lease", slug="recovery-lease")
    assinatura = _criar_assinatura(organizacao, _criar_versao(codigo="recovery-lease"))
    AssinaturaGateway.objects.create(
        organizacao=organizacao,
        assinatura=assinatura,
        variante="stripe",
        identificador_externo="sub_recovery_lease",
    )
    with organizacao_atual_privilegiada(organizacao.pk):
        evento = EventoCobranca.objects.create(
            organizacao=organizacao,
            variante="stripe",
            identificador_evento="evt_recovery_lease",
            tipo="subscription.updated",
            identificador_assinatura="sub_recovery_lease",
            status=StatusEventoCobranca.ROTEADO,
            hash_payload="f" * 64,
            ocorrido_em=timezone.now(),
        )
    enviados = []
    monkeypatch.setattr("apps.assinaturas.subapps.faturamento.tasks.processar_evento_cobranca.delay", lambda *args: enviados.append(args))

    assert recuperar_eventos_cobranca.run(limite=1) == 1
    assert recuperar_eventos_cobranca.run(limite=1) == 0
    assert enviados == [(evento.pk, "stripe", organizacao.pk)]


@pytest.mark.django_db(transaction=True)
def test_recovery_orfao_no_prefixo_nao_causa_starvation_acima_do_limite(monkeypatch):
    _evento_recebido_sem_tenant(identificador_assinatura="sub_orfao_prefixo")
    organizacao = Organizacao.objects.create(nome="Recovery justo", slug="recovery-justo")
    assinatura = _criar_assinatura(organizacao, _criar_versao(codigo="recovery-justo"))
    AssinaturaGateway.objects.create(
        organizacao=organizacao,
        assinatura=assinatura,
        variante="stripe",
        identificador_externo="sub_recovery_justo",
    )
    evento_id = _evento_recebido_sem_tenant(identificador_assinatura="sub_recovery_justo")
    enviados = []
    monkeypatch.setattr("apps.assinaturas.subapps.faturamento.tasks.processar_evento_cobranca.delay", lambda *args: enviados.append(args))

    assert recuperar_eventos_cobranca.run(limite=1) == 1
    assert enviados == [(evento_id, "stripe", organizacao.pk)]


@pytest.mark.django_db(transaction=True)
def test_recovery_torna_orfao_terminal_ao_esgotar_oito_tentativas(monkeypatch):
    evento_id = _evento_recebido_sem_tenant(identificador_assinatura="sub_orfao_esgotado", tentativas_roteamento=7)
    monkeypatch.setattr("apps.assinaturas.subapps.faturamento.tasks.processar_evento_cobranca.delay", lambda *args: None)

    assert recuperar_eventos_cobranca.run(limite=1) == 0

    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL ROLE billing_functions_owner")
        cursor.execute("SELECT status,tentativas_roteamento,proxima_tentativa_em,erro FROM evento_cobranca WHERE id=%s", [evento_id])
        assert cursor.fetchone() == (StatusEventoCobranca.FALHOU, 8, None, "routing_exhausted")


@pytest.mark.django_db(transaction=True)
def test_task_reconcile_first_reabre_e_enfileira_processador_exato_uma_vez(monkeypatch):
    organizacao = Organizacao.objects.create(nome="Reconcile direcionado", slug="reconcile-direcionado")
    assinatura = _criar_assinatura(organizacao, _criar_versao(codigo="reconcile-direcionado"))
    AssinaturaGateway.objects.create(
        organizacao=organizacao,
        assinatura=assinatura,
        variante="stripe",
        identificador_externo="sub_reconcile_direcionado",
    )
    with organizacao_atual_privilegiada(organizacao.pk):
        evento = EventoCobranca.objects.create(
            organizacao=organizacao,
            variante="stripe",
            identificador_evento="evt_reconcile_direcionado",
            tipo="subscription.updated",
            identificador_assinatura="sub_reconcile_direcionado",
            status=StatusEventoCobranca.FALHOU,
            aguarda_reconciliacao=True,
            hash_payload="c" * 64,
        )
    enviados = []
    monkeypatch.setattr(
        "apps.assinaturas.subapps.faturamento.processing.current_app",
        SimpleNamespace(send_task=lambda *args, **kwargs: enviados.append((args, kwargs))),
    )

    assert billing_tasks.reconciliar_evento_cobranca.run(evento.pk, "stripe", organizacao.pk) is True
    assert billing_tasks.reconciliar_evento_cobranca.run(evento.pk, "stripe", organizacao.pk) is False

    assert enviados == [
        (
            ("faturamento.processar_evento_cobranca",),
            {"args": (evento.pk, "stripe", organizacao.pk)},
        )
    ]


def _solicitacao_operacional(organizacao, ator):
    agora = timezone.now()
    with organizacao_atual_privilegiada(organizacao.pk):
        return SolicitacaoReconciliacaoCobranca.objects.create(
            organizacao=organizacao,
            variante="stripe",
            ator=ator,
            motivo="Diagnóstico operacional",
            chave_idempotencia=f"solicitacao:{organizacao.pk}",
            janela_inicio=agora - timedelta(minutes=10),
            janela_fim=agora,
            parametros={"evento_id": 123, "janela_segundos": 600},
        )


@pytest.mark.django_db(transaction=True)
def test_task_reconciliacao_operacional_usa_janela_auditada_e_registra_contagem(monkeypatch):
    from tests.support.usuarios import criar_usuario

    organizacao = Organizacao.objects.create(nome="Auditoria concluída", slug="auditoria-concluida")
    solicitacao = _solicitacao_operacional(organizacao, criar_usuario(email="auditoria-concluida@example.com"))
    chamadas = []

    class Events:
        def list(self, **kwargs):
            assert connection.in_atomic_block is False
            chamadas.append(kwargs)
            return SimpleNamespace(items=(), next_cursor=None)

    monkeypatch.setattr(billing_tasks, "get_checkout_gateway", lambda variante: SimpleNamespace(events=Events()))

    assert billing_tasks.executar_reconciliacao_operacional.run(solicitacao.pk, organizacao.pk) == 0

    with organizacao_atual_privilegiada(organizacao.pk):
        solicitacao.refresh_from_db()
    assert chamadas == [
        {
            "occurred_since": solicitacao.janela_inicio,
            "occurred_before": solicitacao.janela_fim,
            "cursor": None,
            "limit": 100,
        }
    ]
    assert solicitacao.resultado == "concluida"
    assert solicitacao.parametros == {
        "evento_id": 123,
        "janela_segundos": 600,
        "tentativas_execucao": 1,
        "eventos_ingeridos": 0,
    }


@pytest.mark.django_db(transaction=True)
def test_task_reconciliacao_operacional_registra_falha_sem_persistir_mensagem(monkeypatch):
    from tests.support.usuarios import criar_usuario

    organizacao = Organizacao.objects.create(nome="Auditoria falha", slug="auditoria-falha")
    solicitacao = _solicitacao_operacional(organizacao, criar_usuario(email="auditoria-falha@example.com"))

    class Events:
        def list(self, **kwargs):
            del kwargs
            raise RuntimeError("segredo remoto que não deve persistir")

    monkeypatch.setattr(billing_tasks, "get_checkout_gateway", lambda variante: SimpleNamespace(events=Events()))

    with pytest.raises(RuntimeError, match="segredo remoto"):
        billing_tasks.executar_reconciliacao_operacional.run(solicitacao.pk, organizacao.pk)

    with organizacao_atual_privilegiada(organizacao.pk):
        solicitacao.refresh_from_db()
    assert solicitacao.resultado == "falhou"
    assert solicitacao.parametros == {
        "evento_id": 123,
        "janela_segundos": 600,
        "tentativas_execucao": 1,
        "erro_codigo": "RuntimeError",
    }


@pytest.mark.django_db(transaction=True)
def test_task_reconciliacao_operacional_recupera_execucao_abandonada(monkeypatch):
    from django.conf import settings

    from tests.support.usuarios import criar_usuario

    organizacao = Organizacao.objects.create(nome="Auditoria abandonada", slug="auditoria-abandonada")
    solicitacao = _solicitacao_operacional(organizacao, criar_usuario(email="auditoria-abandonada@example.com"))
    with organizacao_atual_privilegiada(organizacao.pk):
        SolicitacaoReconciliacaoCobranca.objects.filter(pk=solicitacao.pk).update(
            resultado="executando",
            last_modified_at=timezone.now() - timedelta(seconds=settings.CELERY_TASK_TIME_LIMIT + 1),
        )
    chamadas = []

    class Events:
        def list(self, **kwargs):
            chamadas.append(kwargs)
            return SimpleNamespace(items=(), next_cursor=None)

    monkeypatch.setattr(billing_tasks, "get_checkout_gateway", lambda variante: SimpleNamespace(events=Events()))

    assert billing_tasks.executar_reconciliacao_operacional.run(solicitacao.pk, organizacao.pk) == 0

    with organizacao_atual_privilegiada(organizacao.pk):
        solicitacao.refresh_from_db()
    assert len(chamadas) == 1
    assert solicitacao.resultado == "concluida"


@pytest.mark.django_db(transaction=True)
def test_finalizacao_atrasada_nao_sobrescreve_recuperacao_mais_nova(monkeypatch):
    from django.conf import settings

    from tests.support.usuarios import criar_usuario

    organizacao = Organizacao.objects.create(nome="Auditoria concorrente", slug="auditoria-concorrente")
    solicitacao = _solicitacao_operacional(organizacao, criar_usuario(email="auditoria-concorrente@example.com"))
    primeira_iniciou = Event()
    liberar_primeira = Event()
    lock = Lock()
    gateways = 0

    class EventsBloqueados:
        def list(self, **kwargs):
            del kwargs
            primeira_iniciou.set()
            assert liberar_primeira.wait(timeout=10)
            raise RuntimeError("falha tardia")

    class EventsConcluidos:
        def list(self, **kwargs):
            del kwargs
            return SimpleNamespace(items=(), next_cursor=None)

    def gateway(variante):
        nonlocal gateways
        assert variante == "stripe"
        with lock:
            gateways += 1
            numero = gateways
        return SimpleNamespace(events=EventsBloqueados() if numero == 1 else EventsConcluidos())

    monkeypatch.setattr(billing_tasks, "get_checkout_gateway", gateway)

    def executar_primeira():
        close_old_connections()
        try:
            return billing_tasks.executar_reconciliacao_operacional.run(solicitacao.pk, organizacao.pk)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        primeira = pool.submit(executar_primeira)
        assert primeira_iniciou.wait(timeout=10)
        with organizacao_atual_privilegiada(organizacao.pk):
            SolicitacaoReconciliacaoCobranca.objects.filter(pk=solicitacao.pk).update(
                last_modified_at=timezone.now() - timedelta(seconds=settings.CELERY_TASK_TIME_LIMIT + 1)
            )
        segunda = billing_tasks.executar_reconciliacao_operacional.run(solicitacao.pk, organizacao.pk)
        liberar_primeira.set()
        with pytest.raises(RuntimeError, match="falha tardia"):
            primeira.result(timeout=10)

    with organizacao_atual_privilegiada(organizacao.pk):
        solicitacao.refresh_from_db()
    assert segunda == 0
    assert gateways == 2
    assert solicitacao.resultado == "concluida"
    assert solicitacao.parametros == {
        "evento_id": 123,
        "janela_segundos": 600,
        "tentativas_execucao": 2,
        "eventos_ingeridos": 0,
    }
