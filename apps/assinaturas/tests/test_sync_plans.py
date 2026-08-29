from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from io import StringIO
from threading import Barrier, Event
from time import monotonic, sleep

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import close_old_connections, connection, connections, transaction

import pytest

from apps.assinaturas import catalogs
from apps.assinaturas.catalogs import PLANOS_BOOTSTRAP, DefinicaoPrecoPlano, sincronizar_planos
from apps.assinaturas.models import Periodicidade, Plano, PrecoPlano, VersaoPlano
from apps.logs.models import LogAlteracao

pytestmark = pytest.mark.django_db


def executar_sync(*args):
    stdout = StringIO()
    call_command("sync_plans", *args, stdout=stdout)
    return stdout.getvalue()


def criar_draft(plano, definicao):
    versao = VersaoPlano.objects.create(
        plano=plano,
        numero=definicao.numero,
        atual=False,
        seats_inclusos=definicao.seats_inclusos,
        limite_seats_trial=definicao.limite_seats_trial,
        duracao_trial_dias=definicao.duracao_trial_dias,
        carencia_pagamento_dias=definicao.carencia_pagamento_dias,
        carencia_excesso_seats_dias=definicao.carencia_excesso_seats_dias,
        expansao_automatica_seats=definicao.expansao_automatica_seats,
        recursos=definicao.recursos.materializar(),
        publicada_em=None,
        is_active=definicao.is_active,
    )
    for definicao_preco in definicao.precos:
        PrecoPlano.objects.create(
            versao_plano=versao,
            periodicidade=definicao_preco.periodicidade,
            moeda=definicao_preco.moeda,
            valor_base_centavos=definicao_preco.valor_base_centavos,
            valor_seat_centavos=definicao_preco.valor_seat_centavos,
            is_active=definicao_preco.is_active,
        )
    return versao


def test_sync_plans_e_dry_run_por_padrao():
    saida = executar_sync()

    assert Plano.objects.count() == 0
    assert "DRY-RUN" in saida
    assert "criaria plano 'gratuito'" in saida
    assert "criaria plano 'profissional'" in saida


def test_sync_plans_apply_cria_catalogo_declarado():
    saida = executar_sync("--apply")

    assert list(Plano.objects.order_by("codigo").values_list("codigo", flat=True)) == ["gratuito", "profissional"]
    assert VersaoPlano.objects.count() == 2
    assert PrecoPlano.objects.count() == 4
    assert VersaoPlano.objects.filter(atual=True, publicada_em__isnull=False).count() == 2
    assert "APLICADO" in saida


def test_sync_plans_apply_repetido_e_idempotente():
    executar_sync("--apply")
    ids_antes = (
        tuple(Plano.objects.order_by("pk").values_list("pk", flat=True)),
        tuple(VersaoPlano.objects.order_by("pk").values_list("pk", flat=True)),
        tuple(PrecoPlano.objects.order_by("pk").values_list("pk", flat=True)),
    )

    saida = executar_sync("--apply")

    ids_depois = (
        tuple(Plano.objects.order_by("pk").values_list("pk", flat=True)),
        tuple(VersaoPlano.objects.order_by("pk").values_list("pk", flat=True)),
        tuple(PrecoPlano.objects.order_by("pk").values_list("pk", flat=True)),
    )
    assert ids_depois == ids_antes
    assert "sem alterações" in saida


def test_sync_plans_recusa_mudar_conteudo_da_mesma_versao(monkeypatch):
    executar_sync("--apply")
    gratuito = PLANOS_BOOTSTRAP[0]
    versao_alterada = replace(gratuito.versoes[0], seats_inclusos=999)
    catalogo_alterado = (replace(gratuito, versoes=(versao_alterada,)), PLANOS_BOOTSTRAP[1])
    monkeypatch.setattr("apps.assinaturas.management.commands.sync_plans.PLANOS_BOOTSTRAP", catalogo_alterado)

    with pytest.raises(CommandError, match="declare o próximo número"):
        executar_sync("--apply")

    assert VersaoPlano.objects.get(plano__codigo="gratuito", numero=1).seats_inclusos != 999


def test_sync_plans_muda_versao_atual_sem_mutar_a_publicada(monkeypatch):
    executar_sync("--apply")
    gratuito = PLANOS_BOOTSTRAP[0]
    versao_1 = replace(gratuito.versoes[0], atual=False)
    versao_2 = replace(gratuito.versoes[0], numero=2, atual=True, seats_inclusos=2)
    catalogo_v2 = (replace(gratuito, versoes=(versao_1, versao_2)), PLANOS_BOOTSTRAP[1])
    monkeypatch.setattr("apps.assinaturas.management.commands.sync_plans.PLANOS_BOOTSTRAP", catalogo_v2)

    executar_sync("--apply")

    versoes = VersaoPlano.objects.filter(plano__codigo="gratuito").order_by("numero")
    assert list(versoes.values_list("numero", "atual", "seats_inclusos")) == [
        (1, False, 1),
        (2, True, 2),
    ]
    versao_1_banco, versao_2_banco = versoes
    mudancas_v1 = [
        registro.changes["atual"]
        for registro in LogAlteracao.objects.get_for_object(versao_1_banco).filter(action=LogAlteracao.Action.UPDATE)
        if "atual" in registro.changes
    ]
    mudancas_v2 = [
        registro.changes["atual"]
        for registro in LogAlteracao.objects.get_for_object(versao_2_banco).filter(action=LogAlteracao.Action.UPDATE)
        if "atual" in registro.changes
    ]
    assert ["True", "False"] in mudancas_v1
    assert ["False", "True"] in mudancas_v2


