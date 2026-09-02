from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock, call

from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import override_settings
from django.urls import reverse

import pytest
import stripe
from django_checkouts.enums import CheckoutMode, CheckoutStatus, EventType, Gateway, InvoiceStatus, ResourceKind, SetupStatus, SubscriptionStatus
from django_checkouts.exceptions import ConfigurationError, GatewayProtocolError, WebhookVerificationError
from django_checkouts.gateways.stripe.mapping import EVENT_MAP, SETUP_EVENT_MAP
from django_checkouts.registry import GATEWAY_CACHE
from django_checkouts.types import Checkout, WebhookEvent

from apps.api.core.route_markers import MARCADOR_PUBLICA, rota_tem_marcador
from apps.assinaturas.subapps.faturamento.checkouts import criar_referencia_checkout
from apps.assinaturas.subapps.faturamento.events import (
    AssinaturaWebhookInvalida,
    ColisaoEventoCobranca,
    DestinoEvento,
    EventosCobranca,
    EventoWebhookInvalido,
    RepositorioEventosPostgres,
    hash_payload_evento,
    resolver_destino_evento,
)
from apps.assinaturas.subapps.faturamento.models import (
    AssinaturaGateway,
    CheckoutCobranca,
    EventoCobranca,
    FinalidadeCheckout,
    StatusCheckout,
    StatusEventoCobranca,
)
from apps.assinaturas.tests.test_subscription_models import _criar_assinatura, _criar_versao
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Organizacao


@dataclass
class RepositorioMemoria:
    eventos: dict[tuple[str, str], SimpleNamespace]
    proximo_id: int = 1

    def receber(self, dados):
        chave = (dados.variante, dados.identificador_evento)
        existente = self.eventos.get(chave)
        if existente is not None:
            return existente, False
        evento = SimpleNamespace(id=self.proximo_id, organizacao_id=None, status=dados.status, hash_payload=dados.hash_payload)
        self.proximo_id += 1
        self.eventos[chave] = evento
        return evento, True

    def rotear(self, evento, destino):
        evento.organizacao_id = destino.organizacao_id
        evento.status = StatusEventoCobranca.ROTEADO
        return evento


def checkout_evento(*, event_id="evt_1", reference_id="referencia", amount=1000, raw=None):
    recurso = Checkout(
        external_id="cs_1",
        gateway=Gateway.STRIPE,
        variant="stripe",
        status=CheckoutStatus.PAID,
        mode=CheckoutMode.SUBSCRIPTION,
        url=None,
        amount_total=amount,
        currency="brl",
        customer=None,
        reference_id=reference_id,
        subscription_id="sub_1",
        expires_at=None,
        created_at=None,
        raw=raw or {},
    )
    return WebhookEvent(
        gateway=Gateway.STRIPE,
        variant="stripe",
        event_id=event_id,
        event_type="checkout.session.completed",
        type=EventType.CHECKOUT_PAID,
        occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
        resource_kind=ResourceKind.CHECKOUT,
        resource_id="cs_1",
        resource=recurso,
        livemode=False,
        raw=raw or {},
    )


def test_hash_canonico_ignora_raw_e_ordem_e_muda_com_fato_relevante():
    primeiro = EventosCobranca.normalizar(checkout_evento(raw={"card": "4242", "x": 1}))
    segundo = EventosCobranca.normalizar(checkout_evento(raw={"x": 2, "email": "secret@example.com"}))
    diferente = EventosCobranca.normalizar(checkout_evento(amount=1001))

    assert primeiro.payload == {"amount": 1000, "currency": "BRL", "payment_status": "paid", "customer_reference": "referencia"}
    assert hash_payload_evento(primeiro) == hash_payload_evento(segundo)
    assert hash_payload_evento(primeiro) != hash_payload_evento(diferente)


