"""Persistencia e invariantes autoritativas das propostas comerciais."""

from datetime import timedelta

from django.conf import settings
from django.db import DatabaseError, IntegrityError, connection, connections, models, transaction
from django.utils import timezone

import psycopg2
import pytest

from apps.api.base.models import Base
from apps.assinaturas.models import (
    AssinaturaOrganizacao,
    ModoAtivacaoProposta,
    Periodicidade,
    Plano,
    PropostaComercial,
    StatusAssinatura,
    StatusFinanceiro,
    StatusPropostaComercial,
    VersaoPlano,
)
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Organizacao
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db

PAPEL_RLS_PROPOSTAS = "proposal_rls_tester"
SENHA_RLS_PROPOSTAS = "proposal_rls_tester"


@pytest.fixture(scope="module")
def papel_rls_propostas(django_db_setup, django_db_blocker):
    with django_db_blocker.unblock(), connection.cursor() as cursor:
        cursor.execute(f"DROP ROLE IF EXISTS {PAPEL_RLS_PROPOSTAS}")
        cursor.execute(f"CREATE ROLE {PAPEL_RLS_PROPOSTAS} LOGIN PASSWORD '{SENHA_RLS_PROPOSTAS}'")
        cursor.execute(f"GRANT USAGE ON SCHEMA public TO {PAPEL_RLS_PROPOSTAS}")
        cursor.execute(f"GRANT SELECT ON proposta_comercial TO {PAPEL_RLS_PROPOSTAS}")
    yield
    with django_db_blocker.unblock(), connection.cursor() as cursor:
        cursor.execute(f"DROP OWNED BY {PAPEL_RLS_PROPOSTAS}")
        cursor.execute(f"DROP ROLE IF EXISTS {PAPEL_RLS_PROPOSTAS}")


def _criar_versao(codigo: str = "enterprise-base") -> VersaoPlano:
    plano = Plano.objects.create(codigo=codigo, nome=codigo.title(), descricao="", visivel=False)
    return VersaoPlano.objects.create(
        plano=plano,
        numero=1,
        atual=False,
        seats_inclusos=2,
        carencia_pagamento_dias=7,
        carencia_excesso_seats_dias=7,
        recursos={"quantidade_projetos": 10},
    )


def _dados_proposta(organizacao: Organizacao, **sobrescritos):
    dados = {
        "organizacao": organizacao,
        "versao_plano_referencia": _criar_versao(f"enterprise-{organizacao.pk}"),
        "status": StatusPropostaComercial.RASCUNHO,
        "modo_ativacao": ModoAtivacaoProposta.PAGAMENTO,
        "revisao": 1,
        "periodicidade": Periodicidade.ANUAL,
        "moeda": "BRL",
        "valor_base_centavos": 120_000,
        "valor_seat_centavos": 5_000,
        "seats_inclusos": 10,
        "seats_contratados": 25,
        "expansao_automatica_seats": False,
        "recursos": {"quantidade_projetos": 100},
        "carencia_pagamento_dias": 15,
        "carencia_excesso_seats_dias": 10,
        "valida_ate": timezone.now() + timedelta(days=30),
    }
    dados.update(sobrescritos)
    return dados


def _criar_proposta(organizacao: Organizacao, **sobrescritos) -> PropostaComercial:
    with organizacao_atual_privilegiada(organizacao.pk):
        return PropostaComercial.objects.create(**_dados_proposta(organizacao, **sobrescritos))


def _dados_assinatura(organizacao: Organizacao, **sobrescritos):
    dados = {
        "organizacao": organizacao,
        "versao_plano": _criar_versao(f"assinatura-{organizacao.pk}"),
        "status": StatusAssinatura.ATIVA,
        "status_financeiro": StatusFinanceiro.REGULAR,
        "revisao": 1,
        "periodicidade": Periodicidade.MENSAL,
        "moeda": "BRL",
        "valor_base_centavos": 1_000,
        "valor_seat_centavos": 250,
        "seats_inclusos": 1,
        "seats_contratados": 3,
        "expansao_automatica_seats": False,
        "recursos": {"quantidade_projetos": 3},
        "carencia_pagamento_dias": 7,
        "carencia_excesso_seats_dias": 7,
        "chave_idempotencia": f"assinatura-{organizacao.pk}",
    }
    dados.update(sobrescritos)
    return dados