def test_sync_plans_preserva_atual_se_troca_falha(monkeypatch):
    executar_sync("--apply")
    gratuito = PLANOS_BOOTSTRAP[0]
    versao_1 = replace(gratuito.versoes[0], atual=False)
    versao_2 = replace(gratuito.versoes[0], numero=2, atual=True, seats_inclusos=2)
    catalogo_v2 = (replace(gratuito, versoes=(versao_1, versao_2)), PLANOS_BOOTSTRAP[1])
    monkeypatch.setattr("apps.assinaturas.management.commands.sync_plans.PLANOS_BOOTSTRAP", catalogo_v2)
    save_original = VersaoPlano.save

    def falhar_ao_marcar_alvo(self, *args, **kwargs):
        if self.numero == 2 and self.atual and "atual" in (kwargs.get("update_fields") or ()):
            raise RuntimeError("falha simulada na troca")
        return save_original(self, *args, **kwargs)

    monkeypatch.setattr(VersaoPlano, "save", falhar_ao_marcar_alvo)

    with pytest.raises(RuntimeError, match="falha simulada"):
        executar_sync("--apply")

    assert list(VersaoPlano.objects.filter(plano__codigo="gratuito").values_list("numero", "atual")) == [(1, True)]


def test_sync_plans_recusa_adicionar_chave_de_preco_a_versao_publicada(monkeypatch):
    executar_sync("--apply")
    gratuito = PLANOS_BOOTSTRAP[0]
    preco_extra = DefinicaoPrecoPlano(
        periodicidade=Periodicidade.MENSAL,
        moeda="USD",
        valor_base_centavos=0,
        valor_seat_centavos=0,
    )
    versao_alterada = replace(gratuito.versoes[0], precos=(*gratuito.versoes[0].precos, preco_extra))
    catalogo_alterado = (replace(gratuito, versoes=(versao_alterada,)), PLANOS_BOOTSTRAP[1])
    monkeypatch.setattr("apps.assinaturas.management.commands.sync_plans.PLANOS_BOOTSTRAP", catalogo_alterado)

    with pytest.raises(CommandError, match="conjunto de preços.*próximo número"):
        executar_sync("--apply")

    assert PrecoPlano.objects.filter(versao_plano__plano__codigo="gratuito", moeda="USD").exists() is False


def test_sync_plans_recusa_remover_chave_de_preco_da_versao_publicada(monkeypatch):
    executar_sync("--apply")
    gratuito = PLANOS_BOOTSTRAP[0]
    versao_alterada = replace(gratuito.versoes[0], precos=(gratuito.versoes[0].precos[0],))
    catalogo_alterado = (replace(gratuito, versoes=(versao_alterada,)), PLANOS_BOOTSTRAP[1])
    monkeypatch.setattr("apps.assinaturas.management.commands.sync_plans.PLANOS_BOOTSTRAP", catalogo_alterado)

    with pytest.raises(CommandError, match="conjunto de preços.*próximo número"):
        executar_sync("--apply")

    assert PrecoPlano.objects.filter(versao_plano__plano__codigo="gratuito").count() == 2


def test_sync_plans_recusa_alterar_termos_de_preco_publicado(monkeypatch):
    executar_sync("--apply")
    gratuito = PLANOS_BOOTSTRAP[0]
    preco_alterado = replace(gratuito.versoes[0].precos[0], valor_base_centavos=1)
    versao_alterada = replace(gratuito.versoes[0], precos=(preco_alterado, gratuito.versoes[0].precos[1]))
    catalogo_alterado = (replace(gratuito, versoes=(versao_alterada,)), PLANOS_BOOTSTRAP[1])
    monkeypatch.setattr("apps.assinaturas.management.commands.sync_plans.PLANOS_BOOTSTRAP", catalogo_alterado)

    with pytest.raises(CommandError, match="Preço.*diverge.*próximo número"):
        executar_sync("--apply")

    assert (
        PrecoPlano.objects.get(
            versao_plano__plano__codigo="gratuito",
            periodicidade=Periodicidade.MENSAL,
            moeda="BRL",
        ).valor_base_centavos
        == 0
    )


