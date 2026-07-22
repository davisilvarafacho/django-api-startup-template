"""Prova de que o isolamento por organização acontece **no banco** (RLS).

Dois detalhes tornam este teste diferente de um teste comum:

1. Superusuários do Postgres ignoram RLS, e o dono da tabela também ignora a
   menos que exista `FORCE ROW LEVEL SECURITY`. Consultar com o usuário do
   Django (dono/superusuário) não provaria nada — por isso o teste cria um
   papel comum e consulta por ele.
2. O contexto é aplicado com `SET LOCAL`, que exige uma transação; a conexão do
   papel de teste abre a sua própria.

Como a verificação usa uma segunda conexão, os dados precisam estar commitados:
daí o `django_db(transaction=True)`.
"""
import psycopg2
import pytest

from django.db import connection, models

from apps.api.base.models import Base
from apps.organizacoes.models import Organizacao

PAPEL_TESTE = "rls_tester"
SENHA_TESTE = "rls_tester"


class RegistroRLS(Base):
    """Modelo concreto usado só para exercitar as policies herdadas de `Base`."""

    descricao = models.CharField(max_length=50)

    class Meta:
        app_label = "organizacoes"
        db_table = "registro_rls_teste"


TABELA = RegistroRLS._meta.db_table


@pytest.fixture
def ambiente_rls(django_db_setup, django_db_blocker):
    """Cria a tabela do modelo de teste, liga o RLS e provisiona o papel comum."""
    with django_db_blocker.unblock():
        with connection.schema_editor() as editor:
            editor.create_model(RegistroRLS)

        RegistroRLS.enable_rls()

        with connection.cursor() as cursor:
            cursor.execute(f"DROP ROLE IF EXISTS {PAPEL_TESTE}")
            cursor.execute(f"CREATE ROLE {PAPEL_TESTE} LOGIN PASSWORD '{SENHA_TESTE}'")
            cursor.execute(f"GRANT USAGE ON SCHEMA public TO {PAPEL_TESTE}")
            cursor.execute(f"GRANT SELECT ON {TABELA} TO {PAPEL_TESTE}")

        yield

        with connection.cursor() as cursor:
            cursor.execute(f"REVOKE ALL ON {TABELA} FROM {PAPEL_TESTE}")
            cursor.execute(f"REVOKE ALL ON SCHEMA public FROM {PAPEL_TESTE}")
        with connection.schema_editor() as editor:
            editor.delete_model(RegistroRLS)
        with connection.cursor() as cursor:
            cursor.execute(f"DROP OWNED BY {PAPEL_TESTE}")
            cursor.execute(f"DROP ROLE IF EXISTS {PAPEL_TESTE}")


def consultar_como_papel_comum(organizacao_id):
    """Lê a tabela por um papel sujeito ao RLS, sob o tenant informado.

    Args:
        organizacao_id: Organização a aplicar via `SET LOCAL`, ou `None` para
            consultar sem nenhum contexto.

    Returns:
        As descrições visíveis para aquele contexto.
    """
    parametros = connection.settings_dict
    conexao = psycopg2.connect(
        dbname=parametros["NAME"],
        user=PAPEL_TESTE,
        password=SENHA_TESTE,
        host=parametros["HOST"] or "127.0.0.1",
        port=parametros["PORT"] or 5432,
    )
    try:
        with conexao, conexao.cursor() as cursor:
            if organizacao_id is not None:
                cursor.execute("SELECT set_config('rls.tenant_id', %s, true)", [str(organizacao_id)])
            cursor.execute(f"SELECT descricao FROM {TABELA} ORDER BY descricao")
            return [linha[0] for linha in cursor.fetchall()]
    finally:
        conexao.close()


@pytest.fixture
def cenario(ambiente_rls):
    """Duas organizações, cada uma com um registro próprio."""
    org_a = Organizacao.objects.create(nome="Org A", slug="org-a")
    org_b = Organizacao.objects.create(nome="Org B", slug="org-b")

    RegistroRLS.objects.create(organizacao=org_a, descricao="registro-a")
    RegistroRLS.objects.create(organizacao=org_b, descricao="registro-b")

    return org_a, org_b


@pytest.mark.django_db(transaction=True)
def test_cada_organizacao_ve_apenas_os_proprios_registros(cenario):
    org_a, org_b = cenario

    assert consultar_como_papel_comum(org_a.id) == ["registro-a"]
    assert consultar_como_papel_comum(org_b.id) == ["registro-b"]


@pytest.mark.django_db(transaction=True)
def test_sem_contexto_nao_ve_nada(cenario):
    """Sem tenant definido o RLS falha fechado — nenhuma linha, não todas."""
    assert consultar_como_papel_comum(None) == []


@pytest.mark.django_db(transaction=True)
def test_contexto_de_organizacao_inexistente_nao_vaza(cenario):
    assert consultar_como_papel_comum(999_999) == []
