"""Transicoes independentes de carencia e encerramento do trial."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

from django.db import DatabaseError, close_old_connections, connection, connections, transaction
from django.utils import timezone

import pytest

from apps.api.core.errors import APIError
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
from apps.organizacoes.memberships import Vinculos
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


def _trial(*, slug: str, agora=None):
    versao, preco = _catalogo("profissional")
    organizacao = Organizacao.objects.create(nome=slug, slug=slug)
    agora = agora or timezone.now() - timedelta(days=15)
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


def _criar_membros(organizacao: Organizacao, *, prefixo: str, quantidade: int) -> list[Vinculo]:
    return [
        Vinculo.objects.create(
            organizacao=organizacao,
            usuario=criar_usuario(email=f"{prefixo}-{indice}@example.com"),
            papel=Papel.MEMBRO,
        )
        for indice in range(quantidade)
    ]


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
    _criar_membros(organizacao, prefixo="pagamento-regulariza", quantidade=6)
    agora = timezone.now()
    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura = Assinaturas.registrar_falha_renovacao(assinatura, agora=agora)
        assinatura = Assinaturas.reconciliar_carencia_seats(assinatura, agora=agora)
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
    vinculos = _criar_membros(organizacao, prefixo="reconciliar-seats", quantidade=3)
    agora = timezone.now()
    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura = Assinaturas.registrar_falha_renovacao(assinatura, agora=agora)
        com_excesso = Assinaturas.reconciliar_carencia_seats(assinatura, agora=agora + timedelta(hours=1))
        repetida = Assinaturas.reconciliar_carencia_seats(com_excesso, agora=agora + timedelta(days=2))
        vinculos[-1].delete()
        regularizada = Assinaturas.reconciliar_carencia_seats(repetida, agora=agora + timedelta(days=3))

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
            agora=agora,
        )
        repetido = Assinaturas.encerrar_trial(
            assinatura,
            resultado=FallbackTrialGratuito(MotivoFallbackTrial.PRIMEIRA_COBRANCA_FALHOU),
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


def test_encerrar_trial_calcula_ocupacao_real_dentro_da_secao_critica():
    organizacao, assinatura = _trial(slug="trial-ocupacao-bloqueada")
    for indice in range(2):
        Vinculo.objects.create(
            organizacao=organizacao,
            usuario=criar_usuario(email=f"trial-ocupacao-bloqueada-{indice}@example.com"),
            papel=Papel.MEMBRO,
        )
    agora = assinatura.trial_termina_em + timedelta(seconds=1)

    with organizacao_atual_privilegiada(organizacao.pk):
        resultado = Assinaturas.encerrar_trial(
            assinatura,
            resultado=FallbackTrialGratuito(MotivoFallbackTrial.TRIAL_LOCAL_ENCERRADO),
            agora=agora,
        )

    assert isinstance(resultado, TrialConvertidoParaGratuito)
    assert resultado.assinatura.carencia_excesso_seats_iniciada_em == agora


def test_reconciliar_carencia_calcula_ocupacao_real_dentro_da_secao_critica():
    organizacao, assinatura = _assinatura_ativa(slug="reconcile-ocupacao-bloqueada", seats=1)
    for indice in range(2):
        Vinculo.objects.create(
            organizacao=organizacao,
            usuario=criar_usuario(email=f"reconcile-ocupacao-bloqueada-{indice}@example.com"),
            papel=Papel.MEMBRO,
        )
    agora = timezone.now()

    with organizacao_atual_privilegiada(organizacao.pk):
        reconciliada = Assinaturas.reconciliar_carencia_seats(assinatura, agora=agora)

    assert reconciliada.carencia_excesso_seats_iniciada_em == agora


def test_conversao_paga_exige_seats_reais_e_e_idempotente():
    organizacao, assinatura = _trial(slug="trial-pago")
    _criar_membros(organizacao, prefixo="trial-pago", quantidade=3)
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
            agora=agora,
        )
        repetido = Assinaturas.encerrar_trial(
            assinatura,
            resultado=pagamento,
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


@pytest.mark.django_db(transaction=True)
def test_aceite_de_convite_e_fallback_serializam_ocupacao_real():
    organizacao, assinatura = _trial(slug="trial-convite-concorrente")
    _criar_membros(organizacao, prefixo="trial-convite-concorrente-existente", quantidade=1)
    convidado = criar_usuario(
        email="trial-convite-concorrente-convidado@example.com",
        email_verificado_em=timezone.now(),
    )
    convite = Vinculos.criar_convite(
        organizacao=organizacao,
        email=convidado.email,
        papel=Papel.MEMBRO,
        convidado_por=None,
    )
    agora = assinatura.trial_termina_em + timedelta(seconds=1)
    barreira = Barrier(2)

    def aceitar():
        close_old_connections()
        try:
            barreira.wait(timeout=5)
            try:
                return Vinculos.aceitar_convite(convite, convidado).pk
            except APIError as exc:
                return exc
        finally:
            connections.close_all()

    def aplicar_fallback():
        close_old_connections()
        try:
            barreira.wait(timeout=5)
            with organizacao_atual_privilegiada(organizacao.pk):
                return Assinaturas.encerrar_trial(
                    assinatura,
                    resultado=FallbackTrialGratuito(MotivoFallbackTrial.TRIAL_LOCAL_ENCERRADO),
                    agora=agora,
                )
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        aceite_futuro = executor.submit(aceitar)
        fallback_futuro = executor.submit(aplicar_fallback)
        aceite = aceite_futuro.result(timeout=10)
        fallback = fallback_futuro.result(timeout=10)

    with organizacao_atual_privilegiada(organizacao.pk):
        fallback.assinatura.refresh_from_db()
    if isinstance(aceite, APIError):
        assert fallback.assinatura.carencia_excesso_seats_iniciada_em is None
    else:
        assert fallback.assinatura.carencia_excesso_seats_iniciada_em == agora


@pytest.mark.django_db(transaction=True)
def test_encerramento_e_reconcile_serializam_sem_reabrir_contrato():
    organizacao, assinatura = _assinatura_ativa(slug="encerramento-reconcile-concorrente", seats=1)
    _criar_membros(organizacao, prefixo="encerramento-reconcile-concorrente", quantidade=2)
    agora = timezone.now()
    barreira = Barrier(2)

    def encerrar():
        close_old_connections()
        try:
            barreira.wait(timeout=5)
            with organizacao_atual_privilegiada(organizacao.pk):
                Assinaturas.encerrar(organizacao, encerrada_em=agora)
        finally:
            connections.close_all()

    def reconciliar():
        close_old_connections()
        try:
            barreira.wait(timeout=5)
            with organizacao_atual_privilegiada(organizacao.pk):
                return Assinaturas.reconciliar_carencia_seats(assinatura, agora=agora).pk
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        encerramento_futuro = executor.submit(encerrar)
        reconcile_futuro = executor.submit(reconciliar)
        encerramento_futuro.result(timeout=10)
        assert reconcile_futuro.result(timeout=10) == assinatura.pk

    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura.refresh_from_db()
    assert assinatura.status == StatusAssinatura.ENCERRADA
    assert assinatura.encerrada_em == agora


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


@pytest.mark.parametrize("escrita", ["save", "update", "bulk_update"])
def test_orm_generico_nao_pode_alterar_termos_contratuais(escrita):
    organizacao, assinatura = _assinatura_ativa(slug=f"termos-orm-{escrita}")

    def escrever_genericamente():
        if escrita == "save":
            assinatura.seats_contratados += 1
            assinatura.revisao += 1
            assinatura.save(update_fields=["seats_contratados", "revisao", "last_modified_at"])
        elif escrita == "update":
            AssinaturaOrganizacao.objects.filter(pk=assinatura.pk).update(
                seats_contratados=assinatura.seats_contratados + 1,
                revisao=assinatura.revisao + 1,
            )
        else:
            assinatura.seats_contratados += 1
            assinatura.revisao += 1
            AssinaturaOrganizacao.objects.bulk_update([assinatura], ["seats_contratados", "revisao"])

    with organizacao_atual_privilegiada(organizacao.pk), pytest.raises(ValueError, match="transição nominal"):
        escrever_genericamente()


def test_sql_nao_pode_converter_trial_pago_sem_transicao_nominal_completa():
    organizacao, assinatura = _trial(slug="trial-pago-sql-direto")

    with organizacao_atual_privilegiada(organizacao.pk), pytest.raises(DatabaseError, match="(?i)trial pago"), transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE assinatura_organizacao
                   SET status = %s,
                       status_financeiro = %s,
                       revisao = revisao + 1
                 WHERE id = %s
                """,
                [StatusAssinatura.ATIVA, StatusFinanceiro.REGULAR, assinatura.pk],
            )


