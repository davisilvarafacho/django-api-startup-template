from concurrent.futures import ThreadPoolExecutor
from threading import Event

from django.db import DatabaseError, close_old_connections, connection, connections, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.utils import timezone

import pytest

from apps.assinaturas.models import Periodicidade, Plano, PrecoPlano, VersaoPlano


def atualizar_preco_com_versao_bloqueada(versao_id, preco_id):
    versao_bloqueada = Event()
    liberar_versao = Event()

    def bloquear_versao():
        close_old_connections()
        try:
            with transaction.atomic():
                VersaoPlano.objects.select_for_update(no_key=True).get(pk=versao_id)
                versao_bloqueada.set()
                assert liberar_versao.wait(timeout=5)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as executor:
        futuro = executor.submit(bloquear_versao)
        assert versao_bloqueada.wait(timeout=5)
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '500ms'")
                cursor.execute("UPDATE preco_plano SET is_active = NOT is_active WHERE id = %s", [preco_id])
        except DatabaseError as exc:
            resultado = exc.__cause__.pgcode
        else:
            resultado = "atualizou"
        finally:
            liberar_versao.set()
        futuro.result(timeout=5)
    return resultado


@pytest.mark.django_db(transaction=True)
def test_migration_0003_protege_identidade_e_reverse_restaura_0002():
    executor = MigrationExecutor(connection)
    alvos_finais = executor.loader.graph.leaf_nodes()

    try:
        executor.migrate([("assinaturas", "0002_proteger_catalogo_publicado")])
        plano = Plano.objects.create(codigo="migration-pk", nome="Migration PK", descricao="", visivel=False)
        versao = VersaoPlano.objects.create(plano=plano, numero=1, atual=False, recursos={})
        preco = PrecoPlano.objects.create(
            versao_plano=versao,
            periodicidade=Periodicidade.MENSAL,
            moeda="BRL",
            valor_base_centavos=100,
            valor_seat_centavos=0,
        )
        versao.publicada_em = timezone.now()
        versao.save(update_fields=["publicada_em"])
        novo_id = preco.pk + 1_000_000

        with connection.cursor() as cursor:
            cursor.execute("UPDATE preco_plano SET id = %s WHERE id = %s", [novo_id, preco.pk])
            cursor.execute("UPDATE preco_plano SET id = %s WHERE id = %s", [preco.pk, novo_id])

        executor = MigrationExecutor(connection)
        executor.migrate([("assinaturas", "0003_proteger_identidade_catalogo")])

        with pytest.raises(DatabaseError, match="Preço de versão publicada é imutável"), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("UPDATE preco_plano SET id = %s WHERE id = %s", [novo_id, preco.pk])

        executor = MigrationExecutor(connection)
        executor.migrate([("assinaturas", "0002_proteger_catalogo_publicado")])

        with connection.cursor() as cursor:
            cursor.execute("UPDATE preco_plano SET id = %s WHERE id = %s", [novo_id, preco.pk])
        assert PrecoPlano.all_objects.filter(pk=novo_id).exists()
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(alvos_finais)


@pytest.mark.django_db(transaction=True)
def test_migration_0004_torna_lock_de_preco_compativel_e_reverse_restaura_0003():
    executor = MigrationExecutor(connection)
    alvos_finais = executor.loader.graph.leaf_nodes()

    try:
        executor.migrate([("assinaturas", "0003_proteger_identidade_catalogo")])
        plano = Plano.objects.create(codigo="migration-lock", nome="Migration Lock", descricao="", visivel=False)
        versao = VersaoPlano.objects.create(plano=plano, numero=1, atual=False, recursos={})
        preco = PrecoPlano.objects.create(
            versao_plano=versao,
            periodicidade=Periodicidade.MENSAL,
            moeda="BRL",
            valor_base_centavos=100,
            valor_seat_centavos=0,
        )
        versao.publicada_em = timezone.now()
        versao.save(update_fields=["publicada_em"])

        assert atualizar_preco_com_versao_bloqueada(versao.pk, preco.pk) == "55P03"

        executor = MigrationExecutor(connection)
        executor.migrate([("assinaturas", "0004_ordenar_locks_catalogo")])

        assert atualizar_preco_com_versao_bloqueada(versao.pk, preco.pk) == "atualizou"

        executor = MigrationExecutor(connection)
        executor.migrate([("assinaturas", "0003_proteger_identidade_catalogo")])

        assert atualizar_preco_com_versao_bloqueada(versao.pk, preco.pk) == "55P03"
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(alvos_finais)


