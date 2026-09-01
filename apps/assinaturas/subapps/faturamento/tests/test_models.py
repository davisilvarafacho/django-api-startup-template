from django.conf import settings
from django.contrib import admin
from django.core.checks import Error
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, connection, connections, models, transaction
from django.test import override_settings

import psycopg2
import pytest

from apps.api.base.models import Base, BaseTenantless
from apps.assinaturas.models import (
    AlteracaoAssinatura,
    MomentoAplicacaoAlteracaoAssinatura,
    PrecoPlano,
    TipoAlteracaoAssinatura,
)
from apps.assinaturas.proposals import Propostas
from apps.assinaturas.subapps.faturamento.checks import configuracao_faturamento_check
from apps.assinaturas.subapps.faturamento.models import (
    AssinaturaGateway,
    CheckoutCobranca,
    ComponentePreco,
    EventoCobranca,
    FaturaAssinatura,
    FinalidadeCheckout,
    ReferenciaPrecoGateway,
    StatusCheckout,
    StatusEventoCobranca,
    StatusFatura,
)
from apps.assinaturas.subapps.faturamento.payloads import normalizar_payload_evento
from apps.assinaturas.tests.test_proposal_models import _criar_proposta
from apps.assinaturas.tests.test_subscription_models import _criar_assinatura, _criar_versao
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Organizacao, Papel, Vinculo
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db

PAPEL_INGRESSO = "billing_ingress_tester"
SENHA_INGRESSO = "billing_ingress_tester"


@pytest.fixture(scope="module")
def papel_ingresso(django_db_setup, django_db_blocker):
    with django_db_blocker.unblock(), connection.cursor() as cursor:
        cursor.execute(f"DROP ROLE IF EXISTS {PAPEL_INGRESSO}")
        cursor.execute(f"CREATE ROLE {PAPEL_INGRESSO} LOGIN PASSWORD %s NOSUPERUSER NOBYPASSRLS NOINHERIT", [SENHA_INGRESSO])
        cursor.execute(f"GRANT billing_ingress_runtime TO {PAPEL_INGRESSO}")
        cursor.execute(f"GRANT USAGE ON SCHEMA public TO {PAPEL_INGRESSO}")
        cursor.execute(f"GRANT SELECT ON checkout_cobranca, fatura_assinatura TO {PAPEL_INGRESSO}")
    yield
    with django_db_blocker.unblock(), connection.cursor() as cursor:
        cursor.execute(f"DROP OWNED BY {PAPEL_INGRESSO}")
        cursor.execute(f"DROP ROLE IF EXISTS {PAPEL_INGRESSO}")


def test_modelos_respeitam_fronteira_global_e_tenantizada():
    assert issubclass(AssinaturaGateway, BaseTenantless)
    assert issubclass(ReferenciaPrecoGateway, BaseTenantless)
    assert issubclass(CheckoutCobranca, Base)
    assert issubclass(FaturaAssinatura, Base)
    assert issubclass(EventoCobranca, BaseTenantless)
    assert EventoCobranca._meta.get_field("organizacao").null is True


def test_enums_persistidos_usam_positive_smallint_e_passos_de_dez():
    assert list(ComponentePreco.values) == [10, 20]
    assert list(FinalidadeCheckout.values) == [10, 20, 30, 40]
    assert list(StatusCheckout.values) == [10, 20, 30, 40, 50, 60, 70]
    assert list(StatusFatura.values) == [10, 20, 30, 40, 50]
    assert list(StatusEventoCobranca.values) == [10, 20, 30, 40, 50, 60]
    for model, field in (
        (CheckoutCobranca, "status"),
        (FaturaAssinatura, "tentativas"),
        (EventoCobranca, "tentativas_roteamento"),
        (EventoCobranca, "tentativas_processamento"),
    ):
        assert isinstance(model._meta.get_field(field), models.PositiveSmallIntegerField)


def test_configuracao_stripe_vem_do_ambiente_e_contexto_ingresso_e_registrado():
    dotted_path, options = settings.CHECKOUT_VARIANTS["stripe"]
    assert dotted_path == "django_checkouts.gateways.stripe.StripeGateway"
    assert set(options) == {"api_key", "webhook_secret", "sandbox"}
    assert "billing_ingress" in settings.DJANGO_RLS["REGISTERED_CONTEXT_KEYS"]


def test_checkout_forma_pagamento_exige_valor_zero():
    checkout = CheckoutCobranca(finalidade=FinalidadeCheckout.FORMA_PAGAMENTO, valor_esperado_centavos=1)
    constraint = next(item for item in CheckoutCobranca._meta.constraints if item.name == "checkout_forma_pagamento_zero")
    with pytest.raises(ValidationError):
        constraint.validate(CheckoutCobranca, checkout, exclude={"organizacao", "assinatura", "chave_idempotencia", "variante", "moeda_esperada"})


