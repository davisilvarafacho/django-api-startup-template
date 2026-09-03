from datetime import timedelta
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.conf import settings
from django.contrib import admin
from django.core.checks import Error
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, connection, connections, models, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import override_settings
from django.utils import timezone

import psycopg2
import pytest

from apps.api.base.models import Base, BaseTenantless
from apps.assinaturas.models import (
    AlteracaoAssinatura,
    MomentoAplicacaoAlteracaoAssinatura,
    PrecoPlano,
    StatusAssinatura,
    StatusFinanceiro,
    TipoAlteracaoAssinatura,
)
from apps.assinaturas.proposals import Propostas
from apps.assinaturas.subapps.faturamento.checks import configuracao_faturamento_check, role_ingresso_check
from apps.assinaturas.subapps.faturamento.models import (
    AssinaturaGateway,
    CheckoutCobranca,
    ComponentePreco,
    EventoCobranca,
    FaturaAssinatura,
    FinalidadeCheckout,
    ReaberturaEventoCobranca,
    ReferenciaPrecoGateway,
    SolicitacaoReconciliacaoCobranca,
    StatusCheckout,
    StatusEventoCobranca,
    StatusFatura,
)
from apps.assinaturas.subapps.faturamento.payloads import normalizar_payload_evento, validar_tipo_evento
from apps.assinaturas.tests.test_proposal_models import _criar_proposta
from apps.assinaturas.tests.test_subscription_models import _criar_assinatura, _criar_versao
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Organizacao, Papel, Vinculo
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db

PAPEL_INGRESSO = "billing_ingress_tester"
SENHA_INGRESSO = "billing_ingress_tester"
PAPEL_WEB = "billing_web_tester"
PAPEL_MIGRATION = "billing_migration_tester"
SENHA_PAPEIS = "billing_roles_tester"


@pytest.fixture(scope="module")
def papel_ingresso(django_db_setup, django_db_blocker):
    with django_db_blocker.unblock(), connection.cursor() as cursor:
        cursor.execute(f"DROP ROLE IF EXISTS {PAPEL_INGRESSO}")
        cursor.execute(f"CREATE ROLE {PAPEL_INGRESSO} LOGIN PASSWORD %s NOSUPERUSER NOBYPASSRLS NOINHERIT", [SENHA_INGRESSO])
        cursor.execute(f"DROP ROLE IF EXISTS {PAPEL_WEB}")
        cursor.execute(f"DROP ROLE IF EXISTS {PAPEL_MIGRATION}")
        cursor.execute(f"CREATE ROLE {PAPEL_WEB} LOGIN PASSWORD %s NOSUPERUSER NOBYPASSRLS NOINHERIT", [SENHA_PAPEIS])
        cursor.execute(f"CREATE ROLE {PAPEL_MIGRATION} LOGIN PASSWORD %s NOSUPERUSER NOBYPASSRLS NOINHERIT", [SENHA_PAPEIS])
        cursor.execute(f"GRANT billing_ingress_runtime TO {PAPEL_INGRESSO}")
        cursor.execute(f"GRANT billing_functions_owner TO {PAPEL_MIGRATION} WITH SET TRUE")
        cursor.execute(f"GRANT USAGE ON SCHEMA public TO {PAPEL_INGRESSO}")
        cursor.execute(f"GRANT SELECT ON evento_cobranca TO {PAPEL_INGRESSO}")
        cursor.execute(f"GRANT SELECT, INSERT, UPDATE ON checkout_cobranca, fatura_assinatura TO {PAPEL_INGRESSO}")
        cursor.execute(f"GRANT USAGE, SELECT ON SEQUENCE checkout_cobranca_id_seq, fatura_assinatura_id_seq TO {PAPEL_INGRESSO}")
        cursor.execute(f"GRANT SELECT ON reabertura_evento_cobranca, solicitacao_reconciliacao_cobranca TO {PAPEL_WEB}")
    yield
    with django_db_blocker.unblock(), connection.cursor() as cursor:
        cursor.execute(f"DROP OWNED BY {PAPEL_INGRESSO}")
        cursor.execute(f"DROP ROLE IF EXISTS {PAPEL_INGRESSO}")
        cursor.execute(f"DROP OWNED BY {PAPEL_WEB}")
        cursor.execute(f"DROP OWNED BY {PAPEL_MIGRATION}")
        cursor.execute(f"DROP ROLE IF EXISTS {PAPEL_WEB}")
        cursor.execute(f"DROP ROLE IF EXISTS {PAPEL_MIGRATION}")


def test_modelos_respeitam_fronteira_global_e_tenantizada():
    assert issubclass(AssinaturaGateway, BaseTenantless)
    assert issubclass(ReferenciaPrecoGateway, BaseTenantless)


def test_migration_0009_recusa_owner_security_definer_divergente():
    migration = import_module("apps.assinaturas.subapps.faturamento.migrations.0009_reconcile_first_conclusivo")
    editor = SimpleNamespace(connection=SimpleNamespace(vendor="postgresql"), execute=Mock())
    with override_settings(BILLING_DATABASE_OWNER_ROLE="owner_incorreto"), pytest.raises(RuntimeError, match="Owner financeiro incompatível"):
        migration.instalar(None, editor)
    editor.execute.assert_not_called()


def test_migration_0011_recusa_owner_security_definer_divergente_no_reverse():
    migration = import_module("apps.assinaturas.subapps.faturamento.migrations.0011_fatos_invoice_e_lease_recovery")
    editor = SimpleNamespace(connection=SimpleNamespace(vendor="postgresql"), execute=Mock())

    with override_settings(BILLING_DATABASE_OWNER_ROLE="owner_incorreto"), pytest.raises(RuntimeError, match="Owner financeiro incompatível"):
        migration.remover(None, editor)

    editor.execute.assert_not_called()


@pytest.mark.parametrize(
    "nome",
    [
        "0009_reconcile_first_conclusivo",
        "0011_fatos_invoice_e_lease_recovery",
    ],
)
def test_preparacao_de_reverse_usa_alias_do_schema_editor_inclusive_sqlite(nome):
    migration = import_module(f"apps.assinaturas.subapps.faturamento.migrations.{nome}")
    manager = Mock()
    manager.using.return_value = manager
    model = SimpleNamespace(objects=manager)
    apps_historicos = SimpleNamespace(get_model=Mock(return_value=model))
    editor = SimpleNamespace(connection=SimpleNamespace(vendor="sqlite", alias="sqlite_reverse"), execute=Mock())

    migration.preparar_reverse(apps_historicos, editor)

    manager.using.assert_called_once_with("sqlite_reverse")
    editor.execute.assert_not_called()