@pytest.mark.parametrize(
    ("tipo", "recurso", "resource_kind", "ids", "payload"),
    [
        (
            EventType.SETUP_COMPLETED,
            SimpleNamespace(external_id="set_1", reference_id="ref", status=SetupStatus.COMPLETE),
            ResourceKind.SETUP,
            ("", "set_1", ""),
            {"customer_reference": "ref", "payment_status": "complete"},
        ),
        (
            EventType.SUBSCRIPTION_UPDATED,
            SimpleNamespace(external_id="sub_1", status=SubscriptionStatus.ACTIVE, current_period_start=None, current_period_end=None),
            ResourceKind.SUBSCRIPTION,
            ("sub_1", "", ""),
            {"subscription_status": "active"},
        ),
        (
            EventType.INVOICE_PAID,
            SimpleNamespace(external_id="in_1", subscription_id="sub_1", status=InvoiceStatus.PAID, currency="brl", reference_id=None),
            ResourceKind.INVOICE,
            ("sub_1", "", "in_1"),
            {"currency": "BRL", "invoice_status": "paid"},
        ),
    ],
)
def test_normaliza_setup_assinatura_e_fatura_sem_raw(tipo, recurso, resource_kind, ids, payload):
    evento = WebhookEvent(
        gateway=Gateway.STRIPE,
        variant="stripe",
        event_id=f"evt_{recurso.external_id}",
        event_type=str(tipo),
        type=tipo,
        occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
        resource_kind=resource_kind,
        resource_id=recurso.external_id,
        resource=recurso,
        livemode=False,
        raw={"email": "secret@example.com", "card": "4242"},
    )

    normalizado = EventosCobranca.normalizar(evento)

    assert (normalizado.identificador_assinatura, normalizado.identificador_checkout, normalizado.identificador_fatura) == ids
    assert normalizado.payload == payload


def test_receber_deduplica_hash_igual_e_enfileira_apenas_novo_roteado(monkeypatch):
    repositorio = RepositorioMemoria({})
    enqueue = Mock()
    destino = SimpleNamespace(organizacao_id=7, assinatura_id=11)
    servico = EventosCobranca(repositorio=repositorio, resolver_destino=lambda evento: destino, enqueue=enqueue)

    monkeypatch.setattr(transaction, "on_commit", lambda callback: callback())
    primeiro = servico.receber("stripe", checkout_evento(), client=SimpleNamespace())
    segundo = servico.receber("stripe", checkout_evento(raw={"ignorado": True}), client=SimpleNamespace())

    assert primeiro.novo is True
    assert segundo.novo is False
    enqueue.assert_called_once_with(primeiro.evento_id, "stripe", destino.organizacao_id)


def test_receber_recusa_colisao_sem_sobrescrever_ou_reagendar(monkeypatch):
    repositorio = RepositorioMemoria({})
    enqueue = Mock()
    destino = SimpleNamespace(organizacao_id=7, assinatura_id=11)
    servico = EventosCobranca(repositorio=repositorio, resolver_destino=lambda evento: destino, enqueue=enqueue)
    monkeypatch.setattr(transaction, "on_commit", lambda callback: callback())
    servico.receber("stripe", checkout_evento(), client=SimpleNamespace())

    with pytest.raises(ColisaoEventoCobranca):
        servico.receber("stripe", checkout_evento(amount=1001), client=SimpleNamespace())

    assert len(repositorio.eventos) == 1
    enqueue.assert_called_once()


def test_tipo_desconhecido_e_ignorado_sem_enqueue():
    evento = WebhookEvent(
        gateway=Gateway.STRIPE,
        variant="stripe",
        event_id="evt_unknown",
        event_type="customer.tax_id.updated",
        type=None,
        occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
        resource_kind=None,
        resource_id=None,
        resource=None,
        livemode=False,
        raw={"email": "secret@example.com"},
    )
    repositorio = RepositorioMemoria({})
    enqueue = Mock()
    resultado = EventosCobranca(repositorio=repositorio, resolver_destino=lambda evento: None, enqueue=enqueue).receber(
        "stripe", evento, client=SimpleNamespace()
    )

    salvo = repositorio.eventos[("stripe", "evt_unknown")]
    assert salvo.status == StatusEventoCobranca.IGNORADO
    assert EventosCobranca.normalizar(evento).tipo == "customer.tax_id.updated"
    assert resultado.novo is True
    enqueue.assert_not_called()


def test_metricas_usam_apenas_variante_resultado_e_familia_fechada(monkeypatch):
    evento = WebhookEvent(
        gateway=Gateway.STRIPE,
        variant="stripe",
        event_id="evt_metric",
        event_type="customer.secret.updated",
        type=None,
        occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
        resource_kind=None,
        resource_id=None,
        resource=None,
        livemode=False,
        raw={},
    )
    labels = Mock()
    labels.return_value.inc = Mock()
    monkeypatch.setattr("apps.assinaturas.subapps.faturamento.events.WEBHOOKS_TOTAL.labels", labels)

    EventosCobranca(repositorio=RepositorioMemoria({})).receber("stripe", evento, client=SimpleNamespace())

    assert labels.call_args_list == [
        call("stripe", "received", "unknown"),
        call("stripe", "ignored", "unknown"),
    ]