def test_evento_nao_expoe_campos_brutos_ou_headers():
    fields = {field.name for field in EventoCobranca._meta.fields}
    assert fields.isdisjoint({"raw", "body", "corpo_bruto", "headers", "token", "signature"})


def test_normalizador_descarta_desconhecidos_pii_secrets_e_nested():
    assert normalizar_payload_evento(
        {
            "amount": 1000,
            "currency": "BRL",
            "email": "pessoa@example.test",
            "token": "secret",
            "raw": {"card": "4242"},
            "headers": {"stripe-signature": "secret"},
            "unknown": ["nested"],
        }
    ) == {"amount": 1000, "currency": "BRL"}


@pytest.mark.parametrize(
    ("chave", "valor"),
    [
        ("amount", -1),
        ("amount", 1.5),
        ("amount", True),
        ("amount", 9_223_372_036_854_775_808),
        ("currency", "BR"),
        ("currency", "B1L"),
        ("invoice_status", "PAID"),
        ("payment_status", "paid@example.test"),
        ("subscription_status", {"nested": True}),
        ("failure_code", "x" * 101),
        ("customer_reference", "pessoa@example.test"),
        ("period_start", "2026-01-01T00:00:00"),
        ("period_end", "not-a-date"),
    ],
)
def test_schema_tipado_recusa_tipo_limite_e_pii_disfarcada(chave, valor):
    with pytest.raises(ValidationError):
        normalizar_payload_evento({chave: valor})


def test_schema_tipado_normaliza_moeda_e_aceita_fatos_validos():
    assert normalizar_payload_evento(
        {
            "amount": 0,
            "currency": "brl",
            "customer_reference": "checkout:42:v1~assinada",
            "failure_code": "card_declined",
            "invoice_status": "paid",
            "period_start": "2026-01-01T00:00:00Z",
        }
    ) == {
        "amount": 0,
        "currency": "BRL",
        "customer_reference": "checkout:42:v1~assinada",
        "failure_code": "card_declined",
        "invoice_status": "paid",
        "period_start": "2026-01-01T00:00:00Z",
    }


@pytest.mark.parametrize("config", [None, (), ("path",), ("path", []), ("path", {"api_key": "x"}), "stripe"])
def test_check_de_configuracao_malformada_retorna_error(config):
    with override_settings(CHECKOUT_VARIANTS={"stripe": config}):
        resultado = configuracao_faturamento_check(None)
    assert len(resultado) == 1
    assert isinstance(resultado[0], Error)


def test_admin_oculta_payload_urls_e_e_somente_leitura():
    for model in (AssinaturaGateway, CheckoutCobranca, FaturaAssinatura, EventoCobranca):
        model_admin = admin.site._registry[model]
        assert model_admin.has_add_permission(None) is False
        assert model_admin.has_change_permission(None) is False
        assert model_admin.has_delete_permission(None) is False
    assert "payload_normalizado" in admin.site._registry[EventoCobranca].exclude
    assert "url" in admin.site._registry[CheckoutCobranca].exclude


