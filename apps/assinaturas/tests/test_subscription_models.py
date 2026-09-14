"""Contratos persistidos de assinatura e de suas alterações."""

from datetime import timedelta

from django.db import DatabaseError, IntegrityError, connection, models, transaction
from django.utils import timezone

import pytest

from apps.api.base.models import Base
from apps.assinaturas.models import (
    AlteracaoAssinatura,
    AssinaturaOrganizacao,
    MomentoAplicacaoAlteracaoAssinatura,
    Periodicidade,
    Plano,
    PoliticaTrial,
    PrecoPlano,
    StatusAlteracaoAssinatura,
    StatusAssinatura,
    StatusFinanceiro,
    TipoAlteracaoAssinatura,
    VersaoPlano,
)
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Organizacao

pytestmark = pytest.mark.django_db


def _criar_versao(*, codigo: str) -> VersaoPlano:
    plano = Plano.objects.create(codigo=codigo, nome=codigo.title(), descricao="", visivel=True)
    versao = VersaoPlano.objects.create(
        plano=plano,
        numero=1,
        atual=False,
        seats_inclusos=1,
        limite_seats_trial=5,
        duracao_trial_dias=14,
        carencia_pagamento_dias=7,
        carencia_excesso_seats_dias=7,
        expansao_automatica_seats=False,
        recursos={"quantidade_projetos": 3},
    )
    PrecoPlano.objects.create(
        versao_plano=versao,
        periodicidade=Periodicidade.MENSAL,
        moeda="BRL",
        valor_base_centavos=1000,
        valor_seat_centavos=250,
    )
    return versao


