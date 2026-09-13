"""Revisão otimista e histórico de alterações contratuais."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Barrier, Event
from time import monotonic, sleep

from django.db import DatabaseError, close_old_connections, connection, connections, transaction
from django.utils import timezone

import pytest

from apps.assinaturas.catalogs import PLANOS_BOOTSTRAP, CatalogoPlanos, sincronizar_planos
from apps.assinaturas.features import CATALOGO_RECURSOS, ValoresRecursos
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
from apps.assinaturas.subscriptions import (
    Assinaturas,
    ConflitoIdempotenciaAssinatura,
    ConflitoRevisaoAssinatura,
    CriacaoAlteracaoAssinatura,
    OrigemVersaoPlano,
    TermosAssinatura,
)
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Organizacao, Papel, Vinculo
from apps.usuarios.models import Usuario
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db


def _catalogos():
    sincronizar_planos(PLANOS_BOOTSTRAP, aplicar=True)
    gratuito = CatalogoPlanos.obter_versao_inicial(codigo="gratuito", periodicidade=Periodicidade.MENSAL)
    profissional = CatalogoPlanos.obter_versao_inicial(codigo="profissional", periodicidade=Periodicidade.MENSAL)
    return gratuito, profissional


def _termos(versao, preco, *, seats_contratados=None, periodicidade=None):
    return TermosAssinatura(
        periodicidade=periodicidade or Periodicidade(preco.periodicidade),
        moeda=preco.moeda,
        valor_base_centavos=preco.valor_base_centavos,
        valor_seat_centavos=preco.valor_seat_centavos,
        seats_inclusos=versao.seats_inclusos,
        seats_contratados=versao.seats_inclusos if seats_contratados is None else seats_contratados,
        expansao_automatica_seats=versao.expansao_automatica_seats,
        recursos=ValoresRecursos(CATALOGO_RECURSOS, versao.recursos),
        carencia_pagamento_dias=versao.carencia_pagamento_dias,
        carencia_excesso_seats_dias=versao.carencia_excesso_seats_dias,
    )


def _assinatura_profissional(*, seats=6, periodo=False):
    (_, _), (versao, preco) = _catalogos()
    organizacao = Organizacao.objects.create(nome="Alterações", slug=f"alteracoes-{Organizacao.objects.count()}")
    termos = _termos(versao, preco, seats_contratados=seats)
    agora = timezone.now()
    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura = AssinaturaOrganizacao.objects.create(
            organizacao=organizacao,
            versao_plano=versao,
            proposta_comercial=None,
            status=StatusAssinatura.ATIVA,
            status_financeiro=StatusFinanceiro.REGULAR,
            periodicidade=termos.periodicidade,
            moeda=termos.moeda,
            valor_base_centavos=termos.valor_base_centavos,
            valor_seat_centavos=termos.valor_seat_centavos,
            seats_inclusos=termos.seats_inclusos,
            seats_contratados=termos.seats_contratados,
            expansao_automatica_seats=termos.expansao_automatica_seats,
            recursos=termos.recursos.materializar(),
            periodo_atual_iniciado_em=agora - timedelta(days=10) if periodo else None,
            periodo_atual_termina_em=agora + timedelta(days=20) if periodo else None,
            carencia_pagamento_dias=termos.carencia_pagamento_dias,
            carencia_excesso_seats_dias=termos.carencia_excesso_seats_dias,
            chave_idempotencia=f"inicial-{organizacao.pk}",
        )
    return organizacao, assinatura, versao, preco


def _comando(assinatura, versao, preco, *, seats, chave, tipo=TipoAlteracaoAssinatura.AUMENTO_SEATS, aplicar_em=None):
    return CriacaoAlteracaoAssinatura(
        assinatura=assinatura,
        tipo=tipo,
        origem_pretendida=OrigemVersaoPlano(versao),
        termos_pretendidos=_termos(versao, preco, seats_contratados=seats),
        revisao_esperada=assinatura.revisao,
        chave_idempotencia=chave,
        aplicar_em=aplicar_em,
    )


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


def test_solicitar_alteracao_persiste_pedido_e_snapshots_completos_e_imutaveis():
    organizacao, assinatura, versao, preco = _assinatura_profissional()
    comando = _comando(assinatura, versao, preco, seats=8, chave="snapshot")

    with organizacao_atual_privilegiada(organizacao.pk):
        alteracao = Assinaturas.solicitar_alteracao(comando)

    assert alteracao.status == StatusAlteracaoAssinatura.SOLICITADA
    assert alteracao.momento_aplicacao == MomentoAplicacaoAlteracaoAssinatura.IMEDIATA
    assert alteracao.snapshot_anterior["revisao"] == 1
    assert alteracao.snapshot_anterior["seats_contratados"] == 6
    assert alteracao.snapshot_pretendido["revisao"] == 2
    assert alteracao.snapshot_pretendido["seats_contratados"] == 8
    assert set(alteracao.snapshot_anterior) == set(alteracao.snapshot_pretendido)
    assert {
        "versao_plano_id",
        "status",
        "status_financeiro",
        "periodicidade",
        "recursos",
        "trial_iniciado_em",
        "periodo_atual_termina_em",
        "carencia_pagamento_termina_em",
        "carencia_excesso_seats_termina_em",
        "cancelamento_agendado_para",
        "encerrada_em",
        "motivo_encerramento",
    } <= set(alteracao.snapshot_anterior)
    alteracao.pedido["seats_contratados"] = 99
    with pytest.raises(ValueError, match="imutáveis"):
        alteracao.save()


def test_solicitacao_idempotente_retorna_existente_e_recusa_payload_divergente():
    organizacao, assinatura, versao, preco = _assinatura_profissional()
    comando = _comando(assinatura, versao, preco, seats=8, chave="alteracao-idem")

    with organizacao_atual_privilegiada(organizacao.pk):
        primeira = Assinaturas.solicitar_alteracao(comando)
        repetida = Assinaturas.solicitar_alteracao(comando)
        with pytest.raises(ConflitoIdempotenciaAssinatura):
            Assinaturas.solicitar_alteracao(replace(comando, termos_pretendidos=replace(comando.termos_pretendidos, seats_contratados=9)))

    assert repetida.pk == primeira.pk


def test_idempotencia_de_alteracao_inclui_assinatura_solicitante_e_momento_resolvido():
    organizacao, assinatura, versao, preco = _assinatura_profissional(periodo=True)
    solicitante_a = criar_usuario(email="autor-a@example.com", email_verificado_em=timezone.now())
    solicitante_b = criar_usuario(email="autor-b@example.com", email_verificado_em=timezone.now())
    Vinculo.objects.create(organizacao=organizacao, usuario=solicitante_a, papel=Papel.PROPRIETARIO)
    Vinculo.objects.create(organizacao=organizacao, usuario=solicitante_b, papel=Papel.PROPRIETARIO)
    aplicar_em = assinatura.periodo_atual_termina_em
    comando = replace(
        _comando(
            assinatura,
            versao,
            preco,
            seats=5,
            chave="fingerprint-completo",
            tipo=TipoAlteracaoAssinatura.REDUCAO_SEATS,
        ),
        solicitada_por=solicitante_a,
        seats_consumidos=5,
    )

    with organizacao_atual_privilegiada(organizacao.pk):
        primeira = Assinaturas.solicitar_alteracao(comando)
        with pytest.raises(ConflitoIdempotenciaAssinatura):
            Assinaturas.solicitar_alteracao(replace(comando, solicitada_por=solicitante_b))

    assert primeira.pedido["organizacao_id"] == organizacao.pk
    assert primeira.pedido["assinatura_id"] == assinatura.pk
    assert primeira.pedido["solicitada_por_id"] == solicitante_a.pk
    assert primeira.pedido["momento_aplicacao"] == int(MomentoAplicacaoAlteracaoAssinatura.PROXIMO_CICLO)
    assert primeira.pedido["aplicar_em"] == aplicar_em.isoformat()


@pytest.mark.django_db(transaction=True)
def test_solicitacao_de_alteracao_bloqueia_solicitante_antes_da_organizacao():
    organizacao, assinatura, versao, preco = _assinatura_profissional()
    solicitante = criar_usuario(email="ordem-alteracao@example.com", email_verificado_em=timezone.now())
    Vinculo.objects.create(organizacao=organizacao, usuario=solicitante, papel=Papel.PROPRIETARIO)
    comando = replace(
        _comando(assinatura, versao, preco, seats=8, chave="ordem-alteracao"),
        solicitada_por=solicitante,
    )
    usuario_bloqueado = Event()
    liberar_usuario = Event()
    alteracao_iniciada = Event()
    pid_alteracao = []

    def manter_usuario_bloqueado():
        close_old_connections()
        try:
            with transaction.atomic():
                Usuario.all_objects.select_for_update().get(pk=solicitante.pk)
                usuario_bloqueado.set()
                if not liberar_usuario.wait(timeout=5):
                    raise AssertionError("Lock do solicitante não foi liberado pelo teste.")
        finally:
            connections.close_all()

    def solicitar():
        close_old_connections()
        try:
            pid_alteracao.append(_pid_backend_atual())
            alteracao_iniciada.set()
            with organizacao_atual_privilegiada(organizacao.pk):
                return Assinaturas.solicitar_alteracao(comando).pk
        finally:
            connections.close_all()

    erro_lock_organizacao = None
    with ThreadPoolExecutor(max_workers=2) as executor:
        usuario_futuro = executor.submit(manter_usuario_bloqueado)
        assert usuario_bloqueado.wait(timeout=5)
        alteracao_futura = executor.submit(solicitar)
        assert alteracao_iniciada.wait(timeout=5)
        _esperar_lock_postgresql(pid_alteracao[0])
        try:
            with transaction.atomic():
                Organizacao.all_objects.select_for_update(nowait=True).get(pk=organizacao.pk)
        except DatabaseError as exc:
            erro_lock_organizacao = getattr(exc.__cause__, "pgcode", None)
        finally:
            liberar_usuario.set()
        usuario_futuro.result(timeout=10)
        alteracao_id = alteracao_futura.result(timeout=10)

    assert erro_lock_organizacao is None
    with organizacao_atual_privilegiada(organizacao.pk):
        assert AlteracaoAssinatura.objects.get(pk=alteracao_id).solicitada_por_id == solicitante.pk


def test_idempotencia_de_alteracao_nunca_retorna_pedido_de_ciclo_anterior():
    organizacao, assinatura_antiga, versao, preco = _assinatura_profissional()
    comando_antigo = _comando(assinatura_antiga, versao, preco, seats=8, chave="mesma-chave-outro-ciclo")

    with organizacao_atual_privilegiada(organizacao.pk):
        antiga = Assinaturas.solicitar_alteracao(comando_antigo)
        Assinaturas.encerrar(organizacao, encerrada_em=timezone.now())
        assinatura_nova = Assinaturas.criar_trial(
            organizacao=organizacao,
            versao_plano=versao,
            preco_plano=preco,
            chave_idempotencia="novo-ciclo",
        )
        comando_novo = _comando(assinatura_nova, versao, preco, seats=8, chave="mesma-chave-outro-ciclo")
        with pytest.raises(ConflitoIdempotenciaAssinatura):
            Assinaturas.solicitar_alteracao(comando_novo)

    assert antiga.assinatura_id == assinatura_antiga.pk


@pytest.mark.parametrize(
    "termos_mascarados",
    [
        lambda termos: replace(termos, valor_base_centavos=termos.valor_base_centavos + 1),
        lambda termos: replace(termos, carencia_pagamento_dias=termos.carencia_pagamento_dias + 1),
        lambda termos: replace(termos, recursos=ValoresRecursos(CATALOGO_RECURSOS, {"quantidade_projetos": 99})),
    ],
)
def test_aumento_de_seats_recusa_qualquer_delta_alem_da_quantidade_absoluta(termos_mascarados):
    organizacao, assinatura, versao, preco = _assinatura_profissional()
    comando = _comando(assinatura, versao, preco, seats=8, chave="aumento-mascarado")

    with organizacao_atual_privilegiada(organizacao.pk), pytest.raises(ValueError, match="somente seats_contratados"):
        Assinaturas.solicitar_alteracao(replace(comando, termos_pretendidos=termos_mascarados(comando.termos_pretendidos)))


def test_aumento_de_seats_usa_snapshot_da_versao_historica_desativada_e_preserva_idempotencia():
    organizacao, assinatura, versao, preco = _assinatura_profissional()
    versao.is_active = False
    versao.save(update_fields=["is_active"])
    comando = _comando(assinatura, versao, preco, seats=8, chave="aumento-versao-historica")

    with organizacao_atual_privilegiada(organizacao.pk):
        primeira = Assinaturas.solicitar_alteracao(comando)
        repetida = Assinaturas.solicitar_alteracao(comando)

    assert repetida.pk == primeira.pk
    assert primeira.snapshot_pretendido["versao_plano_id"] == versao.pk
    assert primeira.snapshot_pretendido["seats_contratados"] == 8


def test_reducao_de_seats_usa_snapshot_da_versao_historica_desativada():
    organizacao, assinatura, versao, preco = _assinatura_profissional(seats=8, periodo=True)
    versao.is_active = False
    versao.save(update_fields=["is_active"])
    comando = replace(
        _comando(
            assinatura,
            versao,
            preco,
            seats=6,
            chave="reducao-versao-historica",
            tipo=TipoAlteracaoAssinatura.REDUCAO_SEATS,
        ),
        seats_consumidos=6,
    )

    with organizacao_atual_privilegiada(organizacao.pk):
        alteracao = Assinaturas.solicitar_alteracao(comando)

    assert alteracao.snapshot_pretendido["versao_plano_id"] == versao.pk
    assert alteracao.snapshot_pretendido["seats_contratados"] == 6


def test_mudancas_reais_de_plano_e_periodicidade_ainda_exigem_catalogo_ativo():
    organizacao, assinatura, versao_profissional, _ = _assinatura_profissional(periodo=True)
    versao_gratuita, preco_gratuito = CatalogoPlanos.obter_versao_inicial(codigo="gratuito", periodicidade=Periodicidade.MENSAL)
    _, preco_anual = CatalogoPlanos.obter_versao_inicial(codigo="profissional", periodicidade=Periodicidade.ANUAL)
    versao_profissional.is_active = False
    versao_profissional.save(update_fields=["is_active"])
    versao_gratuita.is_active = False
    versao_gratuita.save(update_fields=["is_active"])
    mudar_periodicidade = CriacaoAlteracaoAssinatura(
        assinatura=assinatura,
        tipo=TipoAlteracaoAssinatura.MUDANCA_PERIODICIDADE,
        origem_pretendida=OrigemVersaoPlano(versao_profissional),
        termos_pretendidos=_termos(
            versao_profissional,
            preco_anual,
            seats_contratados=assinatura.seats_contratados,
            periodicidade=Periodicidade.ANUAL,
        ),
        revisao_esperada=assinatura.revisao,
        chave_idempotencia="periodicidade-catalogo-inativo",
    )
    mudar_plano = CriacaoAlteracaoAssinatura(
        assinatura=assinatura,
        tipo=TipoAlteracaoAssinatura.DOWNGRADE_PLANO,
        origem_pretendida=OrigemVersaoPlano(versao_gratuita),
        termos_pretendidos=_termos(versao_gratuita, preco_gratuito, seats_contratados=assinatura.seats_contratados),
        revisao_esperada=assinatura.revisao,
        chave_idempotencia="plano-catalogo-inativo",
    )

    with organizacao_atual_privilegiada(organizacao.pk):
        for comando in (mudar_periodicidade, mudar_plano):
            with pytest.raises(ValueError, match="Versão pretendida precisa estar ativa"):
                Assinaturas.solicitar_alteracao(comando)


def test_mudanca_de_periodicidade_usa_preco_publicado_sem_mascarar_seats_ou_recursos():
    organizacao, assinatura, versao, _ = _assinatura_profissional(periodo=True)
    _, preco_anual = CatalogoPlanos.obter_versao_inicial(codigo="profissional", periodicidade=Periodicidade.ANUAL)
    comando = CriacaoAlteracaoAssinatura(
        assinatura=assinatura,
        tipo=TipoAlteracaoAssinatura.MUDANCA_PERIODICIDADE,
        origem_pretendida=OrigemVersaoPlano(versao),
        termos_pretendidos=_termos(
            versao,
            preco_anual,
            seats_contratados=assinatura.seats_contratados,
            periodicidade=Periodicidade.ANUAL,
        ),
        revisao_esperada=assinatura.revisao,
        chave_idempotencia="periodicidade-canonica",
    )

    with organizacao_atual_privilegiada(organizacao.pk):
        alteracao = Assinaturas.solicitar_alteracao(comando)
        with pytest.raises(ValueError, match="termos publicados"):
            Assinaturas.solicitar_alteracao(
                replace(
                    comando,
                    chave_idempotencia="periodicidade-mascarada",
                    termos_pretendidos=replace(comando.termos_pretendidos, seats_contratados=assinatura.seats_contratados + 1),
                )
            )

    assert alteracao.aplicar_em == assinatura.periodo_atual_termina_em


def test_mudanca_de_plano_nao_pode_mascarar_periodicidade_ou_seats():
    organizacao, assinatura, _, _ = _assinatura_profissional(periodo=True)
    versao_gratuita, preco_gratuito = CatalogoPlanos.obter_versao_inicial(codigo="gratuito", periodicidade=Periodicidade.MENSAL)
    comando = CriacaoAlteracaoAssinatura(
        assinatura=assinatura,
        tipo=TipoAlteracaoAssinatura.DOWNGRADE_PLANO,
        origem_pretendida=OrigemVersaoPlano(versao_gratuita),
        termos_pretendidos=_termos(versao_gratuita, preco_gratuito, seats_contratados=assinatura.seats_contratados),
        revisao_esperada=assinatura.revisao,
        chave_idempotencia="plano-canonico",
    )

    with organizacao_atual_privilegiada(organizacao.pk):
        alteracao = Assinaturas.solicitar_alteracao(comando)
        with pytest.raises(ValueError, match="termos publicados"):
            Assinaturas.solicitar_alteracao(
                replace(
                    comando,
                    chave_idempotencia="plano-mascarado",
                    termos_pretendidos=replace(comando.termos_pretendidos, periodicidade=Periodicidade.ANUAL),
                )
            )

    assert alteracao.momento_aplicacao == MomentoAplicacaoAlteracaoAssinatura.PROXIMO_CICLO


def test_alteracao_de_proximo_ciclo_recusa_data_diferente_do_fim_do_periodo():
    organizacao, assinatura, versao, preco = _assinatura_profissional(seats=8, periodo=True)
    comando = replace(
        _comando(
            assinatura,
            versao,
            preco,
            seats=6,
            chave="momento-fora-do-ciclo",
            tipo=TipoAlteracaoAssinatura.REDUCAO_SEATS,
            aplicar_em=assinatura.periodo_atual_termina_em + timedelta(days=1),
        ),
        seats_consumidos=6,
    )

    with organizacao_atual_privilegiada(organizacao.pk), pytest.raises(ValueError, match="fim do período"):
        Assinaturas.solicitar_alteracao(comando)


def test_confirmar_imediata_aplica_snapshot_e_incrementa_revisao():
    organizacao, assinatura, versao, preco = _assinatura_profissional()
    agora = timezone.now()

    with organizacao_atual_privilegiada(organizacao.pk):
        alteracao = Assinaturas.solicitar_alteracao(_comando(assinatura, versao, preco, seats=8, chave="imediata"))
        Assinaturas.marcar_aguardando_gateway(alteracao)
        confirmada = Assinaturas.confirmar_alteracao(alteracao, evento_gateway="evt_1", agora=agora)

    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura.refresh_from_db()
    assert assinatura.seats_contratados == 8
    assert assinatura.revisao == 2
    assert confirmada.status == StatusAlteracaoAssinatura.CONFIRMADA
    assert confirmada.processada_em == agora
    assert confirmada.aplicada_em == agora
    assert confirmada.revisao_aplicada == 2
    assert confirmada.evento_gateway == "evt_1"


def test_revisao_otimista_recusa_confirmacao_obsoleta():
    organizacao, assinatura, versao, preco = _assinatura_profissional()

    with organizacao_atual_privilegiada(organizacao.pk):
        primeira = Assinaturas.solicitar_alteracao(_comando(assinatura, versao, preco, seats=7, chave="primeira"))
        segunda = Assinaturas.solicitar_alteracao(_comando(assinatura, versao, preco, seats=8, chave="segunda"))
        Assinaturas.confirmar_alteracao(primeira)
        with pytest.raises(ConflitoRevisaoAssinatura):
            Assinaturas.confirmar_alteracao(segunda)

    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura.refresh_from_db()
    assert assinatura.revisao == 2
    assert assinatura.seats_contratados == 7


def test_evento_atrasado_completa_historico_sem_regredir_assinatura():
    organizacao, assinatura, versao, preco = _assinatura_profissional()
    agora = timezone.now()

    with organizacao_atual_privilegiada(organizacao.pk):
        antiga = Assinaturas.solicitar_alteracao(_comando(assinatura, versao, preco, seats=7, chave="antiga"))
        nova = Assinaturas.solicitar_alteracao(_comando(assinatura, versao, preco, seats=9, chave="nova"))
        Assinaturas.confirmar_alteracao(nova)
        historico = Assinaturas.confirmar_alteracao(
            antiga,
            evento_gateway="evt_atrasado",
            agora=agora,
            permitir_evento_atrasado=True,
        )

    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura.refresh_from_db()
    assert assinatura.revisao == 2
    assert assinatura.seats_contratados == 9
    assert historico.status == StatusAlteracaoAssinatura.CONFIRMADA
    assert historico.aplicada_em is None
    assert historico.revisao_aplicada is None
    assert historico.revisao_observada == 2
    assert historico.ignorada_em == agora


def test_reducao_agendada_so_aplica_no_proximo_ciclo_e_valida_consumo():
    organizacao, assinatura, versao, preco = _assinatura_profissional(seats=8, periodo=True)
    aplicar_em = assinatura.periodo_atual_termina_em
    comando = _comando(
        assinatura,
        versao,
        preco,
        seats=6,
        chave="reducao",
        tipo=TipoAlteracaoAssinatura.REDUCAO_SEATS,
        aplicar_em=aplicar_em,
    )

    with organizacao_atual_privilegiada(organizacao.pk):
        with pytest.raises(ValueError, match="consumo"):
            Assinaturas.solicitar_alteracao(replace(comando, seats_consumidos=7))
        alteracao = Assinaturas.solicitar_alteracao(replace(comando, seats_consumidos=6))
        confirmada = Assinaturas.confirmar_alteracao(alteracao, agora=aplicar_em - timedelta(seconds=1))

    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura.refresh_from_db()
    assert confirmada.status == StatusAlteracaoAssinatura.CONFIRMADA
    assert confirmada.aplicada_em is None
    assert assinatura.seats_contratados == 8

    with organizacao_atual_privilegiada(organizacao.pk):
        aplicada = Assinaturas.aplicar_alteracao_agendada(confirmada, agora=aplicar_em)

    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura.refresh_from_db()
    assert assinatura.seats_contratados == 6
    assert assinatura.revisao == 2
    assert aplicada.aplicada_em == aplicar_em


@pytest.mark.django_db(transaction=True)
def test_confirmacoes_concorrentes_da_mesma_revisao_aplicam_somente_uma():
    organizacao, assinatura, versao, preco = _assinatura_profissional()
    with organizacao_atual_privilegiada(organizacao.pk):
        ids = [
            Assinaturas.solicitar_alteracao(_comando(assinatura, versao, preco, seats=seats, chave=chave)).pk
            for seats, chave in ((7, "corrente-1"), (8, "corrente-2"))
        ]
    barreira = Barrier(2)

    def confirmar(alteracao_id):
        close_old_connections()
        try:
            barreira.wait(timeout=5)
            with organizacao_atual_privilegiada(organizacao.pk):
                alteracao = AlteracaoAssinatura.objects.get(pk=alteracao_id)
                try:
                    Assinaturas.confirmar_alteracao(alteracao)
                except ConflitoRevisaoAssinatura:
                    return "conflito"
                return "aplicada"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        resultados = list(executor.map(confirmar, ids))

    assert sorted(resultados) == ["aplicada", "conflito"]
    with organizacao_atual_privilegiada(organizacao.pk):
        assert AlteracaoAssinatura.objects.filter(status=StatusAlteracaoAssinatura.CONFIRMADA).count() == 1


@pytest.mark.django_db(transaction=True)
def test_falha_durante_aquisicao_dos_locks_nao_deixa_transacao_aberta():
    organizacao, assinatura, versao, preco = _assinatura_profissional()
    with organizacao_atual_privilegiada(organizacao.pk):
        alteracao = Assinaturas.solicitar_alteracao(_comando(assinatura, versao, preco, seats=7, chave="lock-falhou"))
    alteracao.pk += 1_000_000

    assert connection.in_atomic_block is False
    with organizacao_atual_privilegiada(organizacao.pk), pytest.raises(AlteracaoAssinatura.DoesNotExist):
        Assinaturas.confirmar_alteracao(alteracao)

    assert connection.in_atomic_block is False