def test_tipos_remotos_desconhecidos_distintos_colidem_e_tipo_inseguro_falha():
    primeiro = WebhookEvent(
        gateway=Gateway.STRIPE,
        variant="stripe",
        event_id="evt_unknown_collision",
        event_type="customer.tax_id.updated",
        type=None,
        occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
        resource_kind=None,
        resource_id=None,
        resource=None,
        livemode=False,
        raw={},
    )
    campos = {campo: getattr(primeiro, campo) for campo in primeiro.__dataclass_fields__ if campo != "event_type"}
    segundo = WebhookEvent(**campos, event_type="customer.discount.updated")
    repositorio = RepositorioMemoria({})
    servico = EventosCobranca(repositorio=repositorio)

    servico.receber("stripe", primeiro, client=SimpleNamespace())
    with pytest.raises(ColisaoEventoCobranca):
        servico.receber("stripe", segundo, client=SimpleNamespace())

    object.__setattr__(segundo, "event_id", "evt_unknown_inseguro")
    object.__setattr__(segundo, "event_type", "customer email\nupdated")
    with pytest.raises(EventoWebhookInvalido):
        servico.receber("stripe", segundo, client=SimpleNamespace())


@pytest.mark.parametrize(
    ("tipo", "kind", "status_remoto"),
    [
        (EventType.CHECKOUT_PAID, ResourceKind.INVOICE, InvoiceStatus.PAID),
        (EventType.CHECKOUT_PAID, ResourceKind.CHECKOUT, InvoiceStatus.PAID),
        (EventType.INVOICE_OPENED, ResourceKind.INVOICE, "future_valid_status"),
    ],
)
def test_matriz_fechada_rejeita_kind_classe_ou_status_futuro(tipo, kind, status_remoto):
    recurso = SimpleNamespace(
        external_id="resource_1",
        status=status_remoto,
        amount_total=100,
        currency="BRL",
        reference_id=None,
        subscription_id="sub_1",
        current_period_start=None,
        current_period_end=None,
    )
    evento = WebhookEvent(
        gateway=Gateway.STRIPE,
        variant="stripe",
        event_id="evt_protocol",
        event_type=str(tipo),
        type=tipo,
        occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
        resource_kind=kind,
        resource_id="resource_1",
        resource=recurso,
        livemode=False,
        raw={},
    )
    repositorio = RepositorioMemoria({})

    with pytest.raises(EventoWebhookInvalido):
        EventosCobranca(repositorio=repositorio, resolver_destino=lambda evento: None).receber("stripe", evento, client=SimpleNamespace())

    assert repositorio.eventos == {}


@pytest.mark.parametrize(
    ("remote_type", "tipo"),
    [(remote_type, tipo) for remote_type, (tipo, kind) in EVENT_MAP.items() if kind == ResourceKind.CHECKOUT],
)
def test_taxonomia_checkout_aceita_evento_historico_com_snapshot_atual_pago(remote_type, tipo):
    evento = checkout_evento(event_id=f"evt_{remote_type}")
    object.__setattr__(evento, "event_type", remote_type)
    object.__setattr__(evento, "type", tipo)

    normalizado = EventosCobranca.normalizar(evento)

    assert normalizado.tipo == tipo
    assert normalizado.payload["payment_status"] == CheckoutStatus.PAID


@pytest.mark.parametrize(
    ("tipo", "kind", "status"),
    [
        (EventType.INVOICE_PAYMENT_FAILED, ResourceKind.INVOICE, InvoiceStatus.PAID),
        (SETUP_EVENT_MAP["checkout.session.async_payment_failed"], ResourceKind.SETUP, SetupStatus.COMPLETE),
        (EventType.SUBSCRIPTION_CANCELED, ResourceKind.SUBSCRIPTION, SubscriptionStatus.ACTIVE),
    ],
)
def test_familias_aceitam_snapshot_atual_valido_independente_da_semantica_historica(tipo, kind, status):
    recurso = SimpleNamespace(
        external_id="resource_current",
        status=status,
        amount_total=100,
        currency="BRL",
        reference_id=None,
        subscription_id="sub_1",
        current_period_start=None,
        current_period_end=None,
    )
    evento = WebhookEvent(
        gateway=Gateway.STRIPE,
        variant="stripe",
        event_id=f"evt_{tipo}",
        event_type=str(tipo),
        type=tipo,
        occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
        resource_kind=kind,
        resource_id=recurso.external_id,
        resource=recurso,
        livemode=False,
        raw={},
    )

    assert EventosCobranca.normalizar(evento).tipo == tipo