def _dados_assinatura(organizacao: Organizacao, versao: VersaoPlano, **sobrescritos):
    dados = {
        "organizacao": organizacao,
        "versao_plano": versao,
        "status": StatusAssinatura.ATIVA,
        "status_financeiro": StatusFinanceiro.REGULAR,
        "revisao": 1,
        "periodicidade": Periodicidade.MENSAL,
        "moeda": "BRL",
        "valor_base_centavos": 1000,
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


def _criar_assinatura(organizacao: Organizacao, versao: VersaoPlano, **sobrescritos) -> AssinaturaOrganizacao:
    with organizacao_atual_privilegiada(organizacao.pk):
        return AssinaturaOrganizacao.objects.create(**_dados_assinatura(organizacao, versao, **sobrescritos))


def test_estados_persistidos_usam_smallint_e_passos_de_dez():
    assert list(StatusAssinatura.values) == [10, 20, 30, 40]
    assert list(StatusFinanceiro.values) == [10, 20, 30, 40, 50]
    assert list(StatusAlteracaoAssinatura.values) == [10, 20, 30, 40, 50]
    assert list(MomentoAplicacaoAlteracaoAssinatura.values) == [10, 20]
    assert list(TipoAlteracaoAssinatura.values) == [10, 20, 30, 40, 50, 60]

    for model, campo in (
        (AssinaturaOrganizacao, "status"),
        (AssinaturaOrganizacao, "status_financeiro"),
        (AssinaturaOrganizacao, "politica_trial"),
        (AlteracaoAssinatura, "tipo"),
        (AlteracaoAssinatura, "momento_aplicacao"),
        (AlteracaoAssinatura, "status"),
    ):
        assert isinstance(model._meta.get_field(campo), models.PositiveSmallIntegerField)


def test_contratos_e_alteracoes_sao_models_tenantizados():
    assert issubclass(AssinaturaOrganizacao, Base)
    assert issubclass(AlteracaoAssinatura, Base)


def test_origem_de_catalogo_exige_versao_plano_ate_a_task_10():
    organizacao = Organizacao.objects.create(nome="Sem origem", slug="sem-origem")

    with organizacao_atual_privilegiada(organizacao.pk), pytest.raises(IntegrityError), transaction.atomic():
        AssinaturaOrganizacao.objects.create(**_dados_assinatura(organizacao, None))


def test_organizacao_tem_no_maximo_um_contrato_corrente():
    organizacao = Organizacao.objects.create(nome="Corrente", slug="corrente")
    versao = _criar_versao(codigo="corrente")
    _criar_assinatura(organizacao, versao)

    with organizacao_atual_privilegiada(organizacao.pk), pytest.raises(IntegrityError), transaction.atomic():
        AssinaturaOrganizacao.objects.create(
            **_dados_assinatura(
                organizacao,
                versao,
                chave_idempotencia="outro-ciclo",
                status=StatusAssinatura.EM_TRIAL,
                status_financeiro=StatusFinanceiro.ISENTO,
                politica_trial=PoliticaTrial.SEM_FORMA_PAGAMENTO,
                trial_iniciado_em=timezone.now(),
                trial_termina_em=timezone.now() + timedelta(days=14),
            )
        )


def test_chave_idempotente_e_unica_por_organizacao_e_pode_repetir_em_outro_tenant():
    org_a = Organizacao.objects.create(nome="A", slug="idem-a")
    org_b = Organizacao.objects.create(nome="B", slug="idem-b")
    versao = _criar_versao(codigo="idem")
    _criar_assinatura(org_a, versao, chave_idempotencia="mesma-chave")

    with organizacao_atual_privilegiada(org_a.pk), pytest.raises(IntegrityError), transaction.atomic():
        AssinaturaOrganizacao.objects.create(
            **_dados_assinatura(
                org_a,
                versao,
                status=StatusAssinatura.ENCERRADA,
                encerrada_em=timezone.now(),
                motivo_encerramento="fim",
                chave_idempotencia="mesma-chave",
            )
        )

    assinatura_b = _criar_assinatura(org_b, versao, chave_idempotencia="mesma-chave")
    assert assinatura_b.organizacao == org_b


def test_snapshot_de_recursos_e_validado_e_materializa_todas_as_chaves():
    organizacao = Organizacao.objects.create(nome="Snapshot", slug="snapshot")
    versao = _criar_versao(codigo="snapshot")

    assinatura = _criar_assinatura(organizacao, versao, recursos={"quantidade_projetos": 8})

    assert assinatura.recursos == {
        "papeis_isentos_seat": [],
        "quantidade_projetos": 8,
    }

    with organizacao_atual_privilegiada(organizacao.pk), pytest.raises(ValueError, match="recurso desconhecido"):
        AssinaturaOrganizacao.objects.create(
            **_dados_assinatura(
                organizacao,
                versao,
                status=StatusAssinatura.ENCERRADA,
                encerrada_em=timezone.now(),
                motivo_encerramento="outro ciclo",
                chave_idempotencia="snapshot-invalido",
                recursos={"desconhecido": True},
            )
        )


@pytest.mark.parametrize(
    "sobrescritos",
    [
        {"status": StatusAssinatura.ENCERRADA},
        {"encerrada_em": timezone.now(), "motivo_encerramento": "indevido"},
        {
            "status": StatusAssinatura.EM_TRIAL,
            "status_financeiro": StatusFinanceiro.ISENTO,
            "politica_trial": None,
            "trial_iniciado_em": None,
            "trial_termina_em": None,
        },
        {
            "status": StatusAssinatura.EM_TRIAL,
            "status_financeiro": StatusFinanceiro.ISENTO,
            "politica_trial": PoliticaTrial.SEM_FORMA_PAGAMENTO,
            "trial_iniciado_em": timezone.now(),
            "trial_termina_em": timezone.now() - timedelta(seconds=1),
        },
        {"periodo_atual_iniciado_em": timezone.now(), "periodo_atual_termina_em": None},
        {
            "carencia_pagamento_iniciada_em": timezone.now(),
            "carencia_pagamento_termina_em": timezone.now() - timedelta(seconds=1),
        },
        {"moeda": "brl"},
        {"revisao": 0},
        {"status": StatusAssinatura.ATIVA, "status_financeiro": StatusFinanceiro.PENDENTE},
    ],
)
def test_banco_recusa_estados_datas_moeda_e_revisao_incoerentes(sobrescritos):
    organizacao = Organizacao.objects.create(nome="Inválida", slug=f"invalida-{hash(str(sobrescritos))}")
    versao = _criar_versao(codigo=f"invalida-{organizacao.pk}")

    with organizacao_atual_privilegiada(organizacao.pk), pytest.raises(DatabaseError), transaction.atomic():
        AssinaturaOrganizacao.objects.create(**_dados_assinatura(organizacao, versao, **sobrescritos))


def test_alteracao_guarda_pedido_e_snapshots_e_idempotencia_tenantizada():
    org_a = Organizacao.objects.create(nome="Alteração A", slug="alteracao-a")
    org_b = Organizacao.objects.create(nome="Alteração B", slug="alteracao-b")
    versao = _criar_versao(codigo="alteracao")
    assinatura_a = _criar_assinatura(org_a, versao)
    assinatura_b = _criar_assinatura(org_b, versao)
    aplicar_em = timezone.now() + timedelta(days=30)
    dados = {
        "tipo": TipoAlteracaoAssinatura.REDUCAO_SEATS,
        "momento_aplicacao": MomentoAplicacaoAlteracaoAssinatura.PROXIMO_CICLO,
        "status": StatusAlteracaoAssinatura.SOLICITADA,
        "revisao_esperada": 1,
        "chave_idempotencia": "alteracao-idem",
        "pedido": {"seats_contratados": 2},
        "snapshot_anterior": {"revisao": 1, "seats_contratados": 3},
        "snapshot_pretendido": {"revisao": 2, "seats_contratados": 2},
        "aplicar_em": aplicar_em,
    }

    with organizacao_atual_privilegiada(org_a.pk):
        AlteracaoAssinatura.objects.create(organizacao=org_a, assinatura=assinatura_a, **dados)
        with pytest.raises(IntegrityError), transaction.atomic():
            AlteracaoAssinatura.objects.create(organizacao=org_a, assinatura=assinatura_a, **dados)

    with organizacao_atual_privilegiada(org_b.pk):
        outra = AlteracaoAssinatura.objects.create(organizacao=org_b, assinatura=assinatura_b, **dados)

    assert outra.snapshot_anterior["revisao"] == 1
    assert outra.snapshot_pretendido["seats_contratados"] == 2


def test_pedido_e_snapshots_da_alteracao_nao_podem_ser_reescritos():
    organizacao = Organizacao.objects.create(nome="Imutável", slug="alteracao-imutavel")
    versao = _criar_versao(codigo="alteracao-imutavel")
    assinatura = _criar_assinatura(organizacao, versao)
    with organizacao_atual_privilegiada(organizacao.pk):
        alteracao = AlteracaoAssinatura.objects.create(
            organizacao=organizacao,
            assinatura=assinatura,
            tipo=TipoAlteracaoAssinatura.AUMENTO_SEATS,
            momento_aplicacao=MomentoAplicacaoAlteracaoAssinatura.IMEDIATA,
            status=StatusAlteracaoAssinatura.SOLICITADA,
            revisao_esperada=1,
            chave_idempotencia="imutavel",
            pedido={"seats_contratados": 4},
            snapshot_anterior={"revisao": 1},
            snapshot_pretendido={"revisao": 2},
        )

        alteracao.pedido = {"adulterado": True}
        with pytest.raises(ValueError, match="imutáveis"):
            alteracao.save()

        with pytest.raises(ValueError, match="imutáveis"):
            AlteracaoAssinatura.objects.filter(pk=alteracao.pk).update(snapshot_anterior={"adulterado": True})


def test_banco_protege_snapshots_de_alteracao_contra_sql_direto():
    organizacao = Organizacao.objects.create(nome="Trigger", slug="alteracao-trigger")
    versao = _criar_versao(codigo="alteracao-trigger")
    assinatura = _criar_assinatura(organizacao, versao)
    with organizacao_atual_privilegiada(organizacao.pk):
        alteracao = AlteracaoAssinatura.objects.create(
            organizacao=organizacao,
            assinatura=assinatura,
            tipo=TipoAlteracaoAssinatura.AUMENTO_SEATS,
            momento_aplicacao=MomentoAplicacaoAlteracaoAssinatura.IMEDIATA,
            status=StatusAlteracaoAssinatura.SOLICITADA,
            revisao_esperada=1,
            chave_idempotencia="trigger",
            pedido={"seats_contratados": 4},
            snapshot_anterior={"revisao": 1},
            snapshot_pretendido={"revisao": 2},
        )

        with pytest.raises(DatabaseError, match="imutáveis"), transaction.atomic():
            with transaction.get_connection().cursor() as cursor:
                cursor.execute(
                    "UPDATE alteracao_assinatura SET pedido = %s WHERE id = %s",
                    ['{"adulterado": true}', alteracao.pk],
                )


def test_alteracao_agendada_exige_data_e_aplicacao_registrada_e_coerente():
    organizacao = Organizacao.objects.create(nome="Agenda", slug="alteracao-agenda")
    versao = _criar_versao(codigo="alteracao-agenda")
    assinatura = _criar_assinatura(organizacao, versao)

    with organizacao_atual_privilegiada(organizacao.pk), pytest.raises(IntegrityError), transaction.atomic():
        AlteracaoAssinatura.objects.create(
            organizacao=organizacao,
            assinatura=assinatura,
            tipo=TipoAlteracaoAssinatura.REDUCAO_SEATS,
            momento_aplicacao=MomentoAplicacaoAlteracaoAssinatura.PROXIMO_CICLO,
            status=StatusAlteracaoAssinatura.SOLICITADA,
            revisao_esperada=1,
            chave_idempotencia="sem-data",
            pedido={"seats_contratados": 2},
            snapshot_anterior={"revisao": 1},
            snapshot_pretendido={"revisao": 2},
        )

    with organizacao_atual_privilegiada(organizacao.pk), pytest.raises(IntegrityError), transaction.atomic():
        AlteracaoAssinatura.objects.create(
            organizacao=organizacao,
            assinatura=assinatura,
            tipo=TipoAlteracaoAssinatura.AUMENTO_SEATS,
            momento_aplicacao=MomentoAplicacaoAlteracaoAssinatura.IMEDIATA,
            status=StatusAlteracaoAssinatura.CONFIRMADA,
            revisao_esperada=1,
            chave_idempotencia="aplicacao-incompleta",
            pedido={"seats_contratados": 4},
            snapshot_anterior={"revisao": 1},
            snapshot_pretendido={"revisao": 2},
            aplicada_em=timezone.now(),
            revisao_aplicada=None,
        )


@pytest.mark.parametrize(
    "sobrescritos",
    [
        {"periodicidade": 99},
        {
            "status": StatusAssinatura.ENCERRADA,
            "status_financeiro": 99,
            "encerrada_em": timezone.now(),
            "motivo_encerramento": "dominio",
        },
        {
            "politica_trial": 99,
            "trial_iniciado_em": timezone.now(),
            "trial_termina_em": timezone.now() + timedelta(days=1),
        },
    ],
)
def test_banco_recusa_valores_fora_dos_dominios_da_assinatura(sobrescritos):
    organizacao = Organizacao.objects.create(nome="Domínio contrato", slug=f"dominio-contrato-{hash(str(sobrescritos))}")
    versao = _criar_versao(codigo=f"dominio-contrato-{organizacao.pk}")

    with organizacao_atual_privilegiada(organizacao.pk), pytest.raises(IntegrityError), transaction.atomic():
        AssinaturaOrganizacao.objects.create(**_dados_assinatura(organizacao, versao, **sobrescritos))


@pytest.mark.parametrize(
    "sobrescritos",
    [
        {"cancelamento_agendado_para": timezone.now() + timedelta(days=10)},
        {
            "periodo_atual_iniciado_em": timezone.now() - timedelta(days=1),
            "periodo_atual_termina_em": timezone.now() + timedelta(days=30),
            "cancelamento_agendado_para": timezone.now() + timedelta(days=20),
        },
        {
            "status": StatusAssinatura.PENDENTE,
            "status_financeiro": StatusFinanceiro.PENDENTE,
            "periodo_atual_iniciado_em": timezone.now() - timedelta(days=1),
            "periodo_atual_termina_em": timezone.now() + timedelta(days=30),
            "cancelamento_agendado_para": timezone.now() + timedelta(days=30),
        },
    ],
)
def test_banco_recusa_cancelamento_agendado_fora_do_fim_de_periodo_ativo(sobrescritos):
    organizacao = Organizacao.objects.create(nome="Cancelamento incoerente", slug=f"cancelamento-{hash(str(sobrescritos))}")
    versao = _criar_versao(codigo=f"cancelamento-{organizacao.pk}")

    with organizacao_atual_privilegiada(organizacao.pk), pytest.raises(IntegrityError), transaction.atomic():
        AssinaturaOrganizacao.objects.create(**_dados_assinatura(organizacao, versao, **sobrescritos))


def test_banco_recusa_regressao_de_status_da_assinatura_por_sql_direto():
    organizacao = Organizacao.objects.create(nome="Transição contrato", slug="transicao-contrato")
    versao = _criar_versao(codigo="transicao-contrato")
    assinatura = _criar_assinatura(organizacao, versao)

    with organizacao_atual_privilegiada(organizacao.pk), pytest.raises(DatabaseError, match="(?i)transição inválida"), transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE assinatura_organizacao SET status = %s, status_financeiro = %s WHERE id = %s",
                [StatusAssinatura.PENDENTE, StatusFinanceiro.PENDENTE, assinatura.pk],
            )