@pytest.mark.django_db(transaction=True)
def test_rls_real_ingresso_roteia_uma_vez_e_tenant_isola(papel_ingresso):
    organizacao = Organizacao.objects.create(nome="RLS Billing", slug="rls-billing")
    assinatura = _criar_assinatura(organizacao, _criar_versao(codigo="rls-billing"))
    AssinaturaGateway.objects.create(
        organizacao=organizacao,
        assinatura=assinatura,
        variante="stripe",
        identificador_externo="sub-rls",
    )
    database = settings.DATABASES["default"]
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT rolname, rolsuper, rolcanlogin, rolcreaterole, rolcreatedb, rolbypassrls
               FROM pg_roles
               WHERE rolname IN ('billing_router_owner', 'billing_ingress_runtime')
               ORDER BY rolname"""
        )
        assert cursor.fetchall() == [
            ("billing_ingress_runtime", False, False, False, False, False),
            ("billing_router_owner", False, False, False, False, False),
        ]
        cursor.execute(
            """SELECT p.prosecdef, owner.rolname,
                      has_function_privilege('public', p.oid, 'EXECUTE'),
                      has_function_privilege('billing_ingress_runtime', p.oid, 'EXECUTE')
               FROM pg_proc p JOIN pg_roles owner ON owner.oid = p.proowner
               WHERE p.oid = 'public.faturamento_rotear_evento(bigint)'::regprocedure"""
        )
        assert cursor.fetchone() == (False, "billing_router_owner", False, True)
    conexao = psycopg2.connect(
        dbname=database["NAME"],
        user=PAPEL_INGRESSO,
        password=SENHA_INGRESSO,
        host=database["HOST"],
        port=database["PORT"],
    )
    try:
        with conexao.cursor() as cursor, pytest.raises(psycopg2.errors.InsufficientPrivilege):
            cursor.execute("SELECT faturamento_rotear_evento(0)")
        conexao.rollback()
        with conexao, conexao.cursor() as cursor:
            cursor.execute("SET ROLE billing_ingress_runtime")
            cursor.execute("SELECT set_config('rls.tenant_id', '0', true)")
            cursor.execute("SELECT set_config('rls.billing_ingress', '1', true)")
            cursor.execute(
                """INSERT INTO evento_cobranca
                   (created_at, last_modified_at, is_active, is_deleted, variante,
                    identificador_evento, tipo, identificador_assinatura, status, exige_tenant,
                    tentativas_roteamento, tentativas_processamento,
                    payload_normalizado, hash_payload, erro,
                    identificador_checkout, identificador_fatura)
                   VALUES (NOW(), NOW(), true, false, 'stripe', 'evt-rls',
                           'invoice.paid', 'sub-rls', 10, true, 0, 0, '{}'::jsonb,
                           repeat('a', 64), '', '', '') RETURNING id"""
            )
            evento_id = cursor.fetchone()[0]

        with conexao, conexao.cursor() as cursor:
            cursor.execute("SET ROLE billing_ingress_runtime")
            cursor.execute("SELECT set_config('rls.tenant_id', '0', true)")
            cursor.execute("SELECT set_config('rls.billing_ingress', '1', true)")
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                cursor.execute("UPDATE evento_cobranca SET organizacao_id = %s WHERE id = %s", [organizacao.pk, evento_id])
            conexao.rollback()

        with conexao, conexao.cursor() as cursor:
            cursor.execute("SET ROLE billing_ingress_runtime")
            cursor.execute("SELECT set_config('rls.tenant_id', '0', true)")
            cursor.execute("SELECT set_config('rls.billing_ingress', '1', true)")
            cursor.execute("SELECT faturamento_rotear_evento(%s)", [evento_id])
            assert cursor.fetchone() == (True,)
            cursor.execute("SELECT id FROM evento_cobranca WHERE id = %s", [evento_id])
            assert cursor.fetchall() == []
            cursor.execute("SELECT faturamento_rotear_evento(%s)", [evento_id])
            assert cursor.fetchone() == (False,)

        with conexao, conexao.cursor() as cursor:
            cursor.execute("SET ROLE billing_ingress_runtime")
            cursor.execute("SELECT set_config('rls.tenant_id', %s, true)", [str(organizacao.pk)])
            cursor.execute("SELECT set_config('rls.billing_ingress', '0', true)")
            cursor.execute("SELECT id FROM evento_cobranca WHERE id = %s", [evento_id])
            assert cursor.fetchall() == [(evento_id,)]
    finally:
        conexao.close()
        connections.close_all()


def test_constraint_do_banco_recusa_payload_fora_do_schema():
    organizacao = Organizacao.objects.create(nome="Payload", slug="payload-invalido")
    from apps.organizacoes.context import organizacao_atual_privilegiada

    evento = EventoCobranca(
        organizacao=organizacao,
        variante="stripe",
        identificador_evento="evt-save-normaliza",
        tipo="invoice.paid",
        exige_tenant=False,
        payload_normalizado={"amount": 100, "currency": "brl", "email": "descartar@example.test"},
        hash_payload="a" * 64,
    )
    with organizacao_atual_privilegiada(organizacao.pk):
        evento.save()
        evento.refresh_from_db()
        with pytest.raises(IntegrityError), transaction.atomic():
            EventoCobranca.objects.create(
                organizacao=organizacao,
                variante="stripe",
                identificador_evento="evt-save-normaliza",
                tipo="invoice.paid",
                exige_tenant=False,
                payload_normalizado={},
                hash_payload="c" * 64,
            )
    assert evento.payload_normalizado == {"amount": 100, "currency": "BRL"}

    invalido_bulk = EventoCobranca(
        organizacao=organizacao,
        variante="stripe",
        identificador_evento="evt-bulk-invalido",
        tipo="invoice.paid",
        exige_tenant=False,
        payload_normalizado={"amount": True},
        hash_payload="b" * 64,
    )
    with organizacao_atual_privilegiada(organizacao.pk), pytest.raises(DatabaseError), transaction.atomic():
        EventoCobranca.objects.bulk_create([invalido_bulk])

    with organizacao_atual_privilegiada(organizacao.pk), pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            """INSERT INTO evento_cobranca
               (created_at, last_modified_at, is_active, is_deleted, variante,
                identificador_evento, tipo, organizacao_id, status, exige_tenant,
                tentativas_roteamento, tentativas_processamento,
                payload_normalizado, hash_payload, erro,
                identificador_assinatura, identificador_checkout, identificador_fatura)
               VALUES (NOW(), NOW(), true, false, 'stripe', 'evt-payload-invalido',
                       'invoice.paid', %s, 10, false, 0, 0, %s::jsonb,
                       repeat('a', 64), '', '', '', '')""",
            [organizacao.pk, '{"email":"pii@example.test","raw":{"card":"4242"}}'],
        )

    with organizacao_atual_privilegiada(organizacao.pk):
        assert EventoCobranca.objects.create(
            organizacao=organizacao,
            variante="stripe",
            identificador_evento="evt-processado-valido",
            tipo="invoice.paid",
            status=StatusEventoCobranca.PROCESSADO,
            exige_tenant=True,
            payload_normalizado={},
            hash_payload="d" * 64,
        ).pk
    with pytest.raises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            """INSERT INTO evento_cobranca
               (created_at,last_modified_at,is_active,is_deleted,variante,identificador_evento,tipo,status,exige_tenant,
                tentativas_roteamento,tentativas_processamento,payload_normalizado,hash_payload,erro,
                identificador_assinatura,identificador_checkout,identificador_fatura)
               VALUES (NOW(),NOW(),true,false,'stripe','evt-processado-sem-tenant','invoice.paid',40,true,0,0,'{}',repeat('e',64),'','','','')"""
        )


@pytest.mark.django_db(transaction=True)
def test_matriz_unicidades_coerencia_e_rls_tenant_no_postgresql(papel_ingresso):
    org_a = Organizacao.objects.create(nome="Matriz A", slug="matriz-a")
    org_b = Organizacao.objects.create(nome="Matriz B", slug="matriz-b")
    versao_a = _criar_versao(codigo="matriz-a")
    versao_b = _criar_versao(codigo="matriz-b")
    assinatura_a = _criar_assinatura(org_a, versao_a)
    assinatura_b = _criar_assinatura(org_b, versao_b)
    AssinaturaGateway.objects.create(organizacao=org_a, assinatura=assinatura_a, variante="stripe", identificador_externo="sub-global")

    with pytest.raises(IntegrityError), transaction.atomic():
        AssinaturaGateway.objects.create(organizacao=org_b, assinatura=assinatura_b, variante="stripe", identificador_externo="sub-global")
    with pytest.raises(IntegrityError), transaction.atomic():
        AssinaturaGateway.objects.create(organizacao=org_b, assinatura=assinatura_a, variante="stripe", identificador_externo="sub-divergente")

    preco = PrecoPlano.objects.get(versao_plano=versao_a)
    ReferenciaPrecoGateway.objects.create(preco_plano=preco, variante="stripe", componente=ComponentePreco.BASE, identificador_externo="price-base")
    with pytest.raises(IntegrityError), transaction.atomic():
        ReferenciaPrecoGateway.objects.create(
            preco_plano=preco, variante="stripe", componente=ComponentePreco.BASE, identificador_externo="price-outra"
        )
    ReferenciaPrecoGateway.objects.create(
        preco_plano=preco,
        variante="stripe",
        componente=ComponentePreco.BASE,
        identificador_externo="price-inativa",
        is_active=False,
    )

    with organizacao_atual_privilegiada(org_a.pk):
        checkout_a = CheckoutCobranca.objects.create(
            organizacao=org_a,
            assinatura=assinatura_a,
            finalidade=FinalidadeCheckout.FORMA_PAGAMENTO,
            status=StatusCheckout.CRIADO,
            chave_idempotencia="idem",
            variante="stripe",
            identificador_externo="cs-global",
            valor_esperado_centavos=0,
            moeda_esperada="BRL",
        )
        fatura_a = FaturaAssinatura.objects.create(
            organizacao=org_a,
            assinatura=assinatura_a,
            variante="stripe",
            identificador_externo="in-mesmo",
            status=StatusFatura.ABERTA,
            moeda="BRL",
        )
        assert list(CheckoutCobranca.objects.values_list("pk", flat=True)) == [checkout_a.pk]
        assert list(FaturaAssinatura.objects.values_list("pk", flat=True)) == [fatura_a.pk]
        for finalidade, valor in (
            (FinalidadeCheckout.CONTRATACAO, 100),
            (FinalidadeCheckout.ALTERACAO, 100),
            (FinalidadeCheckout.PROPOSTA, 100),
            (FinalidadeCheckout.FORMA_PAGAMENTO, 1),
        ):
            with pytest.raises(IntegrityError), transaction.atomic():
                CheckoutCobranca.objects.create(
                    organizacao=org_a,
                    assinatura=assinatura_a,
                    finalidade=finalidade,
                    chave_idempotencia=f"invalida-{finalidade}",
                    variante="stripe",
                    valor_esperado_centavos=valor,
                    moeda_esperada="BRL",
                )
        alteracao = AlteracaoAssinatura.objects.create(
            organizacao=org_a,
            assinatura=assinatura_a,
            tipo=TipoAlteracaoAssinatura.AUMENTO_SEATS,
            momento_aplicacao=MomentoAplicacaoAlteracaoAssinatura.IMEDIATA,
            revisao_esperada=assinatura_a.revisao,
            chave_idempotencia="alteracao-checkout",
            pedido={},
            snapshot_anterior={},
            snapshot_pretendido={},
        )
        assert CheckoutCobranca.objects.create(
            organizacao=org_a,
            assinatura=assinatura_a,
            alteracao=alteracao,
            finalidade=FinalidadeCheckout.ALTERACAO,
            chave_idempotencia="checkout-alteracao-valida",
            variante="stripe",
            valor_esperado_centavos=100,
            moeda_esperada="BRL",
        ).pk

        ator = criar_usuario(email="owner-matriz@example.test")
        Vinculo.objects.create(organizacao=org_a, usuario=ator, papel=Papel.PROPRIETARIO)
        proposta = _criar_proposta(org_a)
        proposta = Propostas.enviar(proposta, revisao_esperada=proposta.revisao)
        proposta = Propostas.aceitar(proposta, ator=ator, revisao_esperada=proposta.revisao).proposta
        with organizacao_atual_privilegiada(org_a.pk):
            assert CheckoutCobranca.objects.create(
                organizacao=org_a,
                assinatura=assinatura_a,
                proposta=proposta,
                finalidade=FinalidadeCheckout.PROPOSTA,
                chave_idempotencia="checkout-proposta-valida",
                variante="stripe",
                valor_esperado_centavos=100,
                moeda_esperada="BRL",
            ).pk

    with organizacao_atual_privilegiada(org_b.pk):
        FaturaAssinatura.objects.create(
            organizacao=org_b,
            assinatura=assinatura_b,
            variante="stripe",
            identificador_externo="in-mesmo",
            status=StatusFatura.ABERTA,
            moeda="BRL",
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            CheckoutCobranca.objects.create(
                organizacao=org_b,
                assinatura=assinatura_b,
                finalidade=FinalidadeCheckout.FORMA_PAGAMENTO,
                chave_idempotencia="outra",
                variante="stripe",
                identificador_externo="cs-global",
                valor_esperado_centavos=0,
                moeda_esperada="BRL",
            )
        with pytest.raises(IntegrityError), transaction.atomic():
            FaturaAssinatura.objects.create(
                organizacao=org_b,
                assinatura=assinatura_a,
                variante="stripe",
                identificador_externo="in-divergente",
                status=StatusFatura.ABERTA,
                moeda="BRL",
            )

    database = settings.DATABASES["default"]
    conexao = psycopg2.connect(dbname=database["NAME"], user=PAPEL_INGRESSO, password=SENHA_INGRESSO, host=database["HOST"], port=database["PORT"])
    try:
        with conexao, conexao.cursor() as cursor:
            cursor.execute("SELECT set_config('rls.tenant_id', %s, true)", [str(org_a.pk)])
            cursor.execute("SELECT id FROM checkout_cobranca")
            ids_checkout_org_a = {row[0] for row in cursor.fetchall()}
            assert checkout_a.pk in ids_checkout_org_a
            assert len(ids_checkout_org_a) == 3
            cursor.execute("SELECT id FROM fatura_assinatura")
            assert cursor.fetchall() == [(fatura_a.pk,)]
        with conexao, conexao.cursor() as cursor:
            cursor.execute("SELECT set_config('rls.tenant_id', %s, true)", [str(org_b.pk)])
            cursor.execute("SELECT id FROM checkout_cobranca")
            assert cursor.fetchall() == []
            cursor.execute("SELECT count(*) FROM fatura_assinatura")
            assert cursor.fetchone() == (1,)
    finally:
        conexao.close()