def test_evento_conhecido_sem_recurso_e_falha_de_protocolo_sem_persistir():
    evento = checkout_evento()
    object.__setattr__(evento, "resource", None)
    repositorio = RepositorioMemoria({})

    with pytest.raises(EventoWebhookInvalido):
        EventosCobranca(repositorio=repositorio).receber("stripe", evento, client=SimpleNamespace())

    assert repositorio.eventos == {}


def test_verificacao_acontece_antes_do_repositorio():
    repositorio = Mock()
    client = SimpleNamespace(webhooks=SimpleNamespace(verify=Mock(side_effect=WebhookVerificationError("invalida"))))

    with pytest.raises(AssinaturaWebhookInvalida):
        EventosCobranca(repositorio=repositorio).receber_bytes("stripe", b"raw", {"Stripe-Signature": "sig"}, client=client)

    repositorio.receber.assert_not_called()


def test_falha_de_protocolo_da_biblioteca_nao_persiste():
    repositorio = Mock()
    client = SimpleNamespace(webhooks=SimpleNamespace(verify=Mock(side_effect=GatewayProtocolError("status", gateway="stripe", variant="stripe"))))

    with pytest.raises(EventoWebhookInvalido):
        EventosCobranca(repositorio=repositorio).receber_bytes("stripe", b"raw", {}, client=client)

    repositorio.receber.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_on_commit_nao_enfileira_quando_transacao_reverte():
    repositorio = RepositorioMemoria({})
    enqueue = Mock()
    destino = SimpleNamespace(organizacao_id=7, assinatura_id=11)
    servico = EventosCobranca(repositorio=repositorio, resolver_destino=lambda evento: destino, enqueue=enqueue)

    def executar_e_reverter():
        with transaction.atomic():
            servico.receber("stripe", checkout_evento(), client=SimpleNamespace())
            raise RuntimeError("rollback")

    with pytest.raises(RuntimeError):
        executar_e_reverter()

    enqueue.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_repositorio_ingresso_deduplica_evento_roteado_sem_grants_diretos():
    organizacao = Organizacao.objects.create(nome="Webhook", slug="webhook-ingresso")
    dados = EventosCobranca.normalizar(checkout_evento(reference_id="referencia-opaca"))
    repositorio = RepositorioEventosPostgres()

    recebido, novo = repositorio.receber(dados)
    roteado = repositorio.rotear(recebido, SimpleNamespace(organizacao_id=organizacao.pk, assinatura_id=1))
    duplicado, novo_duplicado = repositorio.receber(dados)

    assert novo is True
    assert novo_duplicado is False
    assert duplicado == roteado
    with organizacao_atual_privilegiada(organizacao.pk):
        persistido = EventoCobranca.objects.get(pk=roteado.id)
        assert persistido.payload_normalizado == {
            "amount": 1000,
            "currency": "BRL",
            "customer_reference": "referencia-opaca",
            "payment_status": "paid",
        }
        assert persistido.status == StatusEventoCobranca.ROTEADO


def test_rota_webhook_e_publica_e_sem_schema_de_payload():
    assert rota_tem_marcador("/faturamento/webhooks/stripe/", "POST", MARCADOR_PUBLICA)


