"""Rollout seguro das assinaturas gratuitas para organizações existentes."""

from io import StringIO

from django.core.management import CommandError, call_command
from django.db import NotSupportedError, connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

import psycopg2
import pytest

from apps.assinaturas.catalogs import PLANOS_BOOTSTRAP, sincronizar_planos
from apps.assinaturas.checks import organizacoes_ativas_sem_assinatura_check
from apps.assinaturas.models import AssinaturaOrganizacao
from apps.assinaturas.operational import FinalidadeOperacionalAssinatura, selecionar_organizacoes_operacionais
from apps.assinaturas.subscriptions import Assinaturas
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Organizacao

from .test_subscription_access_transitions import _assinatura_ativa

pytestmark = pytest.mark.django_db

PAPEL_OPERACIONAL_ASSINATURAS = "subscription_operations_tester"
SENHA_OPERACIONAL_ASSINATURAS = "subscription_operations_tester"
ASSINATURAS_SELECTOR_REGPROCEDURE = "selecionar_organizacoes_operacionais_assinatura(text,bigint,integer,timestamp with time zone)"


@pytest.fixture
def papel_operacional_assinaturas(django_db_setup, django_db_blocker):
    """Papel comum com os privilégios mínimos consumidos pelo selector invoker."""
    with django_db_blocker.unblock(), connection.cursor() as cursor:
        cursor.execute(f"DROP ROLE IF EXISTS {PAPEL_OPERACIONAL_ASSINATURAS}")
        cursor.execute(
            f"CREATE ROLE {PAPEL_OPERACIONAL_ASSINATURAS} LOGIN PASSWORD %s NOSUPERUSER NOBYPASSRLS NOINHERIT",
            [SENHA_OPERACIONAL_ASSINATURAS],
        )
        cursor.execute(f"GRANT USAGE ON SCHEMA public TO {PAPEL_OPERACIONAL_ASSINATURAS}")
        cursor.execute(f"GRANT SELECT ON organizacao, assinatura_organizacao, vinculo TO {PAPEL_OPERACIONAL_ASSINATURAS}")
        cursor.execute("SELECT to_regprocedure(%s)", [ASSINATURAS_SELECTOR_REGPROCEDURE])
        if cursor.fetchone()[0] is not None:
            cursor.execute(
                "GRANT EXECUTE ON FUNCTION selecionar_organizacoes_operacionais_assinatura"
                "(text,bigint,integer,timestamp with time zone) "
                f"TO {PAPEL_OPERACIONAL_ASSINATURAS}"
            )

    yield

    with django_db_blocker.unblock(), connection.cursor() as cursor:
        cursor.execute(f"DROP OWNED BY {PAPEL_OPERACIONAL_ASSINATURAS}")
        cursor.execute(f"DROP ROLE IF EXISTS {PAPEL_OPERACIONAL_ASSINATURAS}")


def _conexao_operacional():
    parametros = connection.settings_dict
    return psycopg2.connect(
        dbname=parametros["NAME"],
        user=PAPEL_OPERACIONAL_ASSINATURAS,
        password=SENHA_OPERACIONAL_ASSINATURAS,
        host=parametros["HOST"] or "127.0.0.1",
        port=parametros["PORT"] or 5432,
    )


def _command(*args):
    stdout = StringIO()
    call_command("initialize_subscriptions", *args, stdout=stdout)
    return stdout.getvalue()


def test_dry_run_e_padrao_e_nao_persiste_contratos():
    sincronizar_planos(PLANOS_BOOTSTRAP, aplicar=True)
    organizacoes = [Organizacao.objects.create(nome=f"Legada {indice}", slug=f"legada-dry-{indice}") for indice in range(2)]

    output = _command("--batch-size", "1")

    assert "DRY-RUN" in output
    assert "2 organização(ões)" in output
    for organizacao in organizacoes:
        with organizacao_atual_privilegiada(organizacao.pk):
            assert AssinaturaOrganizacao.all_objects.filter(organizacao=organizacao).exists() is False


def test_apply_inicializa_todas_em_lotes_e_repeticao_e_idempotente():
    sincronizar_planos(PLANOS_BOOTSTRAP, aplicar=True)
    organizacoes = [Organizacao.objects.create(nome=f"Legada {indice}", slug=f"legada-apply-{indice}") for indice in range(3)]

    primeira = _command("--apply", "--batch-size", "1")
    repetida = _command("--apply", "--batch-size", "1")

    assert "APLICADO" in primeira
    assert "3 organização(ões)" in primeira
    assert "0 organização(ões)" in repetida
    for organizacao in organizacoes:
        with organizacao_atual_privilegiada(organizacao.pk):
            assinatura = Assinaturas.obter_corrente(organizacao)
        assert assinatura is not None
        assert assinatura.versao_plano.plano.codigo == "gratuito"