def _dados_alteracao(organizacao, assinatura, **sobrescritos):
    dados = {
        "organizacao": organizacao,
        "assinatura": assinatura,
        "tipo": TipoAlteracaoAssinatura.AUMENTO_SEATS,
        "momento_aplicacao": MomentoAplicacaoAlteracaoAssinatura.IMEDIATA,
        "status": StatusAlteracaoAssinatura.SOLICITADA,
        "revisao_esperada": assinatura.revisao,
        "chave_idempotencia": f"invariante-{organizacao.pk}-{hash(str(sobrescritos))}",
        "pedido": {"seats_contratados": assinatura.seats_contratados + 1},
        "snapshot_anterior": {"revisao": assinatura.revisao},
        "snapshot_pretendido": {"revisao": assinatura.revisao + 1},
    }
    dados.update(sobrescritos)
    return dados


@pytest.mark.parametrize(
    "sobrescritos",
    [
        {"tipo": 99},
        {"momento_aplicacao": 99, "aplicar_em": timezone.now() + timedelta(days=1)},
        {"status": 99},
    ],
)
def test_banco_recusa_valores_fora_dos_dominios_da_alteracao(sobrescritos):
    organizacao = Organizacao.objects.create(nome="Domínio alteração", slug=f"dominio-alteracao-{hash(str(sobrescritos))}")
    versao = _criar_versao(codigo=f"dominio-alteracao-{organizacao.pk}")
    assinatura = _criar_assinatura(organizacao, versao)

    with organizacao_atual_privilegiada(organizacao.pk), pytest.raises(IntegrityError), transaction.atomic():
        AlteracaoAssinatura.objects.create(**_dados_alteracao(organizacao, assinatura, **sobrescritos))