def test_fatos_financeiros_opcionais_distinguem_indisponivel_de_zero_explicito():
    for campo in ("subtotal_centavos", "desconto_centavos", "imposto_centavos", "total_centavos"):
        field = FaturaAssinatura._meta.get_field(campo)
        assert field.null is True
        assert field.default is models.NOT_PROVIDED
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


def test_configuracao_separa_roles_e_credencial_de_migration():
    assert settings.BILLING_DATABASE_OWNER_ROLE == "billing_functions_owner"
    assert settings.BILLING_INGRESS_DATABASE_ROLE == "billing_ingress_runtime"
    assert settings.BILLING_DATABASE_OWNER_ROLE != settings.DATABASES["default"]["USER"]


@pytest.mark.parametrize(
    "tipo",
    [
        "invoice.paid",
        "invoice.payment_succeeded",
        "customer_subscription.updated",
        "checkout_session.completed",
        "a",
        "a1.b2_c3",
    ],
)
def test_tipo_evento_aceita_gramatica_canonica(tipo):
    validar_tipo_evento(tipo)


@pytest.mark.parametrize(
    "tipo",
    [
        "",
        "Invoice.paid",
        "invoice-paid",
        "invoice paid",
        "invoice@paid.example",
        ".invoice",
        "invoice.",
        "invoice..paid",
        "invoice__paid",
        "invoice\npaid",
        "a" * 101,
    ],
)
def test_tipo_evento_recusa_formas_ambiguas_ou_sensiveis(tipo):
    with pytest.raises(ValidationError):
        validar_tipo_evento(tipo)


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


@pytest.mark.parametrize(
    "valor",
    [
        "pii@example.testZ",
        "not-a-date+00:00",
        "2026-02-30T00:00:00Z",
        "2026-01-01T00:00:00",
        "2026-01-01+00:00",
        "2026-01-01 00:00:00Z",
        "2026-01-01T00:00:00+15:00",
        "2026-01-01T00:00:00.1234567Z",
        "2026-01-01T23:59:60Z",
        "2026-01-01T24:00:00Z",
    ],
)
def test_validator_postgresql_rejeita_timestamp_semantico_invalido(valor):
    with pytest.raises(ValidationError):
        normalizar_payload_evento({"period_start": valor})
    with connection.cursor() as cursor:
        cursor.execute("SELECT evento_payload_valido(jsonb_build_object('period_start', %s))", [valor])
        assert cursor.fetchone() == (False,)


@pytest.mark.parametrize("config", [None, (), ("path",), ("path", []), ("path", {"api_key": "x"}), "stripe"])
def test_check_de_configuracao_malformada_retorna_error(config):
    with override_settings(CHECKOUT_VARIANTS={"stripe": config}):
        resultado = configuracao_faturamento_check(None)
    assert len(resultado) == 1
    assert isinstance(resultado[0], Error)


def _mock_role_check(modo, owner_set, runtime_set, *, owner_outbound=False, runtime_replication=False, catalogo=(True, True, 2)):
    cursor_patch = patch("apps.assinaturas.subapps.faturamento.checks.connection.cursor")
    cursor_factory = cursor_patch.start()
    cursor = cursor_factory.return_value.__enter__.return_value
    cursor.fetchall.return_value = [
        ("billing_functions_owner", False, False, False, False, False, False, False, owner_set, owner_outbound, "app_user"),
        ("billing_ingress_runtime", False, False, False, False, False, False, runtime_replication, runtime_set, False, "app_user"),
    ]
    cursor.fetchone.return_value = catalogo
    settings_patch = override_settings(BILLING_DATABASE_MODE=modo)
    settings_patch.enable()
    return cursor_patch, settings_patch


@pytest.mark.parametrize(("modo", "memberships"), [("web", (False, False)), ("ingress", (False, True)), ("migration", (True, False))])
def test_check_deploy_prova_memberships_por_modo(modo, memberships):
    cursor_patch, settings_patch = _mock_role_check(modo, *memberships)
    try:
        assert role_ingresso_check(None) == []
    finally:
        settings_patch.disable()
        cursor_patch.stop()


@pytest.mark.parametrize(
    ("kwargs", "error_id"),
    [
        ({"modo": "web", "owner_set": True, "runtime_set": False}, "faturamento.E004"),
        ({"modo": "ingress", "owner_set": False, "runtime_set": False}, "faturamento.E004"),
        ({"modo": "migration", "owner_set": False, "runtime_set": False}, "faturamento.E004"),
        ({"modo": "web", "owner_set": False, "runtime_set": False, "owner_outbound": True}, "faturamento.E003"),
        ({"modo": "web", "owner_set": False, "runtime_set": False, "runtime_replication": True}, "faturamento.E003"),
        ({"modo": "web", "owner_set": False, "runtime_set": False, "catalogo": (False, True, 1)}, "faturamento.E006"),
        ({"modo": "web", "owner_set": False, "runtime_set": False, "catalogo": (True, True, 0)}, "faturamento.E006"),
    ],
)
def test_check_deploy_rejeita_grafo_ou_catalogo_inseguro(kwargs, error_id):
    cursor_patch, settings_patch = _mock_role_check(**kwargs)
    try:
        assert [erro.id for erro in role_ingresso_check(None)] == [error_id]
    finally:
        settings_patch.disable()
        cursor_patch.stop()


def test_check_migration_permite_catalogo_zero_no_preflight():
    cursor_patch, settings_patch = _mock_role_check("migration", True, False, catalogo=(True, True, 0))
    try:
        assert role_ingresso_check(None) == []
    finally:
        settings_patch.disable()
        cursor_patch.stop()


