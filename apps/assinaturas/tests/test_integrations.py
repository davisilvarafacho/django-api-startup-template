"""Integrações reais do contrato com onboarding, ciclo e seats."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event
from time import monotonic, sleep

from django.db import DatabaseError, close_old_connections, connection, connections, transaction
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
    StatusAlteracaoAssinatura,
    StatusAssinatura,
    StatusFinanceiro,
    TipoAlteracaoAssinatura,
)
from apps.assinaturas.subscriptions import Assinaturas, ConflitoRevisaoAssinatura, UtilizacaoSeats
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.memberships import OcupacaoSeats, Vinculos
from apps.organizacoes.models import Convite, Organizacao, Papel, Vinculo
from apps.organizacoes.onboarding import OrganizationOnboarding
from apps.organizacoes.organizations import EncerramentoAgendado, EncerramentoEfetivado, Organizacoes, TermoEncerramentoImediato
from apps.usuarios.accounts import Contas
from apps.usuarios.models import Usuario
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
        cursor.execute(f"GRANT SELECT, INSERT, UPDATE ON assinatura_organizacao, alteracao_assinatura TO {PAPEL_RLS_ASSINATURAS}")

    yield

    with django_db_blocker.unblock(), connection.cursor() as cursor:
        cursor.execute(f"DROP OWNED BY {PAPEL_RLS_ASSINATURAS}")
        cursor.execute(f"DROP ROLE IF EXISTS {PAPEL_RLS_ASSINATURAS}")


def _sincronizar_catalogo():
    sincronizar_planos(PLANOS_BOOTSTRAP, aplicar=True)


def _organizacao_com_contrato(
    *,
    seats=2,
    slug="seats",
    expansao_automatica=False,
    status=StatusAssinatura.PENDENTE,
    periodo_atual=None,
):
    _sincronizar_catalogo()
    versao, preco = CatalogoPlanos.obter_versao_inicial(codigo="profissional", periodicidade=Periodicidade.MENSAL)
    organizacao = Organizacao.objects.create(nome="Seats", slug=slug)
    with organizacao_atual_privilegiada(organizacao.pk):
        if expansao_automatica or status != StatusAssinatura.PENDENTE or periodo_atual is not None:
            periodo_iniciado_em, periodo_termina_em = periodo_atual or (None, None)
            assinatura = AssinaturaOrganizacao.objects.create(
                organizacao=organizacao,
                versao_plano=versao,
                status=status,
                status_financeiro=StatusFinanceiro.REGULAR if status == StatusAssinatura.ATIVA else StatusFinanceiro.PENDENTE,
                periodicidade=preco.periodicidade,
                moeda=preco.moeda,
                valor_base_centavos=preco.valor_base_centavos,
                valor_seat_centavos=preco.valor_seat_centavos,
                seats_inclusos=versao.seats_inclusos,
                seats_contratados=seats,
                expansao_automatica_seats=True,
                recursos=versao.recursos,
                periodo_atual_iniciado_em=periodo_iniciado_em,
                periodo_atual_termina_em=periodo_termina_em,
                carencia_pagamento_dias=versao.carencia_pagamento_dias,
                carencia_excesso_seats_dias=versao.carencia_excesso_seats_dias,
                chave_idempotencia=f"contrato-{slug}",
            )
        else:
            assinatura = Assinaturas.criar_paga(
                organizacao=organizacao,
                versao_plano=versao,
                preco_plano=preco,
                seats_contratados=seats,
                chave_idempotencia=f"contrato-{slug}",
            )
    return organizacao, assinatura


def _pid_backend_atual() -> int:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_backend_pid()")
        return cursor.fetchone()[0]


def _esperar_lock_postgresql(pid: int, *, timeout: float = 5) -> None:
    limite = monotonic() + timeout
    while monotonic() < limite:
        with connection.cursor() as cursor:
            cursor.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", [pid])
            linha = cursor.fetchone()
        if linha is not None and linha[0] == "Lock":
            return
        sleep(0.01)
    raise AssertionError(f"A conexão PostgreSQL {pid} não aguardou lock dentro do limite.")


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
    usuario = criar_usuario(email="aceite-reserva@example.com", email_verificado_em=timezone.now())
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
def test_convite_e_desativacao_seguem_usuario_antes_de_organizacao_sem_deadlock(monkeypatch):
    organizacao, _ = _organizacao_com_contrato(seats=4, slug="ordem-convite-desativacao")
    convidado_por = criar_usuario(email="ordem-convite@example.com")
    outro_proprietario = criar_usuario(email="ordem-convite-outro-owner@example.com")
    Vinculo.objects.create(organizacao=organizacao, usuario=convidado_por, papel=Papel.PROPRIETARIO)
    Vinculo.objects.create(organizacao=organizacao, usuario=outro_proprietario, papel=Papel.PROPRIETARIO)
    organizacao_bloqueada = Event()
    liberar_convite = Event()
    desativacao_iniciada = Event()
    pid_desativacao = []
    bloquear_organizacao_original = Vinculos._bloquear_organizacao_aberta

    def bloquear_organizacao_e_aguardar(organizacao_id, *, using="default"):
        bloqueada = bloquear_organizacao_original(organizacao_id, using=using)
        organizacao_bloqueada.set()
        if not liberar_convite.wait(timeout=5):
            raise AssertionError("Convite não foi liberado pelo teste.")
        return bloqueada

    monkeypatch.setattr(Vinculos, "_bloquear_organizacao_aberta", staticmethod(bloquear_organizacao_e_aguardar))

    def convidar():
        close_old_connections()
        try:
            try:
                convite = Vinculos.criar_convite(
                    organizacao=Organizacao.objects.get(pk=organizacao.pk),
                    email="ordem-convite-destino@example.com",
                    papel=Papel.MEMBRO,
                    convidado_por=Usuario.objects.get(pk=convidado_por.pk),
                )
            except DatabaseError as exc:
                return ("database_error", getattr(exc.__cause__, "pgcode", None))
            return ("criado", convite.pk)
        finally:
            connections.close_all()

    def desativar():
        close_old_connections()
        try:
            pid_desativacao.append(_pid_backend_atual())
            desativacao_iniciada.set()
            try:
                conta = Contas.desativar(Usuario.objects.get(pk=convidado_por.pk))
            except DatabaseError as exc:
                return ("database_error", getattr(exc.__cause__, "pgcode", None))
            return ("desativada", conta.pk)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        convite_futuro = executor.submit(convidar)
        assert organizacao_bloqueada.wait(timeout=5)
        desativacao_futura = executor.submit(desativar)
        assert desativacao_iniciada.wait(timeout=5)
        try:
            _esperar_lock_postgresql(pid_desativacao[0])
        finally:
            liberar_convite.set()
        resultado_convite = convite_futuro.result(timeout=10)
        resultado_desativacao = desativacao_futura.result(timeout=10)

    assert resultado_convite[0] == "criado"
    assert resultado_desativacao == ("desativada", convidado_por.pk)


@pytest.mark.django_db(transaction=True)
def test_aceite_bloqueia_usuario_antes_da_organizacao():
    organizacao = Organizacao.objects.create(nome="Ordem aceite", slug="ordem-aceite")
    usuario = criar_usuario(email="ordem-aceite@example.com", email_verificado_em=timezone.now())
    convite = Convite.objects.create(
        organizacao=organizacao,
        email=usuario.email,
        papel=Papel.MEMBRO,
        expira_em=timezone.now() + timedelta(days=1),
    )
    usuario_bloqueado = Event()
    liberar_usuario = Event()
    aceite_iniciado = Event()
    pid_aceite = []

    def manter_usuario_bloqueado():
        close_old_connections()
        try:
            with transaction.atomic():
                Usuario.all_objects.select_for_update().get(pk=usuario.pk)
                usuario_bloqueado.set()
                if not liberar_usuario.wait(timeout=5):
                    raise AssertionError("Lock do usuário não foi liberado pelo teste.")
        finally:
            connections.close_all()

    def aceitar():
        close_old_connections()
        try:
            pid_aceite.append(_pid_backend_atual())
            aceite_iniciado.set()
            return Vinculos.aceitar_convite(
                Convite.objects.get(pk=convite.pk),
                Usuario.objects.get(pk=usuario.pk),
            ).pk
        finally:
            connections.close_all()

    erro_lock_organizacao = None
    with ThreadPoolExecutor(max_workers=2) as executor:
        usuario_futuro = executor.submit(manter_usuario_bloqueado)
        assert usuario_bloqueado.wait(timeout=5)
        aceite_futuro = executor.submit(aceitar)
        assert aceite_iniciado.wait(timeout=5)
        _esperar_lock_postgresql(pid_aceite[0])
        try:
            with transaction.atomic():
                Organizacao.all_objects.select_for_update(nowait=True).get(pk=organizacao.pk)
        except DatabaseError as exc:
            erro_lock_organizacao = getattr(exc.__cause__, "pgcode", None)
        finally:
            liberar_usuario.set()
        usuario_futuro.result(timeout=10)
        vinculo_id = aceite_futuro.result(timeout=10)

    assert erro_lock_organizacao is None
    assert Vinculo.objects.get(pk=vinculo_id).usuario_id == usuario.pk


def test_campos_de_usuario_sao_imutaveis_nas_atualizacoes_de_convite_e_vinculo():
    organizacao = Organizacao.objects.create(nome="FK usuário imutável", slug="fk-usuario-imutavel")
    usuario = criar_usuario(email="fk-usuario-imutavel@example.com")
    outro = criar_usuario(email="fk-usuario-imutavel-outro@example.com")
    vinculo = Vinculo.objects.create(organizacao=organizacao, usuario=usuario, papel=Papel.MEMBRO)
    convite = Convite.objects.create(
        organizacao=organizacao,
        email="fk-usuario-imutavel-convite@example.com",
        papel=Papel.MEMBRO,
        convidado_por=usuario,
    )

    for campo, valor in (("convidado_por", outro), ("convidado_por_id", outro.pk)):
        with pytest.raises(ValueError, match="convidado_por"):
            Vinculos.atualizar_convite(convite, dados={campo: valor})
    for campo, valor in (("usuario", outro), ("usuario_id", outro.pk)):
        with pytest.raises(ValueError, match="usuario"):
            Vinculos.atualizar_vinculo(vinculo, dados={campo: valor})


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


def test_expansao_automatica_cria_alteracao_idempotente_e_so_libera_convite_depois_da_confirmacao():
    organizacao, assinatura = _organizacao_com_contrato(seats=1, slug="expansao-automatica", expansao_automatica=True)
    proprietario = criar_usuario(email="owner-expansao@example.com")
    Vinculo.objects.create(organizacao=organizacao, usuario=proprietario, papel=Papel.PROPRIETARIO)

    for _ in range(2):
        with pytest.raises(APIError) as excinfo:
            Vinculos.criar_convite(
                organizacao=organizacao,
                email="expansao@example.com",
                papel=Papel.MEMBRO,
                convidado_por=proprietario,
            )
        assert excinfo.value.code == BillingErrorCode.SEAT_LIMIT_REACHED

    with organizacao_atual_privilegiada(organizacao.pk):
        alteracao = AlteracaoAssinatura.objects.get(organizacao=organizacao)
    assert alteracao.tipo == TipoAlteracaoAssinatura.AUMENTO_SEATS
    assert alteracao.status == StatusAlteracaoAssinatura.SOLICITADA
    assert alteracao.chave_idempotencia == f"auto-seats:{assinatura.pk}:1:2"
    assert alteracao.solicitada_por is None
    assert alteracao.snapshot_pretendido["seats_contratados"] == 2
    assert Convite.objects.filter(organizacao=organizacao).exists() is False

    with organizacao_atual_privilegiada(organizacao.pk):
        Assinaturas.confirmar_alteracao(alteracao)
    convite = Vinculos.criar_convite(
        organizacao=organizacao,
        email="expansao@example.com",
        papel=Papel.MEMBRO,
        convidado_por=proprietario,
    )

    assert convite.email == "expansao@example.com"
    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura.refresh_from_db()
    assert assinatura.seats_contratados == 2


def test_expansao_automatica_usa_snapshot_historico_e_preserva_erro_nominal_e_idempotencia():
    organizacao, assinatura = _organizacao_com_contrato(
        seats=1,
        slug="expansao-catalogo-historico",
        expansao_automatica=True,
    )
    proprietario = criar_usuario(email="owner-expansao-catalogo-historico@example.com")
    Vinculo.objects.create(organizacao=organizacao, usuario=proprietario, papel=Papel.PROPRIETARIO)
    versao_historica = assinatura.versao_plano
    versao_historica.is_active = False
    versao_historica.save(update_fields=["is_active"])

    for _ in range(2):
        with pytest.raises(APIError) as excinfo:
            Vinculos.criar_convite(
                organizacao=organizacao,
                email="expansao-catalogo-historico@example.com",
                papel=Papel.MEMBRO,
                convidado_por=proprietario,
            )
        assert excinfo.value.code == BillingErrorCode.SEAT_LIMIT_REACHED

    with organizacao_atual_privilegiada(organizacao.pk):
        alteracoes = list(AlteracaoAssinatura.objects.filter(organizacao=organizacao))
    assert len(alteracoes) == 1
    assert alteracoes[0].snapshot_pretendido["versao_plano_id"] == versao_historica.pk
    assert alteracoes[0].snapshot_pretendido["seats_contratados"] == 2


@pytest.mark.django_db(transaction=True)
def test_expansoes_automaticas_concorrentes_convergem_para_uma_alteracao_local():
    organizacao, assinatura = _organizacao_com_contrato(seats=1, slug="expansao-concorrente", expansao_automatica=True)
    proprietario = criar_usuario(email="owner-expansao-concorrente@example.com")
    Vinculo.objects.create(organizacao=organizacao, usuario=proprietario, papel=Papel.PROPRIETARIO)
    barreira = Barrier(2)

    def convidar(numero):
        close_old_connections()
        try:
            organizacao_local = Organizacao.objects.get(pk=organizacao.pk)
            proprietario_local = type(proprietario).objects.get(pk=proprietario.pk)
            barreira.wait(timeout=5)
            try:
                Vinculos.criar_convite(
                    organizacao=organizacao_local,
                    email=f"expansao-{numero}@example.com",
                    papel=Papel.MEMBRO,
                    convidado_por=proprietario_local,
                )
            except APIError as exc:
                return exc.code
            return "criado"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        resultados = list(executor.map(convidar, (1, 2)))

    assert resultados == [BillingErrorCode.SEAT_LIMIT_REACHED, BillingErrorCode.SEAT_LIMIT_REACHED]
    assert Convite.objects.filter(organizacao=organizacao).exists() is False
    with organizacao_atual_privilegiada(organizacao.pk):
        alteracoes = list(AlteracaoAssinatura.objects.filter(organizacao=organizacao))
    assert len(alteracoes) == 1
    assert alteracoes[0].snapshot_pretendido["seats_contratados"] == 2


def test_aceite_em_excesso_solicita_expansao_e_mantem_convite_pendente_ate_confirmacao():
    organizacao, assinatura = _organizacao_com_contrato(seats=1, slug="expansao-aceite", expansao_automatica=True)
    proprietario = criar_usuario(email="owner-expansao-aceite@example.com")
    convidado = criar_usuario(email="convidado-expansao-aceite@example.com", email_verificado_em=timezone.now())
    Vinculo.objects.create(organizacao=organizacao, usuario=proprietario, papel=Papel.PROPRIETARIO)
    convite = Convite.objects.create(
        organizacao=organizacao,
        email=convidado.email,
        papel=Papel.MEMBRO,
        expira_em=timezone.now() + timedelta(days=1),
    )

    with pytest.raises(APIError) as excinfo:
        Vinculos.aceitar_convite(convite, convidado)

    assert excinfo.value.code == BillingErrorCode.SEAT_LIMIT_REACHED
    convite.refresh_from_db()
    assert convite.aceito_em is None
    assert Vinculo.objects.filter(organizacao=organizacao, usuario=convidado).exists() is False
    with organizacao_atual_privilegiada(organizacao.pk):
        alteracao = AlteracaoAssinatura.objects.get(organizacao=organizacao)
        Assinaturas.confirmar_alteracao(alteracao)

    vinculo = Vinculos.aceitar_convite(convite, convidado)

    assert vinculo.usuario == convidado
    convite.refresh_from_db()
    assert convite.aceito_em is not None


def test_encerramento_real_gratuito_fecha_o_contrato_no_mesmo_atomic():
    _sincronizar_catalogo()
    versao, preco = CatalogoPlanos.obter_versao_inicial(codigo="gratuito", periodicidade=Periodicidade.MENSAL)
    organizacao = Organizacao.objects.create(nome="Encerramento", slug="encerramento-real")
    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura = Assinaturas.criar_gratuita(organizacao=organizacao, versao_plano=versao, preco_plano=preco)
    agora = timezone.now()

    termo = Assinaturas.obter_termo_encerramento(organizacao)
    resultado = Organizacoes.solicitar_encerramento(organizacao, assinaturas=Assinaturas, agora=agora)

    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura.refresh_from_db()
    assert termo == TermoEncerramentoImediato()
    assert isinstance(resultado, EncerramentoEfetivado)
    assert assinatura.status == StatusAssinatura.ENCERRADA
    assert assinatura.encerrada_em == agora
    assert assinatura.motivo_encerramento == "organization_closed"


def test_encerramento_de_contrato_pendente_nao_agenda_mesmo_se_houver_periodo():
    agora = timezone.now()
    organizacao, assinatura = _organizacao_com_contrato(
        seats=5,
        slug="encerramento-pendente",
        periodo_atual=(agora - timedelta(days=1), agora + timedelta(days=30)),
    )

    resultado = Organizacoes.solicitar_encerramento(organizacao, assinaturas=Assinaturas, agora=agora)

    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura.refresh_from_db()
    assert isinstance(resultado, EncerramentoEfetivado)
    assert assinatura.status == StatusAssinatura.ENCERRADA
    assert assinatura.cancelamento_agendado_para is None


def test_encerramento_pago_decide_e_persiste_contrato_cancelando_alteracoes_no_mesmo_atomic():
    agora = timezone.now()
    fim_periodo = agora + timedelta(days=30)
    organizacao, assinatura = _organizacao_com_contrato(
        seats=5,
        slug="encerramento-pago-atomico",
        expansao_automatica=True,
        status=StatusAssinatura.ATIVA,
        periodo_atual=(agora - timedelta(days=1), fim_periodo),
    )
    with organizacao_atual_privilegiada(organizacao.pk):
        alteracao = Assinaturas.solicitar_expansao_automatica(assinatura, seats_necessarios=6)

    resultado = Organizacoes.solicitar_encerramento(
        organizacao,
        assinaturas=Assinaturas,
        agora=agora,
    )

    organizacao.refresh_from_db()
    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura.refresh_from_db()
        alteracao.refresh_from_db()
        with pytest.raises(ConflitoRevisaoAssinatura):
            Assinaturas.confirmar_alteracao(alteracao)
    assert resultado == EncerramentoAgendado(agendado_para=fim_periodo)
    assert organizacao.encerramento_solicitado_em == agora
    assert organizacao.encerramento_agendado_para == fim_periodo
    assert assinatura.cancelamento_agendado_para == fim_periodo
    assert assinatura.revisao == 2
    assert alteracao.status == StatusAlteracaoAssinatura.CANCELADA
    assert alteracao.processada_em == agora


def test_cancelar_encerramento_pago_limpa_organizacao_e_assinatura_com_nova_revisao():
    agora = timezone.now()
    fim_periodo = agora + timedelta(days=30)
    organizacao, assinatura = _organizacao_com_contrato(
        seats=5,
        slug="cancelar-encerramento-pago",
        status=StatusAssinatura.ATIVA,
        periodo_atual=(agora - timedelta(days=1), fim_periodo),
    )
    Organizacoes.solicitar_encerramento(organizacao, assinaturas=Assinaturas, agora=agora)

    cancelado = Organizacoes.cancelar_encerramento(organizacao, assinaturas=Assinaturas)

    organizacao.refresh_from_db()
    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura.refresh_from_db()
    assert cancelado is True
    assert organizacao.encerramento_solicitado_em is None
    assert organizacao.encerramento_agendado_para is None
    assert assinatura.cancelamento_agendado_para is None
    assert assinatura.revisao == 3


@pytest.mark.django_db(transaction=True)
def test_encerramento_concorrente_com_confirmacao_serializa_sem_upgrade_apos_cancelamento():
    agora = timezone.now()
    fim_periodo = agora + timedelta(days=30)
    organizacao, assinatura = _organizacao_com_contrato(
        seats=5,
        slug="encerramento-confirmacao-concorrente",
        expansao_automatica=True,
        status=StatusAssinatura.ATIVA,
        periodo_atual=(agora - timedelta(days=1), fim_periodo),
    )
    with organizacao_atual_privilegiada(organizacao.pk):
        alteracao = Assinaturas.solicitar_expansao_automatica(assinatura, seats_necessarios=6)
    barreira = Barrier(2)

    def encerrar():
        close_old_connections()
        try:
            barreira.wait(timeout=5)
            return Organizacoes.solicitar_encerramento(organizacao, assinaturas=Assinaturas, agora=agora)
        finally:
            connections.close_all()

    def confirmar():
        close_old_connections()
        try:
            barreira.wait(timeout=5)
            with organizacao_atual_privilegiada(organizacao.pk):
                Assinaturas.confirmar_alteracao(alteracao, agora=agora)
            return "confirmada"
        except ConflitoRevisaoAssinatura:
            return "conflito"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        encerramento = executor.submit(encerrar)
        confirmacao = executor.submit(confirmar)
        resultado_encerramento = encerramento.result(timeout=10)
        resultado_confirmacao = confirmacao.result(timeout=10)

    organizacao.refresh_from_db()
    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura.refresh_from_db()
        alteracao.refresh_from_db()
    assert resultado_encerramento == EncerramentoAgendado(agendado_para=fim_periodo)
    assert assinatura.cancelamento_agendado_para == fim_periodo
    if resultado_confirmacao == "confirmada":
        assert alteracao.status == StatusAlteracaoAssinatura.CONFIRMADA
        assert assinatura.seats_contratados == 6
        assert assinatura.revisao == 3
    else:
        assert alteracao.status == StatusAlteracaoAssinatura.CANCELADA
        assert assinatura.seats_contratados == 5
        assert assinatura.revisao == 2


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


def _inserir_alteracao_como_papel_comum(*, organizacao_id, assinatura_id, alteracao_id, chave):
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
            cursor.execute("SELECT set_config('rls.tenant_id', %s, true)", [str(organizacao_id)])
            cursor.execute(
                """
                INSERT INTO alteracao_assinatura (
                    id, created_at, last_modified_at, is_active, is_deleted,
                    organizacao_id, assinatura_id, tipo, momento_aplicacao, status,
                    revisao_esperada, chave_idempotencia, pedido, snapshot_anterior, snapshot_pretendido
                ) VALUES (
                    %s, NOW(), NOW(), TRUE, FALSE,
                    %s, %s, 20, 10, 10,
                    1, %s, '{}'::jsonb, '{}'::jsonb, '{}'::jsonb
                )
                """,
                [alteracao_id, organizacao_id, assinatura_id, chave],
            )
    finally:
        conexao.close()


def _marcar_aguardando_como_papel_comum(*, organizacao_id, alteracao_id):
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
            cursor.execute("SELECT set_config('rls.tenant_id', %s, true)", [str(organizacao_id)])
            cursor.execute("UPDATE alteracao_assinatura SET status = 20 WHERE id = %s RETURNING status", [alteracao_id])
            return cursor.fetchone()[0]
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


@pytest.mark.django_db(transaction=True)
def test_papel_comum_insere_e_atualiza_alteracao_apenas_com_assinatura_do_mesmo_tenant(papel_rls_assinaturas):
    org_a, assinatura_a = _organizacao_com_contrato(slug="escrita-rls-a")
    org_b, assinatura_b = _organizacao_com_contrato(slug="escrita-rls-b")
    alteracao_id = 9_000_000 + assinatura_a.pk

    _inserir_alteracao_como_papel_comum(
        organizacao_id=org_a.pk,
        assinatura_id=assinatura_a.pk,
        alteracao_id=alteracao_id,
        chave="escrita-tenant-a",
    )
    assert _marcar_aguardando_como_papel_comum(organizacao_id=org_a.pk, alteracao_id=alteracao_id) == 20

    with pytest.raises(psycopg2.DatabaseError, match="organização da assinatura"):
        _inserir_alteracao_como_papel_comum(
            organizacao_id=org_a.pk,
            assinatura_id=assinatura_b.pk,
            alteracao_id=alteracao_id + 1,
            chave="escrita-tenant-divergente",
        )


@pytest.mark.django_db(transaction=True)
def test_rls_aceita_tenant_bigint_acima_do_limite_de_integer(papel_rls_assinaturas):
    _sincronizar_catalogo()
    versao, preco = CatalogoPlanos.obter_versao_inicial(codigo="profissional", periodicidade=Periodicidade.MENSAL)
    organizacao = Organizacao.objects.create(id=2**31, nome="Tenant bigint", slug="tenant-bigint")
    with organizacao_atual_privilegiada(organizacao.pk):
        Assinaturas.criar_paga(
            organizacao=organizacao,
            versao_plano=versao,
            preco_plano=preco,
            seats_contratados=5,
            chave_idempotencia="contrato-tenant-bigint",
        )

    assert _chaves_visiveis_por_papel_comum(organizacao.pk) == (["contrato-tenant-bigint"], [])