@pytest.mark.parametrize(
    "sobrescritos",
    [
        {"status": StatusAlteracaoAssinatura.SOLICITADA, "processada_em": timezone.now()},
        {"status": StatusAlteracaoAssinatura.FALHOU},
        {
            "status": StatusAlteracaoAssinatura.FALHOU,
            "processada_em": timezone.now(),
            "falha_codigo": "",
            "falha_mensagem": "falhou",
        },
        {"status": StatusAlteracaoAssinatura.CANCELADA},
        {"status": StatusAlteracaoAssinatura.CONFIRMADA, "processada_em": timezone.now()},
        {
            "status": StatusAlteracaoAssinatura.SOLICITADA,
            "falha_codigo": "indevido",
            "falha_mensagem": "indevido",
        },
    ],
)
def test_banco_recusa_status_e_metadados_de_processamento_incoerentes(sobrescritos):
    organizacao = Organizacao.objects.create(nome="Processamento", slug=f"processamento-{hash(str(sobrescritos))}")
    versao = _criar_versao(codigo=f"processamento-{organizacao.pk}")
    assinatura = _criar_assinatura(organizacao, versao)

    with organizacao_atual_privilegiada(organizacao.pk), pytest.raises(IntegrityError), transaction.atomic():
        AlteracaoAssinatura.objects.create(**_dados_alteracao(organizacao, assinatura, **sobrescritos))