def test_sql_exige_incremento_unitario_para_mudar_termos_contratuais():
    organizacao, assinatura = _assinatura_ativa(slug="termos-sql-revisao")

    with organizacao_atual_privilegiada(organizacao.pk), pytest.raises(DatabaseError, match="(?i)incremento unitario"), transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE assinatura_organizacao
                   SET seats_contratados = seats_contratados + 1,
                       revisao = revisao + 2
                 WHERE id = %s
                """,
                [assinatura.pk],
            )


@pytest.mark.parametrize("incremento", [1, 7])
def test_sql_nao_pode_alterar_revisao_isoladamente(incremento):
    organizacao, assinatura = _assinatura_ativa(slug=f"revisao-isolada-{incremento}")

    with organizacao_atual_privilegiada(organizacao.pk), pytest.raises(DatabaseError, match="(?i)revisao.*isoladamente"), transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE assinatura_organizacao SET revisao = revisao + %s WHERE id = %s",
                [incremento, assinatura.pk],
            )


@pytest.mark.parametrize("usar_fields", [False, True])
def test_bulk_create_com_conflito_nao_pode_encerrar_assinatura_genericamente(usar_fields):
    organizacao, assinatura = _assinatura_ativa(slug=f"upsert-contratual-fields-{usar_fields}")
    assinatura.status = StatusAssinatura.ENCERRADA
    assinatura.encerrada_em = timezone.now()
    assinatura.motivo_encerramento = "upsert_generico"
    assinatura.revisao += 1
    nomes = ["status", "encerrada_em", "motivo_encerramento", "revisao"]
    update_fields = [AssinaturaOrganizacao._meta.get_field(nome) for nome in nomes] if usar_fields else nomes

    with organizacao_atual_privilegiada(organizacao.pk), pytest.raises(ValueError, match="(?i)upsert.*operacionais"):
        AssinaturaOrganizacao.objects.bulk_create(
            [assinatura],
            update_conflicts=True,
            update_fields=update_fields,
            unique_fields=["id"],
        )


@pytest.mark.parametrize("manager_name", ["objects", "all_objects", "ativos"])
@pytest.mark.parametrize("como_field", [False, True])
def test_bulk_create_com_conflito_permite_campo_operacional(manager_name, como_field):
    organizacao, assinatura = _assinatura_ativa(slug=f"upsert-operacional-{manager_name}-{como_field}")
    assinatura.is_active = False
    manager = getattr(AssinaturaOrganizacao, manager_name)
    update_fields = [AssinaturaOrganizacao._meta.get_field("is_active")] if como_field else ["is_active"]

    with organizacao_atual_privilegiada(organizacao.pk):
        manager.bulk_create(
            [assinatura],
            update_conflicts=True,
            update_fields=update_fields,
            unique_fields=["id"],
        )
        persistida = AssinaturaOrganizacao.all_objects.get(pk=assinatura.pk)

    assert persistida.is_active is False
    assert persistida.revisao == 1


@pytest.mark.parametrize("manager_name", ["objects", "all_objects", "ativos"])
def test_bulk_create_com_conflito_nao_pode_alterar_chave_idempotencia(manager_name, django_assert_num_queries):
    organizacao, assinatura = _assinatura_ativa(slug=f"upsert-identidade-{manager_name}")
    assinatura.chave_idempotencia = f"identidade-alterada-{manager_name}"
    manager = getattr(AssinaturaOrganizacao, manager_name)

    with organizacao_atual_privilegiada(organizacao.pk):
        with django_assert_num_queries(0), pytest.raises(ValueError, match="(?i)upsert.*operacionais"):
            manager.bulk_create(
                [assinatura],
                update_conflicts=True,
                update_fields=["chave_idempotencia"],
                unique_fields=["id"],
            )


@pytest.mark.django_db(databases={"default", "logging"})
@pytest.mark.parametrize("alias", ["default", "logging"])
@pytest.mark.parametrize("manager_name", ["objects", "all_objects", "ativos"])
@pytest.mark.parametrize(
    "campo",
    [
        "id",
        "pk",
        "organizacao",
        "organizacao_id",
        "chave_idempotencia",
        "created_at",
        "created_by",
        "created_by_id",
        "last_modified_at",
        "is_deleted",
        "revisao",
        "status",
        AssinaturaOrganizacao._meta.get_field("organizacao"),
    ],
)
def test_bulk_create_com_conflito_rejeita_campos_nao_operacionais_antes_do_sql(alias, manager_name, campo, django_assert_num_queries):
    manager = getattr(AssinaturaOrganizacao, manager_name).using(alias)

    with django_assert_num_queries(0, using=alias), pytest.raises(ValueError, match="(?i)upsert.*operacionais"):
        manager.bulk_create(
            [AssinaturaOrganizacao(recursos={})],
            update_conflicts=True,
            update_fields=[campo],
            unique_fields=["id"],
        )


def test_sql_nao_pode_reaproveitar_alteracao_compativel_para_mascarar_preco():
    organizacao, assinatura = _assinatura_ativa(slug="termos-sql-snapshot-completo")
    snapshot_anterior = Assinaturas._snapshot_assinatura(assinatura)
    snapshot_pretendido = {
        **snapshot_anterior,
        "revisao": assinatura.revisao + 1,
        "seats_contratados": assinatura.seats_contratados + 1,
    }
    with organizacao_atual_privilegiada(organizacao.pk):
        AlteracaoAssinatura.objects.create(
            organizacao=organizacao,
            assinatura=assinatura,
            tipo=TipoAlteracaoAssinatura.AUMENTO_SEATS,
            momento_aplicacao=10,
            status=10,
            revisao_esperada=assinatura.revisao,
            chave_idempotencia="termos-sql-snapshot-completo",
            pedido={"seats_contratados": assinatura.seats_contratados + 1},
            snapshot_anterior=snapshot_anterior,
            snapshot_pretendido=snapshot_pretendido,
        )
        with pytest.raises(DatabaseError, match="(?i)AlteracaoAssinatura compativel"), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE assinatura_organizacao
                   SET seats_contratados = seats_contratados + 1,
                       valor_base_centavos = valor_base_centavos + 1,
                       revisao = revisao + 1
                 WHERE id = %s
                """,
                [assinatura.pk],
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
    _criar_membros(organizacao, prefixo="pagamento-seats-concorrentes", quantidade=3)
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