def test_apply_nao_sobrescreve_contrato_corrente_nem_inicializa_organizacao_inativa():
    sincronizar_planos(PLANOS_BOOTSTRAP, aplicar=True)
    organizacao_paga, assinatura_paga = _assinatura_ativa(slug="rollout-paga")
    inativa = Organizacao.objects.create(nome="Inativa", slug="rollout-inativa", is_active=False)

    output = _command("--apply")

    assert "0 organização(ões)" in output
    with organizacao_atual_privilegiada(organizacao_paga.pk):
        assinatura_paga.refresh_from_db()
        assert AssinaturaOrganizacao.all_objects.filter(organizacao=organizacao_paga).count() == 1
    with organizacao_atual_privilegiada(inativa.pk):
        assert AssinaturaOrganizacao.all_objects.filter(organizacao=inativa).exists() is False


def test_comando_exige_catalogo_gratuito_e_lote_positivo():
    Organizacao.objects.create(nome="Sem catálogo", slug="rollout-sem-catalogo")

    with pytest.raises(CommandError, match="sync_plans"):
        _command("--apply")

    sincronizar_planos(PLANOS_BOOTSTRAP, aplicar=True)
    with pytest.raises(CommandError, match="positivo"):
        _command("--apply", "--batch-size", "0")


def test_system_check_deploy_detecta_lacuna_em_uma_unica_query_e_ignora_inativas():
    sincronizar_planos(PLANOS_BOOTSTRAP, aplicar=True)
    ativa = Organizacao.objects.create(nome="Ativa sem assinatura", slug="check-rollout-ativa")
    Organizacao.objects.create(nome="Inativa sem assinatura", slug="check-rollout-inativa", is_active=False)

    with CaptureQueriesContext(connection) as queries:
        erros = organizacoes_ativas_sem_assinatura_check(None)

    assert len(queries) == 1
    assert [erro.id for erro in erros] == ["assinaturas.E003"]

    _command("--apply")
    assert organizacoes_ativas_sem_assinatura_check(None) == []
    with organizacao_atual_privilegiada(ativa.pk):
        assert Assinaturas.obter_corrente(ativa) is not None


@pytest.mark.django_db(transaction=True)
def test_selector_operacional_enxerga_lacuna_sob_papel_comum_e_restaura_contexto(
    papel_operacional_assinaturas,
):
    sincronizar_planos(PLANOS_BOOTSTRAP, aplicar=True)
    existente, _ = _assinatura_ativa(slug="selector-com-contrato")
    lacuna = Organizacao.objects.create(nome="Selector sem contrato", slug="selector-sem-contrato")

    conexao = _conexao_operacional()
    try:
        with conexao, conexao.cursor() as cursor:
            cursor.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            assert cursor.fetchone() == (False, False)
            cursor.execute("SELECT set_config('rls.tenant_id', %s, true)", [str(existente.pk)])
            cursor.execute(
                """
                SELECT organizacao_id
                FROM selecionar_organizacoes_operacionais_assinatura(%s, %s, %s, %s)
                """,
                ["rollout", 0, 10, timezone.now()],
            )
            ids = [row[0] for row in cursor.fetchall()]
            cursor.execute("SELECT current_setting('rls.tenant_id', true)")
            contexto_restaurado = cursor.fetchone()[0]
    finally:
        conexao.close()

    assert lacuna.pk in ids
    assert existente.pk not in ids
    assert contexto_restaurado == str(existente.pk)


@pytest.mark.parametrize(
    ("finalidade", "kwargs", "mensagem"),
    [
        ("rollout", {"limite": 1}, "Finalidade"),
        (FinalidadeOperacionalAssinatura.ROLLOUT, {"apos_id": -1, "limite": 1}, "Cursor"),
        (FinalidadeOperacionalAssinatura.ROLLOUT, {"limite": 0}, "Limite"),
        (
            FinalidadeOperacionalAssinatura.ROLLOUT,
            {"limite": 1, "referencia": timezone.make_naive(timezone.now())},
            "datetime consciente",
        ),
    ],
)
def test_selector_operacional_rejeita_argumentos_ambiguos(finalidade, kwargs, mensagem):
    with pytest.raises(ValueError, match=mensagem):
        selecionar_organizacoes_operacionais(finalidade, **kwargs)


@pytest.mark.django_db(databases={"default", "logging"})
def test_selector_operacional_rejeita_backend_sem_force_rls():
    with pytest.raises(NotSupportedError, match="PostgreSQL"):
        selecionar_organizacoes_operacionais(
            FinalidadeOperacionalAssinatura.ROLLOUT,
            limite=1,
            using="logging",
        )
