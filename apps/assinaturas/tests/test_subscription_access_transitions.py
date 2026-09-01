"""Transicoes independentes de carencia e encerramento do trial."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

from django.db import DatabaseError, close_old_connections, connection, connections, transaction
from django.utils import timezone

import pytest

from apps.assinaturas.catalogs import PLANOS_BOOTSTRAP, CatalogoPlanos, sincronizar_planos
from apps.assinaturas.features import CATALOGO_RECURSOS, ValoresRecursos
from apps.assinaturas.models import (
    AlteracaoAssinatura,
    AssinaturaOrganizacao,
    Periodicidade,
    StatusAssinatura,
    StatusFinanceiro,
    TipoAlteracaoAssinatura,
)
from apps.assinaturas.subscriptions import (
    Assinaturas,
    ConflitoRevisaoAssinatura,
    CriacaoAlteracaoAssinatura,
    CriacaoAssinatura,
    FallbackTrialGratuito,
    MotivoFallbackTrial,
    OrigemVersaoPlano,
    PagamentoTrialConfirmado,
    PoliticaTrial,
    TermosAssinatura,
    TrialConvertido,
    TrialConvertidoParaGratuito,
)
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.memberships import OcupacaoSeats
from apps.organizacoes.models import Organizacao, Papel, Vinculo
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db


def _catalogo(codigo: str, periodicidade=Periodicidade.MENSAL):
    sincronizar_planos(PLANOS_BOOTSTRAP, aplicar=True)
    return CatalogoPlanos.obter_versao_inicial(codigo=codigo, periodicidade=periodicidade)


def _termos(versao, preco, *, seats: int) -> TermosAssinatura:
    return TermosAssinatura(
        periodicidade=Periodicidade(preco.periodicidade),
        moeda=preco.moeda,
        valor_base_centavos=preco.valor_base_centavos,
        valor_seat_centavos=preco.valor_seat_centavos,
        seats_inclusos=versao.seats_inclusos,
        seats_contratados=seats,
        expansao_automatica_seats=versao.expansao_automatica_seats,
        recursos=ValoresRecursos(CATALOGO_RECURSOS, versao.recursos),
        carencia_pagamento_dias=versao.carencia_pagamento_dias,
        carencia_excesso_seats_dias=versao.carencia_excesso_seats_dias,
    )


def _assinatura_ativa(*, slug: str, seats: int = 5):
    versao, preco = _catalogo("profissional")
    organizacao = Organizacao.objects.create(nome=slug, slug=slug)
    comando = CriacaoAssinatura(
        organizacao=organizacao,
        origem=OrigemVersaoPlano(versao),
        termos=_termos(versao, preco, seats=seats),
        status=StatusAssinatura.ATIVA,
        status_financeiro=StatusFinanceiro.REGULAR,
        politica_trial=None,
        trial_termina_em=None,
        chave_idempotencia=f"ativa:{slug}",
    )
    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura = Assinaturas.criar(comando)
    return organizacao, assinatura


def _trial(*, slug: str):
    versao, preco = _catalogo("profissional")
    organizacao = Organizacao.objects.create(nome=slug, slug=slug)
    agora = timezone.now() - timedelta(days=15)
    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura = Assinaturas.criar_trial(
            organizacao=organizacao,
            versao_plano=versao,
            preco_plano=preco,
            politica_trial=PoliticaTrial.SEM_FORMA_PAGAMENTO,
            chave_idempotencia=f"trial:{slug}",
            agora=agora,
        )
    return organizacao, assinatura


def test_falha_de_renovacao_abre_carencia_uma_vez_sem_reiniciar_prazo():
    organizacao, assinatura = _assinatura_ativa(slug="falha-renovacao")
    primeira_falha = timezone.now()
    segunda_falha = primeira_falha + timedelta(days=2)

    with organizacao_atual_privilegiada(organizacao.pk):
        primeira = Assinaturas.registrar_falha_renovacao(assinatura, agora=primeira_falha)
        repetida = Assinaturas.registrar_falha_renovacao(primeira, agora=segunda_falha)

    assert repetida.status_financeiro == StatusFinanceiro.INADIMPLENTE
    assert repetida.carencia_pagamento_iniciada_em == primeira_falha
    assert repetida.carencia_pagamento_termina_em == primeira_falha + timedelta(days=7)
    assert repetida.revisao == 2


def test_pagamento_regulariza_apenas_carencia_financeira_e_e_idempotente():
    organizacao, assinatura = _assinatura_ativa(slug="pagamento-regulariza")
    agora = timezone.now()
    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura = Assinaturas.registrar_falha_renovacao(assinatura, agora=agora)
        assinatura = Assinaturas.reconciliar_carencia_seats(
            assinatura,
            OcupacaoSeats(consumidos=6, reservados=0),
            agora=agora,
        )
        regularizada = Assinaturas.registrar_pagamento_confirmado(assinatura, agora=agora + timedelta(hours=1))
        repetida = Assinaturas.registrar_pagamento_confirmado(regularizada, agora=agora + timedelta(hours=2))

    assert repetida.status_financeiro == StatusFinanceiro.REGULAR
    assert repetida.carencia_pagamento_iniciada_em is None
    assert repetida.carencia_pagamento_termina_em is None
    assert repetida.carencia_excesso_seats_iniciada_em == agora
    assert repetida.carencia_excesso_seats_termina_em == agora + timedelta(days=7)
    assert repetida.revisao == 4


def test_reconciliar_excesso_abre_e_limpa_somente_carencia_de_seats():
    organizacao, assinatura = _assinatura_ativa(slug="reconciliar-seats", seats=2)
    agora = timezone.now()
    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura = Assinaturas.registrar_falha_renovacao(assinatura, agora=agora)
        com_excesso = Assinaturas.reconciliar_carencia_seats(
            assinatura,
            OcupacaoSeats(consumidos=3, reservados=4),
            agora=agora + timedelta(hours=1),
        )
        repetida = Assinaturas.reconciliar_carencia_seats(
            com_excesso,
            OcupacaoSeats(consumidos=3, reservados=0),
            agora=agora + timedelta(days=2),
        )
        regularizada = Assinaturas.reconciliar_carencia_seats(
            repetida,
            OcupacaoSeats(consumidos=2, reservados=5),
            agora=agora + timedelta(days=3),
        )

    assert repetida.carencia_excesso_seats_iniciada_em == agora + timedelta(hours=1)
    assert repetida.carencia_excesso_seats_termina_em == agora + timedelta(days=7, hours=1)
    assert regularizada.carencia_excesso_seats_iniciada_em is None
    assert regularizada.carencia_excesso_seats_termina_em is None
    assert regularizada.status_financeiro == StatusFinanceiro.INADIMPLENTE
    assert regularizada.carencia_pagamento_iniciada_em == agora
    assert regularizada.revisao == 4


def test_fallback_de_trial_muda_o_mesmo_contrato_e_abre_somente_carencia_de_seats():
    organizacao, assinatura = _trial(slug="trial-fallback")
    usuarios = [criar_usuario(email=f"trial-fallback-{indice}@example.com") for indice in range(2)]
    for usuario in usuarios:
        Vinculo.objects.create(organizacao=organizacao, usuario=usuario, papel=Papel.MEMBRO)
    agora = assinatura.trial_termina_em + timedelta(seconds=1)

    with organizacao_atual_privilegiada(organizacao.pk):
        resultado = Assinaturas.encerrar_trial(
            assinatura,
            resultado=FallbackTrialGratuito(MotivoFallbackTrial.PRIMEIRA_COBRANCA_FALHOU),
            ocupacao=OcupacaoSeats(consumidos=2, reservados=0),
            agora=agora,
        )
        repetido = Assinaturas.encerrar_trial(
            assinatura,
            resultado=FallbackTrialGratuito(MotivoFallbackTrial.PRIMEIRA_COBRANCA_FALHOU),
            ocupacao=OcupacaoSeats(consumidos=2, reservados=0),
            agora=agora + timedelta(minutes=1),
        )
        contratos = list(AssinaturaOrganizacao.all_objects.filter(organizacao=organizacao))
        alteracoes = list(AlteracaoAssinatura.all_objects.filter(organizacao=organizacao))

    assert isinstance(resultado, TrialConvertidoParaGratuito)
    assert isinstance(repetido, TrialConvertidoParaGratuito)
    assert len(contratos) == 1
    assert contratos[0].pk == assinatura.pk == resultado.assinatura.pk == repetido.assinatura.pk
    assert contratos[0].status == StatusAssinatura.ATIVA
    assert contratos[0].status_financeiro == StatusFinanceiro.ISENTO
    assert contratos[0].versao_plano.plano.codigo == "gratuito"
    assert contratos[0].revisao == 2
    assert contratos[0].carencia_pagamento_iniciada_em is None
    assert contratos[0].carencia_pagamento_termina_em is None
    assert contratos[0].carencia_excesso_seats_iniciada_em == agora
    assert contratos[0].carencia_excesso_seats_termina_em == agora + timedelta(days=7)
    assert len(alteracoes) == 1
    assert alteracoes[0].tipo == TipoAlteracaoAssinatura.FALLBACK_TRIAL
    assert alteracoes[0].revisao_aplicada == 2
    assert repetido.alteracao.pk == alteracoes[0].pk


def test_conversao_paga_exige_seats_reais_e_e_idempotente():
    organizacao, assinatura = _trial(slug="trial-pago")
    agora = assinatura.trial_termina_em + timedelta(seconds=1)
    pagamento_insuficiente = PagamentoTrialConfirmado(
        seats_contratados=2,
        periodo_iniciado_em=agora,
        periodo_termina_em=agora + timedelta(days=30),
    )

    with organizacao_atual_privilegiada(organizacao.pk):
        with pytest.raises(ConflitoRevisaoAssinatura, match="consumo real"):
            Assinaturas.encerrar_trial(
                assinatura,
                resultado=pagamento_insuficiente,
                ocupacao=OcupacaoSeats(consumidos=3, reservados=0),
                agora=agora,
            )
        pagamento = PagamentoTrialConfirmado(
            seats_contratados=3,
            periodo_iniciado_em=agora,
            periodo_termina_em=agora + timedelta(days=30),
        )
        convertido = Assinaturas.encerrar_trial(
            assinatura,
            resultado=pagamento,
            ocupacao=OcupacaoSeats(consumidos=3, reservados=5),
            agora=agora,
        )
        repetido = Assinaturas.encerrar_trial(
            assinatura,
            resultado=pagamento,
            ocupacao=OcupacaoSeats(consumidos=3, reservados=0),
            agora=agora + timedelta(minutes=1),
        )

    assert isinstance(convertido, TrialConvertido)
    assert isinstance(repetido, TrialConvertido)
    assert convertido.assinatura.pk == repetido.assinatura.pk == assinatura.pk
    assert repetido.assinatura.status == StatusAssinatura.ATIVA
    assert repetido.assinatura.status_financeiro == StatusFinanceiro.REGULAR
    assert repetido.assinatura.seats_contratados == 3
    assert repetido.assinatura.periodo_atual_iniciado_em == agora
    assert repetido.assinatura.periodo_atual_termina_em == agora + timedelta(days=30)
    assert repetido.assinatura.revisao == 2


@pytest.mark.django_db(transaction=True)
def test_task_e_evento_concorrentes_encerram_trial_uma_unica_vez():
    organizacao, assinatura = _trial(slug="trial-task-evento-concorrentes")
    agora = assinatura.trial_termina_em + timedelta(seconds=1)
    barreira = Barrier(2)

    def encerrar(resultado):
        close_old_connections()
        try:
            barreira.wait(timeout=5)
            with organizacao_atual_privilegiada(organizacao.pk):
                try:
                    return Assinaturas.encerrar_trial(
                        assinatura,
                        resultado=resultado,
                        ocupacao=OcupacaoSeats(consumidos=1, reservados=0),
                        agora=agora,
                    )
                except ConflitoRevisaoAssinatura as exc:
                    return exc
        finally:
            connections.close_all()

    pagamento = PagamentoTrialConfirmado(
        seats_contratados=1,
        periodo_iniciado_em=agora,
        periodo_termina_em=agora + timedelta(days=30),
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        fallback_futuro = executor.submit(
            encerrar,
            FallbackTrialGratuito(MotivoFallbackTrial.TRIAL_LOCAL_ENCERRADO),
        )
        pagamento_futuro = executor.submit(encerrar, pagamento)
        resultados = (fallback_futuro.result(timeout=10), pagamento_futuro.result(timeout=10))

    assert sum(isinstance(resultado, ConflitoRevisaoAssinatura) for resultado in resultados) == 1
    assert sum(isinstance(resultado, (TrialConvertido, TrialConvertidoParaGratuito)) for resultado in resultados) == 1
    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura.refresh_from_db()
        alteracoes = AlteracaoAssinatura.all_objects.filter(organizacao=organizacao)
        assert alteracoes.count() in (0, 1)
    assert assinatura.status == StatusAssinatura.ATIVA
    assert assinatura.revisao == 2


def test_fallback_de_trial_nao_pode_ser_solicitado_pela_api_generica_de_alteracao():
    organizacao, assinatura = _trial(slug="fallback-reservado")
    versao, preco = _catalogo("gratuito")

    with pytest.raises(ValueError, match="reservado"):
        CriacaoAlteracaoAssinatura(
            assinatura=assinatura,
            tipo=TipoAlteracaoAssinatura.FALLBACK_TRIAL,
            origem_pretendida=OrigemVersaoPlano(versao),
            termos_pretendidos=_termos(versao, preco, seats=1),
            revisao_esperada=assinatura.revisao,
            chave_idempotencia=f"fallback-publico:{organizacao.pk}",
        )


@pytest.mark.parametrize("escrita", ["create", "save", "bulk_create"])
def test_fallback_de_trial_nao_pode_ser_criado_por_escrita_orm_generica(escrita):
    organizacao, assinatura = _assinatura_ativa(slug=f"fallback-orm-{escrita}")
    dados = {
        "organizacao": organizacao,
        "assinatura": assinatura,
        "tipo": TipoAlteracaoAssinatura.FALLBACK_TRIAL,
        "momento_aplicacao": 10,
        "status": 10,
        "revisao_esperada": assinatura.revisao,
        "chave_idempotencia": f"fallback-orm-{escrita}",
        "pedido": {"motivo": MotivoFallbackTrial.SEM_PAGAMENTO_VALIDO.value},
        "snapshot_anterior": {"revisao": assinatura.revisao},
        "snapshot_pretendido": {"revisao": assinatura.revisao + 1},
    }

    def escrever_fallback():
        if escrita == "create":
            AlteracaoAssinatura.objects.create(**dados)
        elif escrita == "save":
            AlteracaoAssinatura(**dados).save()
        else:
            AlteracaoAssinatura.objects.bulk_create([AlteracaoAssinatura(**dados)])

    with organizacao_atual_privilegiada(organizacao.pk), pytest.raises(ValueError, match="encerramento nominal do trial"):
        escrever_fallback()


def test_sql_nao_pode_inserir_fallback_para_contrato_ativo():
    organizacao, assinatura = _assinatura_ativa(slug="fallback-sql-ativo")
    with organizacao_atual_privilegiada(organizacao.pk):
        alteracao = AlteracaoAssinatura.objects.create(
            organizacao=organizacao,
            assinatura=assinatura,
            tipo=TipoAlteracaoAssinatura.AUMENTO_SEATS,
            momento_aplicacao=10,
            status=10,
            revisao_esperada=assinatura.revisao,
            chave_idempotencia="fallback-sql-origem",
            pedido={"seats_contratados": assinatura.seats_contratados + 1},
            snapshot_anterior={"revisao": assinatura.revisao},
            snapshot_pretendido={"revisao": assinatura.revisao + 1},
        )
        campos = [field for field in AlteracaoAssinatura._meta.concrete_fields if not field.primary_key]
        colunas = [connection.ops.quote_name(field.column) for field in campos]
        expressoes = []
        parametros = []
        for field, coluna in zip(campos, colunas, strict=True):
            if field.name == "tipo":
                expressoes.append("%s")
                parametros.append(TipoAlteracaoAssinatura.FALLBACK_TRIAL)
            elif field.name == "chave_idempotencia":
                expressoes.append("%s")
                parametros.append("fallback-sql-invalido")
            elif field.name == "pedido":
                expressoes.append("%s")
                parametros.append('{"motivo": "missing_valid_payment"}')
            else:
                expressoes.append(coluna)
        parametros.append(alteracao.pk)

        with pytest.raises(DatabaseError, match="(?i)fallback.*trial"), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO alteracao_assinatura ({', '.join(colunas)}) SELECT {', '.join(expressoes)} FROM alteracao_assinatura WHERE id = %s",
                parametros,
            )


def test_sql_recusa_carencia_financeira_sem_incremento_unitario_da_revisao():
    organizacao, assinatura = _assinatura_ativa(slug="bypass-carencia")
    agora = timezone.now()

    with organizacao_atual_privilegiada(organizacao.pk):
        with pytest.raises(DatabaseError, match="(?i)incremento unitario da revisao"), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE assinatura_organizacao
                       SET status_financeiro = %s,
                           carencia_pagamento_iniciada_em = %s,
                           carencia_pagamento_termina_em = %s
                     WHERE id = %s
                    """,
                    [StatusFinanceiro.INADIMPLENTE, agora, agora + timedelta(days=7), assinatura.pk],
                )