def test_check_deploy_rejeita_nomes_e_modo_arbitrarios():
    with override_settings(BILLING_INGRESS_DATABASE_ROLE="role-arbitraria"):
        assert [erro.id for erro in role_ingresso_check(None)] == ["faturamento.E002"]
    with override_settings(BILLING_DATABASE_MODE="arbitrario"):
        assert [erro.id for erro in role_ingresso_check(None)] == ["faturamento.E005"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("aplicar", "restaurar"),
    [
        (
            "GRANT SELECT ON assinatura_gateway TO billing_ingress_runtime",
            "REVOKE SELECT ON assinatura_gateway FROM billing_ingress_runtime",
        ),
        (
            "GRANT USAGE ON SEQUENCE evento_cobranca_id_seq TO billing_ingress_runtime",
            "REVOKE USAGE ON SEQUENCE evento_cobranca_id_seq FROM billing_ingress_runtime",
        ),
        (
            "GRANT SELECT(id) ON evento_cobranca TO billing_ingress_runtime",
            "REVOKE SELECT(id) ON evento_cobranca FROM billing_ingress_runtime",
        ),
        (
            "GRANT CREATE ON SCHEMA public TO billing_ingress_runtime",
            "REVOKE CREATE ON SCHEMA public FROM billing_ingress_runtime",
        ),
        (
            "GRANT DELETE ON evento_cobranca TO billing_functions_owner",
            "REVOKE DELETE ON evento_cobranca FROM billing_functions_owner",
        ),
        (
            "CREATE POLICY evento_ingresso_permissiva ON evento_cobranca FOR SELECT TO billing_ingress_runtime USING (true)",
            "DROP POLICY evento_ingresso_permissiva ON evento_cobranca",
        ),
        (
            "ALTER FUNCTION faturamento_rotear_evento_destino(bigint,bigint) RESET search_path",
            "ALTER FUNCTION faturamento_rotear_evento_destino(bigint,bigint) SET search_path=pg_catalog,pg_temp",
        ),
        (
            "REVOKE EXECUTE ON FUNCTION faturamento_rotear_evento_destino(bigint,bigint) FROM billing_ingress_runtime",
            "GRANT EXECUTE ON FUNCTION faturamento_rotear_evento_destino(bigint,bigint) TO billing_ingress_runtime",
        ),
        (
            "GRANT EXECUTE ON FUNCTION faturamento_rotear_evento_destino(bigint,bigint) TO PUBLIC",
            "REVOKE EXECUTE ON FUNCTION faturamento_rotear_evento_destino(bigint,bigint) FROM PUBLIC",
        ),
        (
            "ALTER POLICY evento_interface_definidor_select ON evento_cobranca USING (false)",
            "ALTER POLICY evento_interface_definidor_select ON evento_cobranca USING (true)",
        ),
        (
            "DROP POLICY evento_interface_definidor_select ON evento_cobranca; "
            "CREATE POLICY evento_interface_definidor_select ON evento_cobranca FOR SELECT TO billing_functions_owner",
            "DROP POLICY evento_interface_definidor_select ON evento_cobranca; "
            "CREATE POLICY evento_interface_definidor_select ON evento_cobranca FOR SELECT TO billing_functions_owner USING (true)",
        ),
        (
            "CREATE FUNCTION faturamento_rotear_evento(text,text) RETURNS boolean LANGUAGE sql AS 'SELECT false'",
            "DROP FUNCTION faturamento_rotear_evento(text,text)",
        ),
        ("ALTER TABLE evento_cobranca DISABLE ROW LEVEL SECURITY", "ALTER TABLE evento_cobranca ENABLE ROW LEVEL SECURITY"),
        ("ALTER TABLE evento_cobranca NO FORCE ROW LEVEL SECURITY", "ALTER TABLE evento_cobranca FORCE ROW LEVEL SECURITY"),
        (
            "ALTER POLICY evento_tenant ON evento_cobranca USING (true) WITH CHECK (true)",
            "ALTER POLICY evento_tenant ON evento_cobranca "
            "USING (organizacao_id = NULLIF(current_setting('rls.tenant_id', true), '')::integer) "
            "WITH CHECK (organizacao_id = NULLIF(current_setting('rls.tenant_id', true), '')::integer)",
        ),
        (
            "DROP POLICY isolamento_organizacao ON checkout_cobranca",
            "CREATE POLICY isolamento_organizacao ON checkout_cobranca "
            "USING (organizacao_id = NULLIF(current_setting('rls.tenant_id', true), '')::integer) "
            "WITH CHECK (organizacao_id = NULLIF(current_setting('rls.tenant_id', true), '')::integer)",
        ),
        (
            "CREATE POLICY checkout_permissiva ON checkout_cobranca USING (true)",
            "DROP POLICY checkout_permissiva ON checkout_cobranca",
        ),
        (
            "ALTER POLICY isolamento_organizacao ON fatura_assinatura USING (true) WITH CHECK (true)",
            "ALTER POLICY isolamento_organizacao ON fatura_assinatura "
            "USING (organizacao_id = NULLIF(current_setting('rls.tenant_id', true), '')::integer) "
            "WITH CHECK (organizacao_id = NULLIF(current_setting('rls.tenant_id', true), '')::integer)",
        ),
    ],
)
def test_check_deploy_detecta_drift_real_de_privilegios(aplicar, restaurar):
    with connection.cursor() as cursor:
        cursor.execute(aplicar)
    try:
        with override_settings(BILLING_DATABASE_MODE="migration"):
            assert [erro.id for erro in role_ingresso_check(None)] == ["faturamento.E006"]
    finally:
        with connection.cursor() as cursor:
            cursor.execute(restaurar)


@pytest.mark.django_db(transaction=True)
def test_roles_reais_separam_web_ingresso_e_migration(papel_ingresso):
    database = settings.DATABASES["default"]

    def conectar(usuario, senha):
        return psycopg2.connect(dbname=database["NAME"], user=usuario, password=senha, host=database["HOST"], port=database["PORT"])

    for usuario, senha, permitida, negada in (
        (PAPEL_WEB, SENHA_PAPEIS, None, "billing_ingress_runtime"),
        (PAPEL_INGRESSO, SENHA_INGRESSO, "billing_ingress_runtime", "billing_functions_owner"),
        (PAPEL_MIGRATION, SENHA_PAPEIS, "billing_functions_owner", None),
    ):
        conexao = conectar(usuario, senha)
        try:
            with conexao.cursor() as cursor:
                if permitida:
                    cursor.execute(f"SET ROLE {permitida}")
                    cursor.execute("RESET ROLE")
                if negada:
                    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                        cursor.execute(f"SET ROLE {negada}")
                    conexao.rollback()
                if usuario == PAPEL_WEB:
                    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                        cursor.execute("SET ROLE billing_functions_owner")
        finally:
            conexao.close()