def test_proposta_e_tenantizada_e_estados_persistidos_usam_passos_de_dez():
    assert issubclass(PropostaComercial, Base)
    assert list(StatusPropostaComercial.values) == [10, 20, 30, 40, 50, 60, 70]
    assert list(ModoAtivacaoProposta.values) == [10, 20]
    assert isinstance(PropostaComercial._meta.get_field("status"), models.PositiveSmallIntegerField)
    assert isinstance(PropostaComercial._meta.get_field("modo_ativacao"), models.PositiveSmallIntegerField)


def test_proposta_materializa_snapshot_integral_de_recursos():
    organizacao = Organizacao.objects.create(nome="Snapshot enterprise", slug="snapshot-enterprise")

    proposta = _criar_proposta(organizacao)

    assert proposta.recursos == {
        "papeis_isentos_seat": [],
        "quantidade_projetos": 100,
    }


def test_banco_exige_objeto_json_para_recursos_da_proposta():
    organizacao = Organizacao.objects.create(nome="Recursos enterprise", slug="recursos-enterprise")
    proposta = _criar_proposta(organizacao)

    with organizacao_atual_privilegiada(organizacao.pk), pytest.raises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "UPDATE proposta_comercial SET recursos = '[]'::jsonb WHERE id = %s",
            [proposta.pk],
        )


@pytest.mark.parametrize(
    "sobrescritos",
    [
        {"status": 999},
        {"modo_ativacao": 999},
        {"revisao": 0},
        {"moeda": "brl"},
        {"status": StatusPropostaComercial.ENVIADA, "enviada_em": None},
        {
            "status": StatusPropostaComercial.ACEITA,
            "enviada_em": timezone.now(),
            "aceita_em": timezone.now(),
            "aceita_por": None,
        },
        {
            "status": StatusPropostaComercial.ATIVADA,
            "modo_ativacao": ModoAtivacaoProposta.CONTRATUAL,
            "enviada_em": timezone.now(),
            "aceita_em": timezone.now(),
            "aceita_por": criar_usuario,
            "ativada_em": timezone.now(),
            "ativada_por": None,
            "justificativa_ativacao": "",
        },
    ],
)
def test_banco_recusa_dominio_e_estado_incoerente(sobrescritos):
    organizacao = Organizacao.objects.create(nome="Invalida", slug=f"proposta-invalida-{hash(str(sobrescritos))}")
    dados = _dados_proposta(organizacao)
    if sobrescritos.get("aceita_por") is criar_usuario:
        sobrescritos = {**sobrescritos, "aceita_por": criar_usuario(email=f"aceite-{organizacao.pk}@example.com")}
    dados.update(sobrescritos)

    with organizacao_atual_privilegiada(organizacao.pk), pytest.raises(DatabaseError), transaction.atomic():
        PropostaComercial.objects.create(**dados)


def test_assinatura_exige_exatamente_uma_origem():
    organizacao = Organizacao.objects.create(nome="XOR", slug="xor-enterprise")
    proposta = _criar_proposta(organizacao)
    dados = _dados_assinatura(organizacao, versao_plano=None, proposta_comercial=proposta)

    with organizacao_atual_privilegiada(organizacao.pk):
        enterprise = AssinaturaOrganizacao.objects.create(**dados)
        assert enterprise.proposta_comercial == proposta

        for origens in (
            {"versao_plano": None, "proposta_comercial": None},
            {"versao_plano": _criar_versao("xor-dupla"), "proposta_comercial": proposta},
        ):
            with pytest.raises(IntegrityError), transaction.atomic():
                AssinaturaOrganizacao.objects.create(
                    **_dados_assinatura(
                        organizacao,
                        status=StatusAssinatura.ENCERRADA,
                        encerrada_em=timezone.now(),
                        motivo_encerramento="teste",
                        chave_idempotencia=f"xor-{origens['versao_plano']}",
                        **origens,
                    )
                )