def test_banco_recusa_agenda_em_alteracao_imediata():
    organizacao = Organizacao.objects.create(nome="Imediata", slug="imediata-sem-agenda")
    versao = _criar_versao(codigo="imediata-sem-agenda")
    assinatura = _criar_assinatura(organizacao, versao)

    with organizacao_atual_privilegiada(organizacao.pk), pytest.raises(IntegrityError), transaction.atomic():
        AlteracaoAssinatura.objects.create(
            **_dados_alteracao(
                organizacao,
                assinatura,
                aplicar_em=timezone.now() + timedelta(days=1),
            )
        )


def test_queryset_generico_nao_pode_transicionar_status_de_alteracao():
    organizacao = Organizacao.objects.create(nome="Transição ORM", slug="transicao-orm")
    versao = _criar_versao(codigo="transicao-orm")
    assinatura = _criar_assinatura(organizacao, versao)
    with organizacao_atual_privilegiada(organizacao.pk):
        alteracao = AlteracaoAssinatura.objects.create(**_dados_alteracao(organizacao, assinatura))

        with pytest.raises(ValueError, match="transições nominais"):
            AlteracaoAssinatura.objects.filter(pk=alteracao.pk).update(status=StatusAlteracaoAssinatura.AGUARDANDO_GATEWAY)