def test_admin_oculta_payload_urls_e_e_somente_leitura():
    for model in (AssinaturaGateway, CheckoutCobranca, FaturaAssinatura, EventoCobranca):
        model_admin = admin.site._registry[model]
        assert model_admin.has_add_permission(None) is False
        assert model_admin.has_change_permission(None) is False
        assert model_admin.has_delete_permission(None) is False
    assert "payload_normalizado" in admin.site._registry[EventoCobranca].exclude
    assert "url" in admin.site._registry[CheckoutCobranca].exclude


@pytest.mark.django_db(transaction=True)
def test_rls_auditorias_operacionais_isola_tenant_com_credencial_non_owner(papel_ingresso):
    org_a = Organizacao.objects.create(nome="Auditoria RLS A", slug="auditoria-rls-a")
    org_b = Organizacao.objects.create(nome="Auditoria RLS B", slug="auditoria-rls-b")
    ator = criar_usuario(email="auditoria-rls@example.com")
    registros = []
    for organizacao, sufixo in ((org_a, "a"), (org_b, "b")):
        with organizacao_atual_privilegiada(organizacao.pk):
            evento = EventoCobranca.objects.create(
                organizacao=organizacao,
                variante="stripe",
                identificador_evento=f"evt_auditoria_rls_{sufixo}",
                tipo="invoice.paid",
                status=StatusEventoCobranca.FALHOU,
                hash_payload="f" * 64,
            )
            reabertura = ReaberturaEventoCobranca.objects.create(
                organizacao=organizacao,
                evento=evento,
                motivo="Operação auditada",
                ator=ator,
                chave_idempotencia=f"rls:{sufixo}",
                tentativas_anteriores=1,
            )
            solicitacao = SolicitacaoReconciliacaoCobranca.objects.create(
                organizacao=organizacao,
                variante="stripe",
                ator=ator,
                motivo="Reconciliação auditada",
                chave_idempotencia=f"rls-reconcile:{sufixo}",
                janela_inicio=timezone.now() - timedelta(minutes=1),
                janela_fim=timezone.now(),
            )
            registros.append((reabertura.pk, solicitacao.pk))

    database = settings.DATABASES["default"]
    conexao = psycopg2.connect(
        dbname=database["NAME"],
        user=PAPEL_WEB,
        password=SENHA_PAPEIS,
        host=database["HOST"],
        port=database["PORT"],
    )
    try:
        with conexao, conexao.cursor() as cursor:
            cursor.execute(
                "SELECT current_user <> relowner::regrole::text, relforcerowsecurity FROM pg_class WHERE oid='reabertura_evento_cobranca'::regclass"
            )
            assert cursor.fetchone() == (True, True)
            cursor.execute("SELECT set_config('rls.tenant_id', %s, true)", [str(org_a.pk)])
            cursor.execute("SELECT id FROM reabertura_evento_cobranca ORDER BY id")
            assert cursor.fetchall() == [(registros[0][0],)]
            cursor.execute("SELECT id FROM solicitacao_reconciliacao_cobranca ORDER BY id")
            assert cursor.fetchall() == [(registros[0][1],)]
    finally:
        conexao.close()


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
            """SELECT rolname, rolsuper, rolcanlogin, rolcreaterole, rolcreatedb, rolbypassrls, rolinherit
               FROM pg_roles
               WHERE rolname = 'billing_ingress_runtime'
               ORDER BY rolname"""
        )
        assert cursor.fetchall() == [("billing_ingress_runtime", False, False, False, False, False, False)]
        cursor.execute(
            """SELECT rolname, rolcanlogin, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb,
                      rolinherit, rolreplication, current_user=rolname,
                      EXISTS (SELECT 1 FROM pg_auth_members WHERE member=pg_roles.oid)
               FROM pg_roles WHERE rolname='billing_functions_owner'"""
        )
        assert cursor.fetchone() == ("billing_functions_owner", False, False, False, False, False, False, False, False, False)
        cursor.execute(
            """SELECT table_name, privilege_type
               FROM information_schema.role_table_grants
               WHERE table_schema='public' AND grantee='billing_ingress_runtime'
                 AND table_name IN ('evento_cobranca', 'assinatura_gateway')
               ORDER BY table_name, privilege_type"""
        )
        assert cursor.fetchall() == []
        cursor.execute(
            """SELECT table_name, column_name, privilege_type
               FROM information_schema.role_column_grants
               WHERE table_schema='public' AND grantee='billing_ingress_runtime'
                 AND table_name IN ('evento_cobranca', 'assinatura_gateway')
               ORDER BY table_name, column_name, privilege_type"""
        )
        assert cursor.fetchall() == []
        cursor.execute(
            """SELECT privilege_type FROM information_schema.role_usage_grants
               WHERE object_schema='public' AND object_name='evento_cobranca_id_seq'
                 AND grantee='billing_ingress_runtime'"""
        )
        assert cursor.fetchall() == []
        cursor.execute(
            """SELECT p.proname, pg_get_function_identity_arguments(p.oid), p.prosecdef, owner.rolname,
                      has_function_privilege('public', p.oid, 'EXECUTE'),
                      has_function_privilege('billing_ingress_runtime', p.oid, 'EXECUTE')
               FROM pg_proc p JOIN pg_roles owner ON owner.oid = p.proowner
               WHERE p.pronamespace='public'::regnamespace
                 AND p.proname IN ('faturamento_ingress_evento', 'faturamento_rotear_evento_destino')
               ORDER BY p.proname"""
        )
        funcoes = cursor.fetchall()
        assert [(linha[0], linha[2], linha[4:]) for linha in funcoes] == [
            ("faturamento_ingress_evento", True, (False, True)),
            ("faturamento_rotear_evento_destino", True, (False, True)),
        ]
        assert all(linha[3] == settings.BILLING_DATABASE_OWNER_ROLE for linha in funcoes)
        cursor.execute(
            """SELECT polname, polcmd,
                      ARRAY(SELECT rolname FROM pg_roles WHERE oid=ANY(polroles) ORDER BY rolname)
               FROM pg_policy
               WHERE polrelid='public.evento_cobranca'::regclass
                 AND polname LIKE 'evento_interface_definidor_%'
               ORDER BY polname"""
        )
        assert cursor.fetchall() == [
            ("evento_interface_definidor_insert", "a", [settings.BILLING_DATABASE_OWNER_ROLE]),
            ("evento_interface_definidor_select", "r", [settings.BILLING_DATABASE_OWNER_ROLE]),
            ("evento_interface_definidor_update", "w", [settings.BILLING_DATABASE_OWNER_ROLE]),
        ]
    conexao = psycopg2.connect(
        dbname=database["NAME"],
        user=PAPEL_INGRESSO,
        password=SENHA_INGRESSO,
        host=database["HOST"],
        port=database["PORT"],
    )
    try:
        with conexao.cursor() as cursor, pytest.raises(psycopg2.errors.InsufficientPrivilege):
            cursor.execute("SELECT faturamento_rotear_evento_destino(1, 1)")
        conexao.rollback()
        for tenant_id, ingresso in ((None, None), ("1", "1"), ("0", "0"), ("1", "0")):
            with conexao.cursor() as cursor:
                cursor.execute("SET ROLE billing_ingress_runtime")
                if tenant_id is not None:
                    cursor.execute("SELECT set_config('rls.tenant_id', %s, true)", [tenant_id])
                if ingresso is not None:
                    cursor.execute("SELECT set_config('rls.billing_ingress', %s, true)", [ingresso])
                with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                    cursor.execute(
                        "SELECT * FROM faturamento_ingress_evento(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        ["stripe", f"evt-contexto-{tenant_id}-{ingresso}", "invoice.paid", "sub-rls", "", "", True, "{}", "a" * 64, None],
                    )
            conexao.rollback()
        with conexao, conexao.cursor() as cursor:
            cursor.execute("SET ROLE billing_ingress_runtime")
            cursor.execute("SELECT set_config('rls.tenant_id', '0', true)")
            cursor.execute("SELECT set_config('rls.billing_ingress', '1', true)")
            cursor.execute(
                "SELECT * FROM faturamento_ingress_evento(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                ["stripe", "evt-rls", "invoice.paid", "sub-rls", "", "", True, "{}", "a" * 64, None],
            )
            primeira = cursor.fetchone()
            assert primeira[1] is True
            cursor.execute(
                "SELECT * FROM faturamento_ingress_evento(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                ["stripe", "evt-rls", "invoice.paid", "sub-rls", "", "", True, "{}", "a" * 64, None],
            )
            assert cursor.fetchone()[1] is False

        with conexao.cursor() as cursor, pytest.raises(psycopg2.errors.InvalidParameterValue):
            cursor.execute(
                "SET ROLE billing_ingress_runtime; "
                "SELECT set_config('rls.tenant_id', '0', true); "
                "SELECT set_config('rls.billing_ingress', '1', true); "
                "SELECT * FROM faturamento_ingress_evento(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                ["stripe", "evt-adulterado", "invoice.paid", "sub-rls", "", "", True, '{"email":"pii@example.test"}', "x", None],
            )
        conexao.rollback()
        assert not EventoCobranca._base_manager.filter(identificador_evento="evt-adulterado").exists()

        evento_id = EventoCobranca._base_manager.get(identificador_evento="evt-rls").pk

        with conexao, conexao.cursor() as cursor:
            cursor.execute("SET ROLE billing_ingress_runtime")
            cursor.execute("SELECT set_config('rls.tenant_id', '0', true)")
            cursor.execute("SELECT set_config('rls.billing_ingress', '1', true)")
            for comando in (
                "SELECT * FROM evento_cobranca",
                "INSERT INTO evento_cobranca DEFAULT VALUES",
                "UPDATE evento_cobranca SET organizacao_id = NULL, status = 20, last_modified_at = NOW() RETURNING id, hash_payload",
                "UPDATE evento_cobranca SET status = 20 WHERE organizacao_id IS NULL",
                "DELETE FROM evento_cobranca",
                "SELECT * FROM assinatura_gateway",
            ):
                with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                    cursor.execute(comando)
                conexao.rollback()
                cursor.execute("SET ROLE billing_ingress_runtime")
                cursor.execute("SELECT set_config('rls.tenant_id', '0', true)")
                cursor.execute("SELECT set_config('rls.billing_ingress', '1', true)")

        with conexao, conexao.cursor() as cursor:
            cursor.execute("SET ROLE billing_ingress_runtime")
            cursor.execute("SELECT set_config('rls.tenant_id', '0', true)")
            cursor.execute("SELECT set_config('rls.billing_ingress', '1', true)")
            cursor.execute("SELECT faturamento_rotear_evento_destino(%s,%s)", [evento_id, organizacao.pk])
            assert cursor.fetchone() == (True,)
            cursor.execute("SELECT faturamento_rotear_evento_destino(%s,%s)", [evento_id, organizacao.pk])
            assert cursor.fetchone() == (False,)

        with conexao, conexao.cursor() as cursor:
            cursor.execute("RESET ROLE")
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