@pytest.mark.django_db(transaction=True)
def test_roteia_referencia_assinada_e_recusa_adulteracao_ou_mapeamento_incoerente():
    organizacao = Organizacao.objects.create(nome="Rota", slug="webhook-rota")
    outra = Organizacao.objects.create(nome="Outra", slug="webhook-outra")
    assinatura = _criar_assinatura(organizacao, _criar_versao(codigo="webhook-rota"))
    outra_assinatura = _criar_assinatura(outra, _criar_versao(codigo="webhook-outra"))
    with organizacao_atual_privilegiada(organizacao.pk):
        checkout = CheckoutCobranca.objects.create(
            organizacao=organizacao,
            assinatura=assinatura,
            finalidade=FinalidadeCheckout.FORMA_PAGAMENTO,
            status=StatusCheckout.ABERTO,
            chave_idempotencia="webhook-rota",
            variante="stripe",
            valor_esperado_centavos=0,
            moeda_esperada="BRL",
        )
    referencia = criar_referencia_checkout(checkout.pk, organizacao.pk)
    evento = checkout_evento(reference_id=referencia)
    object.__setattr__(evento, "resource_kind", ResourceKind.SETUP)

    assert resolver_destino_evento(evento) is None
    object.__setattr__(evento, "variant", "stripe-outro")
    assert resolver_destino_evento(evento) is None
    object.__setattr__(evento, "variant", "stripe")
    with organizacao_atual_privilegiada(organizacao.pk):
        checkout.identificador_externo = "cs_remoto_esperado"
        checkout.save(update_fields=["identificador_externo", "last_modified_at"])
    assert resolver_destino_evento(evento) is None
    object.__setattr__(evento.resource, "external_id", "cs_remoto_esperado")
    object.__setattr__(evento, "resource_id", "cs_remoto_esperado")
    assert resolver_destino_evento(evento) == DestinoEvento(organizacao_id=organizacao.pk, assinatura_id=assinatura.pk)
    object.__setattr__(evento, "resource_kind", ResourceKind.CHECKOUT)
    assert resolver_destino_evento(evento) is None
    object.__setattr__(evento, "resource_kind", ResourceKind.SETUP)
    AssinaturaGateway.objects.create(
        assinatura=assinatura,
        organizacao=organizacao,
        variante="stripe",
        identificador_externo="sub_1",
    )
    object.__setattr__(evento.resource, "reference_id", referencia + "x")
    assert resolver_destino_evento(evento) is None

    object.__setattr__(evento.resource, "reference_id", referencia)
    object.__setattr__(evento.resource, "subscription_id", "sub_conflitante")
    AssinaturaGateway.objects.create(
        assinatura=outra_assinatura,
        organizacao=outra,
        variante="stripe",
        identificador_externo="sub_conflitante",
    )
    assert resolver_destino_evento(evento) is None


@pytest.mark.django_db
def test_endpoint_rejeita_assinatura_invalida_sem_persistir(api_client, monkeypatch):
    client = SimpleNamespace(webhooks=SimpleNamespace(verify=Mock(side_effect=WebhookVerificationError("invalida"))))
    monkeypatch.setattr("apps.assinaturas.subapps.faturamento.events.get_checkout_gateway", lambda variante: client)

    resposta = api_client.post(
        reverse("webhook-faturamento", kwargs={"variante": "stripe"}),
        data=b'{"card":"4242"}',
        content_type="application/json",
        HTTP_STRIPE_SIGNATURE="sig-invalida",
    )

    assert resposta.status_code == 400
    assert resposta.json()["errors"][0]["code"] == "billing.webhook_signature_invalid"
    assert not EventoCobranca._base_manager.exists()


@pytest.mark.django_db
def test_endpoint_rejeita_variante_nao_configurada(api_client, monkeypatch):
    monkeypatch.setattr(
        "apps.assinaturas.subapps.faturamento.events.get_checkout_gateway",
        Mock(side_effect=ConfigurationError("ausente")),
    )

    resposta = api_client.post(reverse("webhook-faturamento", kwargs={"variante": "desconhecida"}), data=b"{}", content_type="application/json")

    assert resposta.status_code == 400
    assert resposta.json()["errors"][0]["code"] == "billing.webhook_variant_invalid"


