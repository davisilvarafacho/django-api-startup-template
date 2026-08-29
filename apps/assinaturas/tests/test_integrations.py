"""Integrações reais do contrato com onboarding, ciclo e seats."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

from django.db import close_old_connections, connection, connections
from django.utils import timezone

import psycopg2
import pytest

from apps.api.core.errors import APIError
from apps.assinaturas.catalogs import PLANOS_BOOTSTRAP, CatalogoPlanos, sincronizar_planos
from apps.assinaturas.errors import BillingErrorCode
from apps.assinaturas.models import (
    AlteracaoAssinatura,
    AssinaturaOrganizacao,
    MomentoAplicacaoAlteracaoAssinatura,
    Periodicidade,
    StatusAssinatura,
    TipoAlteracaoAssinatura,
)
from apps.assinaturas.subscriptions import Assinaturas, UtilizacaoSeats
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.memberships import OcupacaoSeats, Vinculos
from apps.organizacoes.models import Convite, Organizacao, Papel, Vinculo
from apps.organizacoes.onboarding import OrganizationOnboarding
from apps.organizacoes.organizations import EncerramentoEfetivado, Organizacoes, TermoEncerramentoImediato
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db

PAPEL_RLS_ASSINATURAS = "subscription_rls_tester"
SENHA_RLS_ASSINATURAS = "subscription_rls_tester"


@pytest.fixture
def papel_rls_assinaturas(django_db_setup, django_db_blocker):
    """Concede somente SELECT a um papel comum, sujeito às policies reais."""
    with django_db_blocker.unblock(), connection.cursor() as cursor:
        cursor.execute(f"DROP ROLE IF EXISTS {PAPEL_RLS_ASSINATURAS}")
        cursor.execute(f"CREATE ROLE {PAPEL_RLS_ASSINATURAS} LOGIN PASSWORD '{SENHA_RLS_ASSINATURAS}'")
        cursor.execute(f"GRANT USAGE ON SCHEMA public TO {PAPEL_RLS_ASSINATURAS}")
        cursor.execute(f"GRANT SELECT ON assinatura_organizacao, alteracao_assinatura TO {PAPEL_RLS_ASSINATURAS}")

    yield

    with django_db_blocker.unblock(), connection.cursor() as cursor:
        cursor.execute(f"DROP OWNED BY {PAPEL_RLS_ASSINATURAS}")
        cursor.execute(f"DROP ROLE IF EXISTS {PAPEL_RLS_ASSINATURAS}")


def _sincronizar_catalogo():
    sincronizar_planos(PLANOS_BOOTSTRAP, aplicar=True)


def _organizacao_com_contrato(*, seats=2, slug="seats"):
    _sincronizar_catalogo()
    versao, preco = CatalogoPlanos.obter_versao_inicial(codigo="profissional", periodicidade=Periodicidade.MENSAL)
    organizacao = Organizacao.objects.create(nome="Seats", slug=slug)
    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura = Assinaturas.criar_paga(
            organizacao=organizacao,
            versao_plano=versao,
            preco_plano=preco,
            seats_contratados=seats,
            chave_idempotencia=f"contrato-{slug}",
        )
    return organizacao, assinatura


@pytest.mark.parametrize(
    ("modo", "plano", "status_esperado"),
    [
        ("gratuito", "gratuito", StatusAssinatura.ATIVA),
        ("trial", "profissional", StatusAssinatura.EM_TRIAL),
    ],
)
def test_onboarding_real_resolve_catalogo_e_cria_contrato_na_mesma_transacao(settings, modo, plano, status_esperado):
    _sincronizar_catalogo()
    settings.ASSINATURAS_ONBOARDING_MODO = modo
    settings.ASSINATURAS_ONBOARDING_PLANO = plano
    settings.ASSINATURAS_ONBOARDING_PERIODICIDADE = "mensal"
    usuario = criar_usuario(email=f"{modo}@example.com", email_verificado_em=timezone.now())

    organizacao = OrganizationOnboarding.criar(usuario=usuario, nome=modo.title(), slug=f"onboarding-real-{modo}")

    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura = AssinaturaOrganizacao.objects.get(organizacao=organizacao)
    assert assinatura.status == status_esperado
    assert assinatura.versao_plano.plano.codigo == plano
    assert Vinculo.objects.get(organizacao=organizacao, usuario=usuario).papel == Papel.PROPRIETARIO


def test_onboarding_real_reverte_inclusive_contrato_quando_etapa_final_falha(monkeypatch, settings):
    _sincronizar_catalogo()
    settings.ASSINATURAS_ONBOARDING_MODO = "gratuito"
    settings.ASSINATURAS_ONBOARDING_PLANO = "gratuito"
    settings.ASSINATURAS_ONBOARDING_PERIODICIDADE = "mensal"
    usuario = criar_usuario(email="rollback-real@example.com", email_verificado_em=timezone.now())
    original = Assinaturas.criar.__func__
    organizacao_id = None

    def criar_e_falhar(cls, comando, **kwargs):
        nonlocal organizacao_id
        organizacao_id = comando.organizacao.pk
        original(cls, comando, **kwargs)
        raise RuntimeError("falha após persistir contrato")

    monkeypatch.setattr(Assinaturas, "criar", classmethod(criar_e_falhar))

    with pytest.raises(RuntimeError, match="após persistir contrato"):
        OrganizationOnboarding.criar(usuario=usuario, nome="Rollback", slug="onboarding-real-rollback")

    assert organizacao_id is not None
    assert Organizacao.all_objects.filter(pk=organizacao_id).exists() is False
    assert Vinculo.all_objects.filter(organizacao_id=organizacao_id).exists() is False
    with organizacao_atual_privilegiada(organizacao_id):
        assert AssinaturaOrganizacao.all_objects.filter(organizacao_id=organizacao_id).exists() is False


def test_calcular_utilizacao_de_seats_e_literal_e_nao_consulta_banco(django_assert_num_queries):
    _, assinatura = _organizacao_com_contrato(seats=5, slug="utilizacao")

    with django_assert_num_queries(0):
        utilizacao = Assinaturas.calcular_utilizacao(assinatura, OcupacaoSeats(consumidos=4, reservados=3))

    assert utilizacao == UtilizacaoSeats(
        contratados=5,
        consumidos=4,
        reservados=3,
        comprometidos=7,
        disponiveis=0,
        excesso_real=0,
        excesso_comprometido=2,
    )


def test_convite_real_bloqueia_quando_ultimo_seat_ja_esta_comprometido():
    organizacao, _ = _organizacao_com_contrato(seats=2, slug="limite")
    proprietario = criar_usuario(email="owner-limite@example.com")
    Vinculo.objects.create(organizacao=organizacao, usuario=proprietario, papel=Papel.PROPRIETARIO)
    Vinculos.criar_convite(
        organizacao=organizacao,
        email="primeiro@example.com",
        papel=Papel.MEMBRO,
        convidado_por=proprietario,
    )

    with pytest.raises(APIError) as excinfo:
        Vinculos.criar_convite(
            organizacao=organizacao,
            email="excesso@example.com",
            papel=Papel.MEMBRO,
            convidado_por=proprietario,
        )

    assert excinfo.value.code == BillingErrorCode.SEAT_LIMIT_REACHED
    assert excinfo.value.context == {"contratados": 2, "consumidos": 1, "reservados": 1}
    assert Convite.objects.filter(organizacao=organizacao).count() == 1


def test_aceite_troca_reserva_por_consumo_sem_exceder_capacidade():
    organizacao, _ = _organizacao_com_contrato(seats=1, slug="aceite-reserva")
    usuario = criar_usuario(email="aceite-reserva@example.com")
    convite = Convite.objects.create(
        organizacao=organizacao,
        email=usuario.email,
        papel=Papel.MEMBRO,
        expira_em=timezone.now() + timedelta(days=1),
    )

    vinculo = Vinculos.aceitar_convite(convite, usuario)

    assert vinculo.usuario == usuario
    assert Vinculos.calcular_ocupacao(organizacao, frozenset()) == OcupacaoSeats(consumidos=1, reservados=0)


@pytest.mark.django_db(transaction=True)
def test_duas_criacoes_concorrentes_conquistam_o_ultimo_seat_uma_unica_vez():
    organizacao, _ = _organizacao_com_contrato(seats=2, slug="ultimo-seat")
    proprietario = criar_usuario(email="owner-ultimo-seat@example.com")
    Vinculo.objects.create(organizacao=organizacao, usuario=proprietario, papel=Papel.PROPRIETARIO)
    barreira = Barrier(2)

    def convidar(numero):
        close_old_connections()
        try:
            organizacao_local = Organizacao.objects.get(pk=organizacao.pk)
            usuario_local = type(proprietario).objects.get(pk=proprietario.pk)
            barreira.wait(timeout=5)
            try:
                Vinculos.criar_convite(
                    organizacao=organizacao_local,
                    email=f"concorrente-{numero}@example.com",
                    papel=Papel.MEMBRO,
                    convidado_por=usuario_local,
                )
            except APIError as exc:
                return exc.code
            return "criado"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        resultados = list(executor.map(convidar, (1, 2)))

    assert sorted(resultados) == [BillingErrorCode.SEAT_LIMIT_REACHED, "criado"]
    assert Convite.objects.filter(organizacao=organizacao).count() == 1


def test_encerramento_real_gratuito_fecha_o_contrato_no_mesmo_atomic():
    _sincronizar_catalogo()
    versao, preco = CatalogoPlanos.obter_versao_inicial(codigo="gratuito", periodicidade=Periodicidade.MENSAL)
    organizacao = Organizacao.objects.create(nome="Encerramento", slug="encerramento-real")
    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura = Assinaturas.criar_gratuita(organizacao=organizacao, versao_plano=versao, preco_plano=preco)
    agora = timezone.now()

    termo = Assinaturas.obter_termo_encerramento(organizacao)
    resultado = Organizacoes.solicitar_encerramento(
        organizacao,
        termo,
        assinaturas=Assinaturas,
        agora=agora,
    )

    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura.refresh_from_db()
    assert termo == TermoEncerramentoImediato()
    assert isinstance(resultado, EncerramentoEfetivado)
    assert assinatura.status == StatusAssinatura.ENCERRADA
    assert assinatura.encerrada_em == agora
    assert assinatura.motivo_encerramento == "organization_closed"


def _chaves_visiveis_por_papel_comum(organizacao_id):
    parametros = connection.settings_dict
    conexao = psycopg2.connect(
        dbname=parametros["NAME"],
        user=PAPEL_RLS_ASSINATURAS,
        password=SENHA_RLS_ASSINATURAS,
        host=parametros["HOST"] or "127.0.0.1",
        port=parametros["PORT"] or 5432,
    )
    try:
        with conexao, conexao.cursor() as cursor:
            if organizacao_id is not None:
                cursor.execute("SELECT set_config('rls.tenant_id', %s, true)", [str(organizacao_id)])
            cursor.execute("SELECT chave_idempotencia FROM assinatura_organizacao ORDER BY chave_idempotencia")
            assinaturas = [linha[0] for linha in cursor.fetchall()]
            cursor.execute("SELECT chave_idempotencia FROM alteracao_assinatura ORDER BY chave_idempotencia")
            alteracoes = [linha[0] for linha in cursor.fetchall()]
            return assinaturas, alteracoes
    finally:
        conexao.close()


@pytest.mark.django_db(transaction=True)
def test_rls_real_isola_contratos_e_alteracoes_para_papel_postgresql_comum(papel_rls_assinaturas):
    org_a, assinatura_a = _organizacao_com_contrato(slug="rls-a")
    org_b, assinatura_b = _organizacao_com_contrato(slug="rls-b")
    for organizacao, assinatura, chave in (
        (org_a, assinatura_a, "alteracao-rls-a"),
        (org_b, assinatura_b, "alteracao-rls-b"),
    ):
        with organizacao_atual_privilegiada(organizacao.pk):
            AlteracaoAssinatura.objects.create(
                organizacao=organizacao,
                assinatura=assinatura,
                tipo=TipoAlteracaoAssinatura.AUMENTO_SEATS,
                momento_aplicacao=MomentoAplicacaoAlteracaoAssinatura.IMEDIATA,
                revisao_esperada=1,
                chave_idempotencia=chave,
                pedido={"revisao_esperada": 1},
                snapshot_anterior={"revisao": 1},
                snapshot_pretendido={"revisao": 2},
            )

    assert _chaves_visiveis_por_papel_comum(org_a.pk) == (["contrato-rls-a"], ["alteracao-rls-a"])
    assert _chaves_visiveis_por_papel_comum(org_b.pk) == (["contrato-rls-b"], ["alteracao-rls-b"])
    assert _chaves_visiveis_por_papel_comum(None) == ([], [])
