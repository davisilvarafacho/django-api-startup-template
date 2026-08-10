from django.db import connection
from django.test.utils import CaptureQueriesContext

import pytest

from apps.api.metadata.models import Metadata
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Organizacao


def updates(execute):
    with CaptureQueriesContext(connection) as queries:
        execute()
    return [query["sql"] for query in queries if query["sql"].lstrip().upper().startswith("UPDATE")]


@pytest.fixture
def metadata_table(django_db_setup, django_db_blocker):
    table = Metadata._meta.db_table
    with django_db_blocker.unblock():
        created = table not in connection.introspection.table_names()
        if created:
            with connection.schema_editor() as editor:
                editor.create_model(Metadata)

    yield

    if created:
        with django_db_blocker.unblock(), connection.schema_editor() as editor:
            editor.delete_model(Metadata)


@pytest.mark.django_db
def test_save_atualiza_somente_campo_modificado_e_timestamp():
    organizacao = Organizacao.objects.create(nome="Antes", slug="antes")
    organizacao.nome = "Depois"

    sql = updates(organizacao.save)

    assert len(sql) == 1
    assert '"nome"' in sql[0]
    assert '"last_modified_at"' in sql[0]
    assert '"slug"' not in sql[0]


@pytest.mark.django_db
def test_save_sem_mudanca_nao_executa_update():
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme")

    assert updates(organizacao.save) == []


@pytest.mark.django_db
def test_save_com_update_fields_preserva_contrato_explicito():
    organizacao = Organizacao.objects.create(nome="Antes", slug="antes")
    organizacao.nome = "Depois"

    sql = updates(lambda: organizacao.save(update_fields=["nome"]))

    assert len(sql) == 1
    assert '"nome"' in sql[0]
    assert '"last_modified_at"' not in sql[0]


@pytest.mark.django_db
def test_update_fields_mantem_outros_campos_pendentes_para_o_proximo_save():
    organizacao = Organizacao.objects.create(nome="Antes", slug="antes-pendente")
    organizacao.nome = "Depois"
    organizacao.slug = "depois-pendente"
    organizacao.save(update_fields=["nome"])

    sql = updates(organizacao.save)

    assert len(sql) == 1
    assert '"slug"' in sql[0]
    assert '"nome"' not in sql[0]


@pytest.mark.django_db
def test_save_ignora_campo_revertido_ao_valor_original():
    organizacao = Organizacao.objects.create(nome="Original", slug="original")
    organizacao.nome = "Temporário"
    organizacao.nome = "Original"

    assert updates(organizacao.save) == []


@pytest.mark.django_db
def test_save_nao_carrega_nem_atualiza_campo_deferred():
    criada = Organizacao.objects.create(nome="Acme", slug="acme-deferred")
    organizacao = Organizacao.objects.defer("nome").get(pk=criada.pk)

    assert "nome" in organizacao.get_deferred_fields()
    assert updates(organizacao.save) == []
    assert "nome" in organizacao.get_deferred_fields()


@pytest.mark.django_db(transaction=True)
def test_save_detecta_mutacao_in_place_de_json(metadata_table):
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme-json")
    with organizacao_atual_privilegiada(organizacao.pk):
        metadata = Metadata.objects.create(
            organizacao=organizacao,
            content_type=Organizacao.get_content_type(),
            object_id=organizacao.pk,
            dados={"tags": []},
        )
        metadata.dados["tags"].append("nova")

        sql = updates(metadata.save)

    assert len(sql) == 1
    assert '"dados"' in sql[0]