@pytest.mark.django_db(transaction=True)
@override_settings(
    CHECKOUT_VARIANTS={
        "stripe": (
            "django_checkouts.gateways.stripe.StripeGateway",
            {"api_key": "sk_test", "webhook_secret": "whsec_test", "sandbox": True},
        )
    }
)
def test_endpoint_stripe_verifica_bytes_e_persiste_invoice_sanitizada(api_client, monkeypatch):
    payload = {
        "id": "evt_invoice_endpoint",
        "object": "event",
        "type": "invoice.paid",
        "created": 1785542500,
        "livemode": False,
        "data": {
            "object": {
                "id": "in_endpoint",
                "object": "invoice",
                "status": "paid",
                "billing_reason": "subscription_cycle",
                "subscription": "sub_sem_mapping",
                "customer": "cus_42",
                "amount_due": 7300,
                "amount_paid": 7300,
                "amount_remaining": 0,
                "currency": "brl",
                "lines": {"object": "list", "data": [], "has_more": False},
                "due_date": None,
                "status_transitions": {"paid_at": 1785542500},
                "next_payment_attempt": None,
                "attempt_count": 1,
                "hosted_invoice_url": "https://invoice.stripe.test/in_endpoint",
                "metadata": {"email": "secret@example.com"},
            }
        },
    }
    construir = Mock(return_value=payload)
    monkeypatch.setattr(stripe.Webhook, "construct_event", construir)
    GATEWAY_CACHE.clear()
    body = b'{"bytes":"exatos","card":"4242"}'

    resposta = api_client.post(
        reverse("webhook-faturamento", kwargs={"variante": "stripe"}),
        data=body,
        content_type="application/json",
        HTTP_STRIPE_SIGNATURE="t=1,v1=assinatura",
    )

    assert resposta.status_code == 200
    construir.assert_called_once_with(payload=body, sig_header="t=1,v1=assinatura", secret="whsec_test")
    with transaction.atomic(), transaction.get_connection().cursor() as cursor:
        cursor.execute("SET LOCAL ROLE billing_functions_owner")
        cursor.execute("SELECT payload_normalizado,status FROM evento_cobranca WHERE identificador_evento=%s", ["evt_invoice_endpoint"])
        salvo = cursor.fetchone()
    assert (json.loads(salvo[0]), salvo[1]) == ({"currency": "BRL", "invoice_status": "paid"}, StatusEventoCobranca.RECEBIDO)


@pytest.mark.django_db(transaction=True)
def test_migration_ingresso_reverte_reaplica_e_preserva_acl_minima():
    def catalogo():
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT p.proname,p.prosecdef,r.rolname,
                          has_function_privilege('public',p.oid,'EXECUTE'),
                          has_function_privilege('billing_ingress_runtime',p.oid,'EXECUTE'),p.proconfig
                   FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
                   WHERE p.oid IN (
                     to_regprocedure('faturamento_ingress_evento(text,text,text,text,text,text,boolean,jsonb,text,timestamptz)'),
                     to_regprocedure('faturamento_rotear_evento_destino(bigint,bigint)')) ORDER BY p.proname"""
            )
            funcoes = cursor.fetchall()
            cursor.execute(
                """SELECT count(*) FROM information_schema.role_table_grants
                   WHERE grantee='billing_ingress_runtime' AND table_schema='public'"""
            )
            grants_diretos = cursor.fetchone()[0]
            cursor.execute(
                """SELECT proname FROM pg_proc WHERE pronamespace='public'::regnamespace
                   AND proname IN ('faturamento_receber_evento','faturamento_rotear_evento',
                                   'faturamento_ingress_evento','faturamento_rotear_evento_destino') ORDER BY proname"""
            )
            nomes = [linha[0] for linha in cursor.fetchall()]
        return funcoes, grants_diretos, nomes

    executor = MigrationExecutor(connection)
    folhas = executor.loader.graph.leaf_nodes()
    try:
        estado = catalogo()
        assert estado[1] == 0
        assert estado[2] == ["faturamento_ingress_evento", "faturamento_rotear_evento_destino"]
        assert [(linha[0], *linha[1:5]) for linha in estado[0]] == [
            ("faturamento_ingress_evento", True, "billing_functions_owner", False, True),
            ("faturamento_rotear_evento_destino", True, "billing_functions_owner", False, True),
        ]
        assert all(linha[5] == ["search_path=pg_catalog, pg_temp"] for linha in estado[0])
        executor.migrate([("faturamento", "0003_checkout_erro_codigo")])
        assert catalogo() == ([], 0, ["faturamento_receber_evento", "faturamento_rotear_evento"])
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE billing_ingress_runtime")
            cursor.execute("SELECT set_config('rls.tenant_id','0',true)")
            cursor.execute("SELECT set_config('rls.billing_ingress','1',true)")
            cursor.execute(
                "SELECT faturamento_receber_evento(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                ["stripe", "evt-interface-legacy", "invoice.paid", "", "", "in_legacy", True, "{}", "d" * 64, None],
            )
            assert cursor.fetchone() == (True,)
        MigrationExecutor(connection).migrate([("faturamento", "0004_ingestao_eventos")])
        assert catalogo() == estado
    finally:
        MigrationExecutor(connection).migrate(folhas)