def test_sql_recusa_prazo_de_carencia_financeira_divergente_do_snapshot():
    organizacao, assinatura = _assinatura_ativa(slug="bypass-prazo-carencia")
    agora = timezone.now()

    with organizacao_atual_privilegiada(organizacao.pk):
        with pytest.raises(DatabaseError, match="(?i)prazo da carencia financeira"), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE assinatura_organizacao
                       SET status_financeiro = %s,
                           carencia_pagamento_iniciada_em = %s,
                           carencia_pagamento_termina_em = %s,
                           revisao = revisao + 1
                     WHERE id = %s
                    """,
                    [StatusFinanceiro.INADIMPLENTE, agora, agora + timedelta(days=6), assinatura.pk],
                )


@pytest.mark.django_db(transaction=True)
def test_pagamento_e_excesso_concorrentes_preservam_as_duas_causas():
    organizacao, assinatura = _assinatura_ativa(slug="pagamento-seats-concorrentes", seats=2)
    agora = timezone.now()
    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura = Assinaturas.registrar_falha_renovacao(assinatura, agora=agora)
    barreira = Barrier(2)

    def pagar():
        close_old_connections()
        try:
            barreira.wait(timeout=5)
            with organizacao_atual_privilegiada(organizacao.pk):
                return Assinaturas.registrar_pagamento_confirmado(assinatura, agora=agora + timedelta(hours=1)).pk
        finally:
            connections.close_all()

    def reconciliar():
        close_old_connections()
        try:
            barreira.wait(timeout=5)
            with organizacao_atual_privilegiada(organizacao.pk):
                return Assinaturas.reconciliar_carencia_seats(
                    assinatura,
                    OcupacaoSeats(consumidos=3, reservados=0),
                    agora=agora + timedelta(hours=1),
                ).pk
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        pagamento_futuro = executor.submit(pagar)
        reconciliacao_futura = executor.submit(reconciliar)
        assert {pagamento_futuro.result(timeout=10), reconciliacao_futura.result(timeout=10)} == {assinatura.pk}

    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura.refresh_from_db()
    assert assinatura.status_financeiro == StatusFinanceiro.REGULAR
    assert assinatura.carencia_pagamento_iniciada_em is None
    assert assinatura.carencia_excesso_seats_iniciada_em == agora + timedelta(hours=1)
    assert assinatura.revisao == 4