def test_sync_plans_nao_torna_draft_atual_e_preserva_versao_anterior(monkeypatch):
    executar_sync("--apply")
    gratuito = PLANOS_BOOTSTRAP[0]
    plano = Plano.objects.get(codigo="gratuito")
    versao_1 = replace(gratuito.versoes[0], atual=False)
    versao_2 = replace(gratuito.versoes[0], numero=2, atual=True, seats_inclusos=2)
    criar_draft(plano, versao_2)
    catalogo_v2 = (replace(gratuito, versoes=(versao_1, versao_2)), PLANOS_BOOTSTRAP[1])
    monkeypatch.setattr("apps.assinaturas.management.commands.sync_plans.PLANOS_BOOTSTRAP", catalogo_v2)

    with pytest.raises(CommandError, match="não.*publicada"):
        executar_sync("--apply")

    assert list(VersaoPlano.objects.filter(plano=plano).order_by("numero").values_list("numero", "atual")) == [(1, True), (2, False)]
    assert VersaoPlano.objects.get(plano=plano, numero=1).publicada_em is not None
    assert VersaoPlano.objects.get(plano=plano, numero=2).publicada_em is None


def test_sync_plans_nao_seleciona_versao_soft_deleted_como_atual(monkeypatch):
    executar_sync("--apply")
    gratuito = PLANOS_BOOTSTRAP[0]
    plano = Plano.objects.get(codigo="gratuito")
    versao_1 = replace(gratuito.versoes[0], atual=False)
    versao_2 = replace(gratuito.versoes[0], numero=2, atual=True, seats_inclusos=2)
    draft_excluido = criar_draft(plano, versao_2)
    draft_excluido.delete()
    catalogo_v2 = (replace(gratuito, versoes=(versao_1, versao_2)), PLANOS_BOOTSTRAP[1])
    monkeypatch.setattr("apps.assinaturas.management.commands.sync_plans.PLANOS_BOOTSTRAP", catalogo_v2)

    with pytest.raises(CommandError, match="excluída"):
        executar_sync("--apply")

    assert VersaoPlano.objects.get(plano=plano, numero=1).atual is True
    assert VersaoPlano.all_objects.get(pk=draft_excluido.pk).atual is False


@pytest.mark.django_db(transaction=True)
def test_primeiro_sync_concorrente_converge_sem_integrity_error():
    barreira = Barrier(2)

    def sincronizar():
        close_old_connections()
        try:
            barreira.wait(timeout=5)
            return sincronizar_planos(PLANOS_BOOTSTRAP, aplicar=True)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futuros = [executor.submit(sincronizar), executor.submit(sincronizar)]
        resultados = [futuro.result(timeout=15) for futuro in futuros]

    assert len(resultados) == 2
    assert Plano.objects.count() == 2
    assert VersaoPlano.objects.count() == 2
    assert PrecoPlano.objects.count() == 4


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("bloquear_antes", [False, True], ids=["update-direto", "select-for-update"])
def test_sync_serializa_com_atualizacao_operacional_de_preco_sem_deadlock(monkeypatch, bloquear_antes):
    sincronizar_planos(PLANOS_BOOTSTRAP, aplicar=True)
    gratuito = PLANOS_BOOTSTRAP[0]
    preco_inativo = replace(gratuito.versoes[0].precos[0], is_active=False)
    versao_alterada = replace(gratuito.versoes[0], precos=(preco_inativo, gratuito.versoes[0].precos[1]))
    definicoes = (replace(gratuito, versoes=(versao_alterada,)), PLANOS_BOOTSTRAP[1])
    preco = PrecoPlano.objects.get(
        versao_plano__plano__codigo="gratuito",
        periodicidade=Periodicidade.MENSAL,
        moeda="BRL",
    )
    versao_bloqueada = Event()
    liberar_sync = Event()
    atualizacao_iniciada = Event()
    pid_atualizacao: list[int] = []
    recusar_mutacao_original = catalogs._recusar_mutacao_versao

    def pausar_sync_com_versao_bloqueada(versao, definicao):
        recusar_mutacao_original(versao, definicao)
        if versao.plano.codigo == "gratuito":
            versao_bloqueada.set()
            assert liberar_sync.wait(timeout=10)

    monkeypatch.setattr(catalogs, "_recusar_mutacao_versao", pausar_sync_com_versao_bloqueada)

    def executar_sync_concorrente():
        close_old_connections()
        try:
            return sincronizar_planos(definicoes, aplicar=True)
        finally:
            connections.close_all()

    def atualizar_preco_concorrente():
        close_old_connections()
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                pid_atualizacao.append(cursor.fetchone()[0])
                atualizacao_iniciada.set()
                if bloquear_antes:
                    PrecoPlano.objects.select_for_update().get(pk=preco.pk)
                return PrecoPlano.objects.filter(pk=preco.pk).update(is_active=False)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futuro_sync = executor.submit(executar_sync_concorrente)
        assert versao_bloqueada.wait(timeout=5)
        futuro_atualizacao = executor.submit(atualizar_preco_concorrente)
        assert atualizacao_iniciada.wait(timeout=5)

        limite = monotonic() + 5
        try:
            while monotonic() < limite:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", [pid_atualizacao[0]])
                    resultado = cursor.fetchone()
                if resultado is not None and resultado[0] == "Lock":
                    break
                sleep(0.01)
            else:
                pytest.fail("a atualização concorrente não entrou em espera de lock")
        finally:
            liberar_sync.set()

        assert futuro_sync.result(timeout=15) is not None
        assert futuro_atualizacao.result(timeout=15) == 1

    preco.refresh_from_db()
    assert preco.is_active is False
