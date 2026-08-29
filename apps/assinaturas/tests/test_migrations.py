from concurrent.futures import ThreadPoolExecutor
from threading import Event

from django.db import DatabaseError, close_old_connections, connection, connections, transaction
from django.db.migrations.executor import MigrationExecutor
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


@pytest.mark.django_db(databases={"default", "logging"})
def test_migration_de_triggers_e_compativel_com_alias_sqlite():
    assert connections["logging"].vendor == "sqlite"