@pytest.mark.django_db(transaction=True, databases={"default", "logging"})
def test_migrations_0010_0011_0012_e_0013_executam_forward_e_reverse_no_sqlite():
    conexao = connections["logging"]
    executor = MigrationExecutor(conexao)
    alvos_finais = executor.loader.graph.leaf_nodes()

    def aplicadas():
        return MigrationRecorder(conexao).applied_migrations()

    try:
        executor.migrate([("assinaturas", "0009_proteger_estado_inicial_proposta")])

        executor = MigrationExecutor(conexao)
        executor.migrate([("assinaturas", "0010_adicionar_fallback_trial")])
        assert ("assinaturas", "0010_adicionar_fallback_trial") in aplicadas()

        executor = MigrationExecutor(conexao)
        executor.migrate([("assinaturas", "0011_proteger_transicoes_acesso")])
        assert ("assinaturas", "0011_proteger_transicoes_acesso") in aplicadas()

        executor = MigrationExecutor(conexao)
        executor.migrate([("assinaturas", "0012_endurecer_rollout_e_transicoes")])
        assert ("assinaturas", "0012_endurecer_rollout_e_transicoes") in aplicadas()

        executor = MigrationExecutor(conexao)
        executor.migrate([("assinaturas", "0013_proteger_revisao_e_upserts")])
        assert ("assinaturas", "0013_proteger_revisao_e_upserts") in aplicadas()

        executor = MigrationExecutor(conexao)
        executor.migrate([("assinaturas", "0012_endurecer_rollout_e_transicoes")])
        assert ("assinaturas", "0013_proteger_revisao_e_upserts") not in aplicadas()

        executor = MigrationExecutor(conexao)
        executor.migrate([("assinaturas", "0011_proteger_transicoes_acesso")])
        assert ("assinaturas", "0012_endurecer_rollout_e_transicoes") not in aplicadas()

        executor = MigrationExecutor(conexao)
        executor.migrate([("assinaturas", "0010_adicionar_fallback_trial")])
        assert ("assinaturas", "0011_proteger_transicoes_acesso") not in aplicadas()

        executor = MigrationExecutor(conexao)
        executor.migrate([("assinaturas", "0009_proteger_estado_inicial_proposta")])
        assert ("assinaturas", "0010_adicionar_fallback_trial") not in aplicadas()
    finally:
        executor = MigrationExecutor(conexao)
        executor.migrate(alvos_finais)


@pytest.mark.django_db(transaction=True)
def test_migration_0006_promove_rls_para_bigint_e_reverse_restaura_0005():
    executor = MigrationExecutor(connection)
    alvos_finais = executor.loader.graph.leaf_nodes()

    def expressao_policy():
        with connection.cursor() as cursor:
            cursor.execute("SELECT qual FROM pg_policies WHERE tablename = 'assinatura_organizacao' AND policyname = 'isolamento_organizacao'")
            return cursor.fetchone()[0]

    try:
        executor.migrate([("assinaturas", "0004_ordenar_locks_catalogo")])
        executor = MigrationExecutor(connection)
        executor.migrate([("assinaturas", "0005_assinaturaorganizacao_alteracaoassinatura_and_more")])
        assert "::integer" in expressao_policy()

        executor = MigrationExecutor(connection)
        executor.migrate([("assinaturas", "0006_reforcar_invariantes_assinaturas")])
        assert "::bigint" in expressao_policy()

        executor = MigrationExecutor(connection)
        executor.migrate([("assinaturas", "0005_assinaturaorganizacao_alteracaoassinatura_and_more")])
        assert "::integer" in expressao_policy()
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(alvos_finais)


@pytest.mark.django_db(transaction=True)
def test_migration_0007_fecha_xor_rls_e_reverse_restaura_origem_catalogo():
    executor = MigrationExecutor(connection)
    alvos_finais = executor.loader.graph.leaf_nodes()

    def constraints_assinatura():
        with connection.cursor() as cursor:
            return connection.introspection.get_constraints(cursor, "assinatura_organizacao")

    try:
        executor.migrate([("assinaturas", "0006_reforcar_invariantes_assinaturas")])
        assert "proposta_comercial" not in connection.introspection.table_names()
        assert "assinatura_origem_catalogo_exige_versao" in constraints_assinatura()

        executor = MigrationExecutor(connection)
        executor.migrate([("assinaturas", "0007_propostas_comerciais_enterprise")])
        assert "proposta_comercial" in connection.introspection.table_names()
        assert "assinatura_origem_xor" in constraints_assinatura()
        with connection.cursor() as cursor:
            cursor.execute("SELECT qual FROM pg_policies WHERE tablename = 'proposta_comercial' AND policyname = 'isolamento_organizacao'")
            assert "::bigint" in cursor.fetchone()[0]
            cursor.execute(
                "SELECT 1 FROM pg_constraint WHERE conrelid = 'proposta_comercial'::regclass AND conname = 'proposta_recursos_objeto_json'"
            )
            assert cursor.fetchone() == (1,)

        executor = MigrationExecutor(connection)
        executor.migrate([("assinaturas", "0006_reforcar_invariantes_assinaturas")])
        assert "proposta_comercial" not in connection.introspection.table_names()
        assert "assinatura_origem_catalogo_exige_versao" in constraints_assinatura()
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(alvos_finais)