@pytest.mark.django_db(transaction=True)
def test_tipo_evento_invalido_falha_em_save_bulk_e_sql():
    organizacao = Organizacao.objects.create(nome="Tipo", slug="tipo-evento-invalido")
    campos = {
        "organizacao": organizacao,
        "variante": "stripe",
        "tipo": "customer@example.test",
        "exige_tenant": False,
        "payload_normalizado": {},
        "hash_payload": "a" * 64,
    }
    with organizacao_atual_privilegiada(organizacao.pk):
        with pytest.raises(IntegrityError), transaction.atomic():
            EventoCobranca.objects.create(identificador_evento="evt-tipo-save", **campos)
        with pytest.raises(IntegrityError), transaction.atomic():
            EventoCobranca.objects.bulk_create([EventoCobranca(identificador_evento="evt-tipo-bulk", **campos)])
        with pytest.raises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO evento_cobranca
                   (created_at,last_modified_at,is_active,is_deleted,variante,identificador_evento,tipo,
                    status,exige_tenant,tentativas_roteamento,tentativas_processamento,payload_normalizado,
                    hash_payload,erro,organizacao_id,identificador_assinatura,identificador_checkout,identificador_fatura)
                   VALUES (NOW(),NOW(),true,false,'stripe','evt-tipo-raw','Invoice Paid',10,false,0,0,'{}',%s,'',%s,'','','')""",
                ["b" * 64, organizacao.pk],
            )


@pytest.mark.django_db(transaction=True)
def test_interface_ingresso_recusa_tipo_fora_da_gramatica(papel_ingresso):
    database = settings.DATABASES["default"]
    conexao = psycopg2.connect(dbname=database["NAME"], user=PAPEL_INGRESSO, password=SENHA_INGRESSO, host=database["HOST"], port=database["PORT"])
    try:
        with conexao, conexao.cursor() as cursor:
            cursor.execute("SET ROLE billing_ingress_runtime")
            cursor.execute("SELECT set_config('rls.tenant_id', '0', true)")
            cursor.execute("SELECT set_config('rls.billing_ingress', '1', true)")
            with pytest.raises(psycopg2.errors.InvalidParameterValue):
                cursor.execute(
                    "SELECT * FROM faturamento_ingress_evento(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    ["stripe", "evt-tipo-funcao", "Invoice Paid", "", "", "", False, "{}", "c" * 64, None],
                )
    finally:
        conexao.close()

    organizacao = Organizacao.objects.create(nome="Payload direto", slug="payload-direto-invalido")
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


@pytest.mark.parametrize("chave", ["period_start", "period_end"])
@pytest.mark.parametrize("timestamp", ["2026-02-30T00:00:00Z", "2026-01-01T23:59:60Z", "2026-01-01T24:00:00Z"])
def test_timestamp_invalido_nao_persiste_por_save_bulk_ou_sql_raw(chave, timestamp):
    organizacao = Organizacao.objects.create(nome=f"Timestamp {chave}", slug=f"timestamp-{chave}")
    dados = {
        "organizacao": organizacao,
        "variante": "stripe",
        "tipo": "invoice.paid",
        "exige_tenant": False,
        "payload_normalizado": {chave: timestamp},
        "hash_payload": "f" * 64,
    }
    with organizacao_atual_privilegiada(organizacao.pk), pytest.raises(ValidationError):
        EventoCobranca.objects.create(identificador_evento=f"evt-save-{chave}", **dados)
    with organizacao_atual_privilegiada(organizacao.pk), pytest.raises(DatabaseError), transaction.atomic():
        EventoCobranca.objects.bulk_create([EventoCobranca(identificador_evento=f"evt-bulk-{chave}", **dados)])
    with organizacao_atual_privilegiada(organizacao.pk), pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            """INSERT INTO evento_cobranca
               (created_at,last_modified_at,is_active,is_deleted,variante,identificador_evento,tipo,organizacao_id,status,exige_tenant,
                tentativas_roteamento,tentativas_processamento,payload_normalizado,hash_payload,erro,
                identificador_assinatura,identificador_checkout,identificador_fatura)
               VALUES (NOW(),NOW(),true,false,'stripe',%s,'invoice.paid',%s,10,false,0,0,
                       jsonb_build_object(%s,%s),repeat('f',64),'','','','')""",
            [f"evt-raw-{chave}-{timestamp}", organizacao.pk, chave, timestamp],
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


def test_unicidades_e_moedas_sao_impostas_isoladamente_pelo_postgresql():
    org_a = Organizacao.objects.create(nome="Isolada A", slug="isolada-a")
    org_b = Organizacao.objects.create(nome="Isolada B", slug="isolada-b")
    versao = _criar_versao(codigo="isolada")
    assinatura_a = _criar_assinatura(org_a, versao)
    assinatura_b = _criar_assinatura(org_b, versao)
    AssinaturaGateway.objects.create(organizacao=org_a, assinatura=assinatura_a, variante="stripe", identificador_externo="sub-a")
    preco = PrecoPlano.objects.get(versao_plano=versao)
    ReferenciaPrecoGateway.objects.create(preco_plano=preco, variante="stripe", componente=ComponentePreco.BASE, identificador_externo="price-global")

    with pytest.raises(IntegrityError), transaction.atomic():
        AssinaturaGateway.objects.create(organizacao=org_a, assinatura=assinatura_a, variante="outro", identificador_externo="sub-outro")
    with pytest.raises(IntegrityError), transaction.atomic():
        ReferenciaPrecoGateway.objects.create(
            preco_plano=preco, variante="stripe", componente=ComponentePreco.SEAT, identificador_externo="price-global"
        )

    with organizacao_atual_privilegiada(org_a.pk):
        CheckoutCobranca.objects.create(
            organizacao=org_a,
            assinatura=assinatura_a,
            finalidade=FinalidadeCheckout.FORMA_PAGAMENTO,
            chave_idempotencia="checkout-idem",
            variante="stripe",
            valor_esperado_centavos=0,
            moeda_esperada="BRL",
        )
        FaturaAssinatura.objects.create(
            organizacao=org_a,
            assinatura=assinatura_a,
            variante="stripe",
            identificador_externo="invoice-org",
            status=StatusFatura.ABERTA,
            moeda="BRL",
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            CheckoutCobranca.objects.create(
                organizacao=org_a,
                assinatura=assinatura_a,
                finalidade=FinalidadeCheckout.FORMA_PAGAMENTO,
                chave_idempotencia="checkout-idem",
                variante="stripe",
                valor_esperado_centavos=0,
                moeda_esperada="BRL",
            )
        with pytest.raises(IntegrityError), transaction.atomic():
            FaturaAssinatura.objects.create(
                organizacao=org_a,
                assinatura=assinatura_a,
                variante="stripe",
                identificador_externo="invoice-org",
                status=StatusFatura.ABERTA,
                moeda="BRL",
            )
        for modelo, dados in (
            (
                CheckoutCobranca,
                {
                    "assinatura": assinatura_a,
                    "finalidade": FinalidadeCheckout.FORMA_PAGAMENTO,
                    "chave_idempotencia": "moeda-invalida",
                    "variante": "stripe",
                    "valor_esperado_centavos": 0,
                    "moeda_esperada": "brl",
                },
            ),
            (
                FaturaAssinatura,
                {
                    "assinatura": assinatura_a,
                    "variante": "stripe",
                    "identificador_externo": "invoice-moeda-invalida",
                    "status": StatusFatura.ABERTA,
                    "moeda": "EU1",
                },
            ),
        ):
            with pytest.raises(IntegrityError), transaction.atomic():
                modelo.objects.create(organizacao=org_a, **dados)

    with organizacao_atual_privilegiada(org_b.pk):
        assert CheckoutCobranca.objects.create(
            organizacao=org_b,
            assinatura=assinatura_b,
            finalidade=FinalidadeCheckout.FORMA_PAGAMENTO,
            chave_idempotencia="checkout-idem",
            variante="stripe",
            identificador_externo=None,
            valor_esperado_centavos=0,
            moeda_esperada="USD",
        ).pk


def test_finalidades_validas_e_relacoes_invalidas_sao_impostas_pelo_postgresql():
    org_a = Organizacao.objects.create(nome="Finalidade A", slug="finalidade-a")
    org_b = Organizacao.objects.create(nome="Finalidade B", slug="finalidade-b")
    versao = _criar_versao(codigo="finalidade")
    assinatura_pendente = _criar_assinatura(
        org_a,
        versao,
        status=StatusAssinatura.PENDENTE,
        status_financeiro=StatusFinanceiro.PENDENTE,
    )
    assinatura_b = _criar_assinatura(org_b, versao)
    with organizacao_atual_privilegiada(org_a.pk):
        assert CheckoutCobranca.objects.create(
            organizacao=org_a,
            assinatura=assinatura_pendente,
            finalidade=FinalidadeCheckout.CONTRATACAO,
            chave_idempotencia="contratacao-valida",
            variante="stripe",
            valor_esperado_centavos=100,
            moeda_esperada="BRL",
        ).pk
        proposta_rascunho = _criar_proposta(org_a)
    with organizacao_atual_privilegiada(org_a.pk):
        with pytest.raises(IntegrityError), transaction.atomic():
            CheckoutCobranca.objects.create(
                organizacao=org_a,
                assinatura=assinatura_pendente,
                proposta=proposta_rascunho,
                finalidade=FinalidadeCheckout.PROPOSTA,
                chave_idempotencia="proposta-nao-aceita",
                variante="stripe",
                valor_esperado_centavos=100,
                moeda_esperada="BRL",
            )
    with organizacao_atual_privilegiada(org_a.pk):
        alteracao_a = AlteracaoAssinatura.objects.create(
            organizacao=org_a,
            assinatura=assinatura_pendente,
            tipo=TipoAlteracaoAssinatura.AUMENTO_SEATS,
            momento_aplicacao=MomentoAplicacaoAlteracaoAssinatura.IMEDIATA,
            revisao_esperada=assinatura_pendente.revisao,
            chave_idempotencia="alteracao-cross-org",
            pedido={},
            snapshot_anterior={},
            snapshot_pretendido={},
        )
    with organizacao_atual_privilegiada(org_b.pk):
        with pytest.raises(IntegrityError), transaction.atomic():
            CheckoutCobranca.objects.create(
                organizacao=org_b,
                assinatura=assinatura_b,
                proposta=proposta_rascunho,
                finalidade=FinalidadeCheckout.PROPOSTA,
                chave_idempotencia="proposta-cross-org",
                variante="stripe",
                valor_esperado_centavos=100,
                moeda_esperada="BRL",
            )
        with pytest.raises(IntegrityError), transaction.atomic():
            CheckoutCobranca.objects.create(
                organizacao=org_b,
                assinatura=assinatura_b,
                alteracao=alteracao_a,
                finalidade=FinalidadeCheckout.ALTERACAO,
                chave_idempotencia="alteracao-cross-org-checkout",
                variante="stripe",
                valor_esperado_centavos=100,
                moeda_esperada="BRL",
            )


@pytest.mark.django_db(transaction=True)
def test_rls_checkout_e_fatura_bloqueia_insert_e_update_cross_tenant(papel_ingresso):
    org_a = Organizacao.objects.create(nome="Mutacao RLS A", slug="mutacao-rls-a")
    org_b = Organizacao.objects.create(nome="Mutacao RLS B", slug="mutacao-rls-b")
    versao = _criar_versao(codigo="mutacao-rls")
    assinatura_a = _criar_assinatura(org_a, versao)
    assinatura_b = _criar_assinatura(org_b, versao)
    with organizacao_atual_privilegiada(org_b.pk):
        checkout_b = CheckoutCobranca.objects.create(
            organizacao=org_b,
            assinatura=assinatura_b,
            finalidade=FinalidadeCheckout.FORMA_PAGAMENTO,
            chave_idempotencia="rls-b",
            variante="stripe",
            valor_esperado_centavos=0,
            moeda_esperada="BRL",
        )
        fatura_b = FaturaAssinatura.objects.create(
            organizacao=org_b,
            assinatura=assinatura_b,
            variante="stripe",
            identificador_externo="invoice-rls-b",
            status=StatusFatura.ABERTA,
            moeda="BRL",
        )
    database = settings.DATABASES["default"]
    conexao = psycopg2.connect(dbname=database["NAME"], user=PAPEL_INGRESSO, password=SENHA_INGRESSO, host=database["HOST"], port=database["PORT"])
    try:
        with conexao, conexao.cursor() as cursor:
            cursor.execute("SELECT set_config('rls.tenant_id', %s, true)", [str(org_a.pk)])
            cursor.execute("UPDATE checkout_cobranca SET status=20 WHERE id=%s", [checkout_b.pk])
            assert cursor.rowcount == 0
            cursor.execute("UPDATE fatura_assinatura SET status=20 WHERE id=%s", [fatura_b.pk])
            assert cursor.rowcount == 0
        for tabela, valores in (
            (
                "checkout_cobranca",
                [org_b.pk, assinatura_a.pk, "rls-insert-checkout"],
            ),
            (
                "fatura_assinatura",
                [org_b.pk, assinatura_a.pk, "rls-insert-fatura"],
            ),
        ):
            if tabela == "checkout_cobranca":
                sql = """INSERT INTO checkout_cobranca
                    (created_at,last_modified_at,is_active,is_deleted,organizacao_id,assinatura_id,finalidade,status,
                     chave_idempotencia,variante,identificador_externo,url,valor_esperado_centavos,moeda_esperada)
                    VALUES (NOW(),NOW(),true,false,%s,%s,40,10,%s,'stripe',NULL,'',0,'BRL')"""
            else:
                sql = """INSERT INTO fatura_assinatura
                    (created_at,last_modified_at,is_active,is_deleted,organizacao_id,assinatura_id,variante,identificador_externo,
                     status,motivo,subtotal_centavos,desconto_centavos,imposto_centavos,total_centavos,moeda,tentativas,url_hospedada)
                    VALUES (NOW(),NOW(),true,false,%s,%s,'stripe',%s,10,'',0,0,0,0,'BRL',0,'')"""
            with conexao.cursor() as cursor:
                cursor.execute("SELECT set_config('rls.tenant_id', %s, false)", [str(org_a.pk)])
                with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                    cursor.execute(sql, valores)
            conexao.rollback()
    finally:
        conexao.close()


@pytest.mark.django_db(transaction=True)
def test_migration_initial_zero_aplica_reverte_reaplica_atomicamente(papel_ingresso):
    def catalogo():
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT p.proname, p.prosecdef, owner.rolname,
                          has_function_privilege('public',p.oid,'EXECUTE')
                   FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                   JOIN pg_roles owner ON owner.oid=p.proowner
                   WHERE n.nspname='public' AND p.proname LIKE 'faturamento_%'
                   ORDER BY 1"""
            )
            funcoes = cursor.fetchall()
            cursor.execute(
                """SELECT polname, polcmd, polroles
                   FROM pg_policy WHERE polrelid='public.evento_cobranca'::regclass ORDER BY polname"""
            )
            policies = cursor.fetchall()
            cursor.execute(
                """SELECT grantee, table_name, privilege_type
                   FROM information_schema.role_table_grants
                   WHERE table_schema='public' AND grantee='billing_ingress_runtime'
                   ORDER BY table_name, privilege_type"""
            )
            grants = cursor.fetchall()
            cursor.execute(
                """SELECT grantee, table_name, column_name, privilege_type
                   FROM information_schema.role_column_grants
                   WHERE table_schema='public' AND grantee='billing_ingress_runtime'
                   ORDER BY table_name, column_name, privilege_type"""
            )
            grants_colunas = cursor.fetchall()
        return funcoes, policies, grants, grants_colunas

    executor = MigrationExecutor(connection)
    folhas = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate([("faturamento", None)])
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('public.evento_cobranca')")
            assert cursor.fetchone() == (None,)
        executor = MigrationExecutor(connection)
        executor.migrate([("faturamento", "0001_initial")])
        estado = catalogo()
        assert [(nome, definer, owner, publico) for nome, definer, owner, publico in estado[0] if definer] == [
            ("faturamento_receber_evento", True, settings.BILLING_DATABASE_OWNER_ROLE, False),
            ("faturamento_rotear_evento", True, settings.BILLING_DATABASE_OWNER_ROLE, False),
        ]
        assert estado[2:] == ([], [])
        executor = MigrationExecutor(connection)
        executor.migrate([("faturamento", None)])
        with connection.cursor() as cursor:
            cursor.execute("ALTER ROLE billing_functions_owner REPLICATION")
        try:
            with pytest.raises(RuntimeError):
                MigrationExecutor(connection).migrate([("faturamento", "0001_initial")])
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT to_regclass('public.evento_cobranca'),to_regprocedure("
                    "'public.faturamento_receber_evento(text,text,text,text,text,text,boolean,jsonb,text,timestamptz)')"
                )
                assert cursor.fetchone() == (None, None)
        finally:
            with connection.cursor() as cursor:
                cursor.execute("ALTER ROLE billing_functions_owner NOREPLICATION")
        executor = MigrationExecutor(connection)
        executor.migrate([("faturamento", "0001_initial")])
        assert catalogo() == estado
    finally:
        MigrationExecutor(connection).migrate(folhas)