def test_banco_recusa_regressao_de_status_por_sql_direto():
    organizacao = Organizacao.objects.create(nome="Transição SQL", slug="transicao-sql")
    versao = _criar_versao(codigo="transicao-sql")
    assinatura = _criar_assinatura(organizacao, versao)
    with organizacao_atual_privilegiada(organizacao.pk):
        alteracao = AlteracaoAssinatura.objects.create(
            **_dados_alteracao(
                organizacao,
                assinatura,
                status=StatusAlteracaoAssinatura.AGUARDANDO_GATEWAY,
            )
        )

        with pytest.raises(DatabaseError, match="(?i)transição inválida"), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "UPDATE alteracao_assinatura SET status = %s WHERE id = %s",
                [StatusAlteracaoAssinatura.SOLICITADA, alteracao.pk],
            )


def test_banco_recusa_alteracao_cuja_assinatura_pertence_a_outro_tenant():
    org_a = Organizacao.objects.create(nome="Tenant A", slug="coerencia-tenant-a")
    org_b = Organizacao.objects.create(nome="Tenant B", slug="coerencia-tenant-b")
    versao = _criar_versao(codigo="coerencia-tenant")
    assinatura_b = _criar_assinatura(org_b, versao)

    with organizacao_atual_privilegiada(org_a.pk), pytest.raises(DatabaseError, match="organização da assinatura"), transaction.atomic():
        AlteracaoAssinatura.objects.create(**_dados_alteracao(org_a, assinatura_b))