def test_banco_recusa_proposta_de_outro_tenant_na_assinatura():
    org_a = Organizacao.objects.create(nome="A", slug="proposta-tenant-a")
    org_b = Organizacao.objects.create(nome="B", slug="proposta-tenant-b")
    proposta_b = _criar_proposta(org_b)

    with organizacao_atual_privilegiada(org_a.pk), pytest.raises(DatabaseError, match="organização"), transaction.atomic():
        AssinaturaOrganizacao.objects.create(**_dados_assinatura(org_a, versao_plano=None, proposta_comercial=proposta_b))


def test_proposta_enviada_protege_termos_e_identidade_contra_orm_e_sql():
    organizacao = Organizacao.objects.create(nome="Imutavel", slug="proposta-imutavel")
    ator = criar_usuario(email="proposta-imutavel@example.com")
    agora = timezone.now()
    proposta = _criar_proposta(
        organizacao,
        status=StatusPropostaComercial.ENVIADA,
        enviada_em=agora,
    )

    with organizacao_atual_privilegiada(organizacao.pk):
        proposta.valor_base_centavos += 1
        with pytest.raises(ValueError, match="termos.*imutáveis"):
            proposta.save()

        with pytest.raises(ValueError, match="termos.*imutáveis"):
            PropostaComercial.objects.filter(pk=proposta.pk).update(seats_contratados=99)

        with pytest.raises(DatabaseError, match="termos.*imutáveis"), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "UPDATE proposta_comercial SET created_by_id = %s WHERE id = %s",
                [ator.pk, proposta.pk],
            )


@pytest.mark.parametrize("campo", ["status", "revisao", "enviada_em", "aceita_por", "cancelada_em"])
def test_querysets_bloqueiam_campos_de_transicao_da_proposta(campo):
    organizacao = Organizacao.objects.create(nome=f"Guard ORM {campo}", slug=f"proposta-guard-orm-{campo.replace('_', '-')}")
    proposta = _criar_proposta(organizacao)
    valores = {
        "status": StatusPropostaComercial.ENVIADA,
        "revisao": 2,
        "enviada_em": timezone.now(),
        "aceita_por": criar_usuario(email=f"guard-{campo}@example.com"),
        "cancelada_em": timezone.now(),
    }

    with organizacao_atual_privilegiada(organizacao.pk), pytest.raises(ValueError, match="transições nominais"):
        PropostaComercial.objects.filter(pk=proposta.pk).update(**{campo: valores[campo]})

    setattr(proposta, campo, valores[campo])
    with organizacao_atual_privilegiada(organizacao.pk), pytest.raises(ValueError, match="transições nominais"):
        PropostaComercial.objects.bulk_update([proposta], [campo])


def test_save_publico_nao_executa_transicao_de_proposta():
    organizacao = Organizacao.objects.create(nome="Guard save", slug="proposta-guard-save")
    proposta = _criar_proposta(organizacao)
    proposta.status = StatusPropostaComercial.ENVIADA
    proposta.enviada_em = timezone.now()
    proposta.revisao += 1

    with organizacao_atual_privilegiada(organizacao.pk), pytest.raises(ValueError, match="casos de uso nominais"):
        proposta.save(update_fields=["status", "enviada_em", "revisao"])