@pytest.mark.django_db(transaction=True)
def test_migration_0008_protege_transicoes_e_reverse_restaura_guarda_0007():
    from apps.assinaturas.tests.test_proposal_models import _criar_proposta
    from apps.organizacoes.context import organizacao_atual_privilegiada
    from apps.organizacoes.models import Organizacao

    executor = MigrationExecutor(connection)
    alvos_finais = executor.loader.graph.leaf_nodes()

    try:
        executor.migrate([("assinaturas", "0007_propostas_comerciais_enterprise")])
        organizacao = Organizacao.objects.create(nome="Migration 0008", slug="migration-proposta-0008")
        modelo_historico = executor.loader.project_state([("assinaturas", "0007_propostas_comerciais_enterprise")]).apps.get_model(
            "assinaturas", "PropostaComercial"
        )
        proposta = _criar_proposta(organizacao, model=modelo_historico)

        with organizacao_atual_privilegiada(organizacao.pk), connection.cursor() as cursor:
            cursor.execute("UPDATE proposta_comercial SET revisao = revisao + 1 WHERE id = %s", [proposta.pk])

        executor = MigrationExecutor(connection)
        executor.migrate([("assinaturas", "0008_endurecer_transicoes_propostas")])
        with (
            organizacao_atual_privilegiada(organizacao.pk),
            pytest.raises(DatabaseError, match="revisão"),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute("UPDATE proposta_comercial SET revisao = revisao + 1 WHERE id = %s", [proposta.pk])

        executor = MigrationExecutor(connection)
        executor.migrate([("assinaturas", "0007_propostas_comerciais_enterprise")])
        with organizacao_atual_privilegiada(organizacao.pk), connection.cursor() as cursor:
            cursor.execute("UPDATE proposta_comercial SET revisao = revisao + 1 WHERE id = %s", [proposta.pk])
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(alvos_finais)


@pytest.mark.django_db(transaction=True)
def test_migration_0009_protege_insert_e_reverse_preserva_guarda_0008():
    from apps.assinaturas.models import StatusPropostaComercial
    from apps.assinaturas.tests.test_proposal_models import _criar_proposta
    from apps.organizacoes.context import organizacao_atual_privilegiada
    from apps.organizacoes.models import Organizacao
    from tests.support.usuarios import criar_usuario

    executor = MigrationExecutor(connection)
    alvos_finais = executor.loader.graph.leaf_nodes()

    def inserir_aceita(proposta, ator, model):
        campos = [field for field in model._meta.concrete_fields if not field.primary_key]
        colunas = [connection.ops.quote_name(field.column) for field in campos]
        agora = timezone.now()
        substituicoes = {
            "status": StatusPropostaComercial.ACEITA,
            "revisao": 3,
            "enviada_em": agora,
            "aceita_em": agora,
            "aceita_por": ator.pk,
        }
        expressoes = []
        parametros = []
        for field, coluna in zip(campos, colunas, strict=True):
            if field.name in substituicoes:
                expressoes.append("%s")
                parametros.append(substituicoes[field.name])
            else:
                expressoes.append(coluna)
        parametros.append(proposta.pk)
        with connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO proposta_comercial ({', '.join(colunas)}) SELECT {', '.join(expressoes)} FROM proposta_comercial WHERE id = %s",
                parametros,
            )

    try:
        executor.migrate([("assinaturas", "0008_endurecer_transicoes_propostas")])
        organizacao = Organizacao.objects.create(nome="Migration 0009", slug="migration-proposta-0009")
        ator = criar_usuario(email="migration-proposta-0009@example.com")
        modelo_historico = executor.loader.project_state([("assinaturas", "0008_endurecer_transicoes_propostas")]).apps.get_model(
            "assinaturas", "PropostaComercial"
        )
        proposta = _criar_proposta(organizacao, model=modelo_historico)
        with organizacao_atual_privilegiada(organizacao.pk):
            inserir_aceita(proposta, ator, modelo_historico)

        executor = MigrationExecutor(connection)
        executor.migrate([("assinaturas", "0009_proteger_estado_inicial_proposta")])
        with (
            organizacao_atual_privilegiada(organizacao.pk),
            pytest.raises(DatabaseError, match="estado inicial"),
            transaction.atomic(),
        ):
            inserir_aceita(proposta, ator, modelo_historico)

        executor = MigrationExecutor(connection)
        executor.migrate([("assinaturas", "0008_endurecer_transicoes_propostas")])
        with organizacao_atual_privilegiada(organizacao.pk):
            inserir_aceita(proposta, ator, modelo_historico)
            with pytest.raises(DatabaseError, match="revisão"), transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("UPDATE proposta_comercial SET revisao = revisao + 1 WHERE id = %s", [proposta.pk])
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(alvos_finais)


