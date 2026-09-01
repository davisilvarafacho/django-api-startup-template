from django.conf import settings
from django.contrib import admin
from django.core.checks import Error
from django.core.exceptions import ValidationError
from django.db import DatabaseError, connection, connections, models, transaction
from django.test import override_settings

import psycopg2
import pytest

from apps.api.base.models import Base, BaseTenantless
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
from apps.organizacoes.models import Organizacao

pytestmark = pytest.mark.django_db

PAPEL_INGRESSO = "billing_ingress_tester"
SENHA_INGRESSO = "billing_ingress_tester"


@pytest.fixture(scope="module")
def papel_ingresso(django_db_setup, django_db_blocker):
    with django_db_blocker.unblock(), connection.cursor() as cursor:
        cursor.execute(f"DROP ROLE IF EXISTS {PAPEL_INGRESSO}")
        cursor.execute(f"CREATE ROLE {PAPEL_INGRESSO} LOGIN PASSWORD %s NOSUPERUSER NOBYPASSRLS NOINHERIT", [SENHA_INGRESSO])
        cursor.execute(f"GRANT USAGE ON SCHEMA public TO {PAPEL_INGRESSO}")
        cursor.execute(f"GRANT SELECT, INSERT, UPDATE ON evento_cobranca TO {PAPEL_INGRESSO}")
        cursor.execute(f"GRANT USAGE, SELECT ON SEQUENCE evento_cobranca_id_seq TO {PAPEL_INGRESSO}")
        cursor.execute(f"GRANT EXECUTE ON FUNCTION faturamento_rotear_evento(bigint, bigint) TO {PAPEL_INGRESSO}")
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
    database = settings.DATABASES["default"]
    conexao = psycopg2.connect(
        dbname=database["NAME"],
        user=PAPEL_INGRESSO,
        password=SENHA_INGRESSO,
        host=database["HOST"],
        port=database["PORT"],
    )
    try:
        with conexao.cursor() as cursor, pytest.raises(psycopg2.errors.InsufficientPrivilege):
            cursor.execute("SELECT faturamento_rotear_evento(0, %s)", [organizacao.pk])
        conexao.rollback()
        with conexao, conexao.cursor() as cursor:
            cursor.execute("SELECT set_config('rls.tenant_id', '0', true)")
            cursor.execute("SELECT set_config('rls.billing_ingress', '1', true)")
            cursor.execute(
                """INSERT INTO evento_cobranca
                   (created_at, last_modified_at, is_active, is_deleted, variante,
                    identificador_evento, tipo, status, exige_tenant,
                    tentativas_roteamento, tentativas_processamento,
                    payload_normalizado, hash_payload, erro,
                    identificador_assinatura, identificador_checkout, identificador_fatura)
                   VALUES (NOW(), NOW(), true, false, 'stripe', 'evt-rls',
                           'invoice.paid', 10, true, 0, 0, '{}'::jsonb,
                           repeat('a', 64), '', '', '', '') RETURNING id"""
            )
            evento_id = cursor.fetchone()[0]

        with conexao, conexao.cursor() as cursor:
            cursor.execute("SELECT set_config('rls.tenant_id', '0', true)")
            cursor.execute("SELECT set_config('rls.billing_ingress', '1', true)")
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                cursor.execute("UPDATE evento_cobranca SET organizacao_id = %s WHERE id = %s", [organizacao.pk, evento_id])
            conexao.rollback()

        with conexao, conexao.cursor() as cursor:
            cursor.execute("SELECT set_config('rls.tenant_id', '0', true)")
            cursor.execute("SELECT set_config('rls.billing_ingress', '1', true)")
            cursor.execute("SELECT faturamento_rotear_evento(%s, %s)", [evento_id, organizacao.pk])
            assert cursor.fetchone() == (True,)
            cursor.execute("SELECT id FROM evento_cobranca WHERE id = %s", [evento_id])
            assert cursor.fetchall() == []
            cursor.execute("SELECT faturamento_rotear_evento(%s, %s)", [evento_id, organizacao.pk])
            assert cursor.fetchone() == (False,)

        with conexao, conexao.cursor() as cursor:
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