def test_trigger_recusa_salto_de_estado_e_revisao_isolada():
    organizacao = Organizacao.objects.create(nome="Guard SQL", slug="proposta-guard-sql")
    proposta = _criar_proposta(organizacao)
    ator = criar_usuario(email="guard-sql@example.com")
    agora = timezone.now()

    with (
        organizacao_atual_privilegiada(organizacao.pk),
        pytest.raises(DatabaseError, match="Transição inválida"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            """
            UPDATE proposta_comercial
               SET status = %s, revisao = 2, enviada_em = %s,
                   aceita_em = %s, aceita_por_id = %s
             WHERE id = %s
            """,
            [StatusPropostaComercial.ACEITA, agora, agora, ator.pk, proposta.pk],
        )

    with (
        organizacao_atual_privilegiada(organizacao.pk),
        pytest.raises(DatabaseError, match="revisão"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("UPDATE proposta_comercial SET revisao = revisao + 1 WHERE id = %s", [proposta.pk])


@pytest.mark.parametrize(
    "sobrescritos",
    [
        {
            "status": StatusPropostaComercial.ACEITA,
            "enviada_em": timezone.now(),
            "aceita_em": timezone.now() - timedelta(seconds=1),
            "aceita_por": criar_usuario,
        },
        {
            "status": StatusPropostaComercial.CANCELADA,
            "enviada_em": None,
            "aceita_em": timezone.now(),
            "aceita_por": criar_usuario,
            "cancelada_em": timezone.now() + timedelta(seconds=1),
            "cancelada_por": criar_usuario,
        },
        {
            "status": StatusPropostaComercial.EXPIRADA,
            "enviada_em": timezone.now(),
            "expirada_em": timezone.now() + timedelta(seconds=1),
        },
    ],
)
def test_banco_recusa_cronologia_incoerente_em_aceite_e_cancelamento(sobrescritos):
    organizacao = Organizacao.objects.create(nome="Cronologia inválida", slug=f"proposta-cronologia-{hash(str(sobrescritos))}")
    dados = _dados_proposta(organizacao)
    dados.update(
        {
            campo: criar_usuario(email=f"cronologia-{campo}-{organizacao.pk}@example.com") if valor is criar_usuario else valor
            for campo, valor in sobrescritos.items()
        }
    )

    with organizacao_atual_privilegiada(organizacao.pk), pytest.raises(IntegrityError), transaction.atomic():
        PropostaComercial.objects.create(**dados)


def test_banco_nao_admite_estado_ativado_para_proposta_de_pagamento():
    organizacao = Organizacao.objects.create(nome="Pagamento não ativa", slug="proposta-pagamento-nao-ativa")
    ator = criar_usuario(email="pagamento-nao-ativa@example.com")
    agora = timezone.now()

    with organizacao_atual_privilegiada(organizacao.pk), pytest.raises(IntegrityError), transaction.atomic():
        PropostaComercial.objects.create(
            **_dados_proposta(
                organizacao,
                status=StatusPropostaComercial.ATIVADA,
                modo_ativacao=ModoAtivacaoProposta.PAGAMENTO,
                enviada_em=agora,
                aceita_em=agora,
                aceita_por=ator,
                ativada_em=agora,
            )
        )


@pytest.mark.django_db(transaction=True)
def test_rls_real_isola_propostas_para_papel_postgresql_comum(papel_rls_propostas):
    org_a = Organizacao.objects.create(nome="RLS A", slug="proposta-rls-a")
    org_b = Organizacao.objects.create(nome="RLS B", slug="proposta-rls-b")
    proposta_a = _criar_proposta(org_a)
    _criar_proposta(org_b)
    database = settings.DATABASES["default"]

    conexao = psycopg2.connect(
        dbname=database["NAME"],
        user=PAPEL_RLS_PROPOSTAS,
        password=SENHA_RLS_PROPOSTAS,
        host=database["HOST"],
        port=database["PORT"],
    )
    try:
        with conexao, conexao.cursor() as cursor:
            cursor.execute("SELECT set_config('rls.tenant_id', %s, true)", [str(org_a.pk)])
            cursor.execute("SELECT id FROM proposta_comercial ORDER BY id")
            assert cursor.fetchall() == [(proposta_a.pk,)]
    finally:
        conexao.close()
        connections.close_all()