@pytest.mark.django_db(transaction=True)
def test_migrations_0010_0011_0012_e_0013_sao_reversiveis_no_postgresql():
    executor = MigrationExecutor(connection)
    alvos_finais = executor.loader.graph.leaf_nodes()

    def triggers():
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT tgname
                FROM pg_trigger
                WHERE NOT tgisinternal
                  AND tgrelid IN ('assinatura_organizacao'::regclass, 'alteracao_assinatura'::regclass)
                """
            )
            return {row[0] for row in cursor.fetchall()}

    def definicao_guarda_assinatura():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_get_functiondef('validar_transicao_status_assinatura()'::regprocedure)")
            return cursor.fetchone()[0]

    def definicao_constraint_tipo():
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT pg_get_constraintdef(oid)
                FROM pg_constraint
                WHERE conrelid = 'alteracao_assinatura'::regclass
                  AND conname = 'alteracao_tipo_dominio'
                """
            )
            return cursor.fetchone()[0]

    try:
        executor.migrate([("assinaturas", "0010_adicionar_fallback_trial")])
        assert "60" in definicao_constraint_tipo()
        assert "assinatura_organizacao_transicao_status" in triggers()
        assert "alteracao_assinatura_validar_fallback_trial" not in triggers()
        assert "carencia_pagamento_iniciada_em" not in definicao_guarda_assinatura()

        executor = MigrationExecutor(connection)
        executor.migrate([("assinaturas", "0011_proteger_transicoes_acesso")])
        assert {
            "assinatura_organizacao_transicao_status",
            "alteracao_assinatura_validar_fallback_trial",
        } <= triggers()
        assert "carencia_pagamento_iniciada_em" in definicao_guarda_assinatura()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM pg_constraint
                WHERE conrelid = 'alteracao_assinatura'::regclass
                  AND conname = 'alteracao_fallback_trial_reservado'
                """
            )
            assert cursor.fetchone() == (1,)

        executor = MigrationExecutor(connection)
        executor.migrate([("assinaturas", "0012_endurecer_rollout_e_transicoes")])
        assert "Alteracao contratual exige incremento unitario" in definicao_guarda_assinatura()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT prosecdef, proconfig
                FROM pg_proc
                WHERE oid = 'public.selecionar_organizacoes_operacionais_assinatura(
                    text,bigint,integer,timestamp with time zone
                )'::regprocedure
                """
            )
            assert cursor.fetchone() == (False, ["search_path=pg_catalog"])

        executor = MigrationExecutor(connection)
        executor.migrate([("assinaturas", "0013_proteger_revisao_e_upserts")])
        assert "Revisao da assinatura nao pode mudar isoladamente" in definicao_guarda_assinatura()

        executor = MigrationExecutor(connection)
        executor.migrate([("assinaturas", "0012_endurecer_rollout_e_transicoes")])
        assert "Revisao da assinatura nao pode mudar isoladamente" not in definicao_guarda_assinatura()

        executor = MigrationExecutor(connection)
        executor.migrate([("assinaturas", "0011_proteger_transicoes_acesso")])
        assert "Alteracao contratual exige incremento unitario" not in definicao_guarda_assinatura()
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT to_regprocedure('public.selecionar_organizacoes_operacionais_assinatura(text,bigint,integer,timestamp with time zone)')"
            )
            assert cursor.fetchone() == (None,)

        executor = MigrationExecutor(connection)
        executor.migrate([("assinaturas", "0010_adicionar_fallback_trial")])
        assert "assinatura_organizacao_transicao_status" in triggers()
        assert "alteracao_assinatura_validar_fallback_trial" not in triggers()
        assert "carencia_pagamento_iniciada_em" not in definicao_guarda_assinatura()

        executor = MigrationExecutor(connection)
        executor.migrate([("assinaturas", "0009_proteger_estado_inicial_proposta")])
        assert "60" not in definicao_constraint_tipo()
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(alvos_finais)