@pytest.mark.django_db(transaction=True)
def test_migrations_0009_0011_revertem_com_auditoria_automatica_e_fatos_nulos(papel_ingresso):
    organizacao = Organizacao.objects.create(nome="Reverse financeiro", slug="reverse-financeiro")
    assinatura = _criar_assinatura(organizacao, _criar_versao(codigo="reverse-financeiro"))
    with organizacao_atual_privilegiada(organizacao.pk):
        evento = EventoCobranca.objects.create(
            organizacao=organizacao,
            variante="stripe",
            identificador_evento="evt_reverse_financeiro",
            tipo="invoice.paid",
            status=StatusEventoCobranca.FALHOU,
            hash_payload="b" * 64,
        )
        ReaberturaEventoCobranca.objects.create(
            organizacao=organizacao,
            evento=evento,
            motivo="Reconciliação automática",
            ator=None,
            automatica=True,
            chave_idempotencia="system:reverse",
            tentativas_anteriores=1,
        )
        FaturaAssinatura.objects.create(
            organizacao=organizacao,
            assinatura=assinatura,
            variante="stripe",
            identificador_externo="in_reverse_financeiro",
            status=StatusFatura.ABERTA,
            moeda="BRL",
            subtotal_centavos=None,
            desconto_centavos=None,
            imposto_centavos=None,
            total_centavos=None,
        )

    folhas = MigrationExecutor(connection).loader.graph.leaf_nodes()
    try:
        MigrationExecutor(connection).migrate([("faturamento", "0008_fronteira_recovery_e_rls_auditoria")])
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT is_nullable FROM information_schema.columns WHERE table_name='reabertura_evento_cobranca' AND column_name='ator_id'"
            )
            assert cursor.fetchone() == ("NO",)
            cursor.execute("SELECT COUNT(*) FROM reabertura_evento_cobranca")
            assert cursor.fetchone() == (0,)
            cursor.execute(
                "SELECT subtotal_centavos,desconto_centavos,imposto_centavos,total_centavos "
                "FROM fatura_assinatura WHERE identificador_externo='in_reverse_financeiro'"
            )
            assert cursor.fetchone() == (0, 0, 0, 0)
        MigrationExecutor(connection).migrate(folhas)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name='evento_cobranca' AND column_name='recuperacao_arrendada_ate'"
            )
            assert cursor.fetchone() == ("recuperacao_arrendada_ate",)
    finally:
        MigrationExecutor(connection).migrate(folhas)
