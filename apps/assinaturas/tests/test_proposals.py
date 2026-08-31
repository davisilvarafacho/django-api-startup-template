"""Casos de uso tipados do ciclo de propostas enterprise."""

from datetime import timedelta

from django.utils import timezone

import pyotp
import pytest

from apps.api.autenticacao.mfa import confirm_enrollment, start_enrollment
from apps.api.autenticacao.models import MFAFactor, MFAFactorType
from apps.assinaturas.features import CATALOGO_RECURSOS, ValoresRecursos
from apps.assinaturas.models import (
    AssinaturaOrganizacao,
    ModoAtivacaoProposta,
    Periodicidade,
    Plano,
    StatusAssinatura,
    StatusFinanceiro,
    StatusPropostaComercial,
    VersaoPlano,
)
from apps.assinaturas.proposals import (
    ConflitoPropostaComercial,
    CriacaoPropostaComercial,
    EdicaoPropostaComercial,
    PreparacaoCheckoutProposta,
    Propostas,
)
from apps.assinaturas.subscriptions import Assinaturas, CriacaoAssinatura, OrigemVersaoPlano, TermosAssinatura
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Organizacao, Papel, Vinculo
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db


def _versao_referencia(codigo: str = "enterprise-referencia") -> VersaoPlano:
    plano = Plano.objects.create(codigo=codigo, nome=codigo.title(), descricao="", visivel=False)
    return VersaoPlano.objects.create(plano=plano, numero=1, atual=False, recursos={})


def _termos(*, valor_base=120_000, seats=25) -> TermosAssinatura:
    return TermosAssinatura(
        periodicidade=Periodicidade.ANUAL,
        moeda="BRL",
        valor_base_centavos=valor_base,
        valor_seat_centavos=5_000,
        seats_inclusos=10,
        seats_contratados=seats,
        expansao_automatica_seats=False,
        recursos=ValoresRecursos(CATALOGO_RECURSOS, {"quantidade_projetos": 100}),
        carencia_pagamento_dias=15,
        carencia_excesso_seats_dias=10,
    )


def _criar_rascunho(organizacao: Organizacao, *, modo=ModoAtivacaoProposta.PAGAMENTO, agora=None):
    agora = agora or timezone.now()
    return Propostas.criar(
        CriacaoPropostaComercial(
            organizacao=organizacao,
            versao_plano_referencia=_versao_referencia(f"enterprise-{organizacao.pk}"),
            modo_ativacao=modo,
            termos=_termos(),
            valida_ate=agora + timedelta(days=30),
        )
    )


def _enviar(proposta, *, agora=None):
    agora = agora or timezone.now()
    return Propostas.enviar(proposta, revisao_esperada=proposta.revisao, agora=agora)


def _contrato_atual(organizacao: Organizacao):
    versao = _versao_referencia(f"contrato-atual-{organizacao.pk}")
    with organizacao_atual_privilegiada(organizacao.pk):
        return Assinaturas.criar(
            CriacaoAssinatura(
                organizacao=organizacao,
                origem=OrigemVersaoPlano(versao),
                termos=_termos(valor_base=10_000, seats=10),
                status=StatusAssinatura.ATIVA,
                status_financeiro=StatusFinanceiro.REGULAR,
                politica_trial=None,
                trial_termina_em=None,
                chave_idempotencia=f"contrato-atual-{organizacao.pk}",
            )
        )


def test_criar_editar_e_enviar_preservam_revisao_otimista_e_snapshot():
    organizacao = Organizacao.objects.create(nome="Enterprise", slug="proposta-ciclo")
    agora = timezone.now()
    proposta = _criar_rascunho(organizacao, agora=agora)

    proposta = Propostas.editar_rascunho(
        EdicaoPropostaComercial(
            proposta=proposta,
            revisao_esperada=1,
            termos=_termos(valor_base=130_000, seats=30),
            valida_ate=agora + timedelta(days=45),
        )
    )
    proposta = Propostas.enviar(proposta, revisao_esperada=2, agora=agora)

    assert proposta.status == StatusPropostaComercial.ENVIADA
    assert proposta.revisao == 3
    assert proposta.valor_base_centavos == 130_000
    assert proposta.seats_contratados == 30
    assert proposta.recursos == {"papeis_isentos_seat": [], "quantidade_projetos": 100}

    with pytest.raises(ConflitoPropostaComercial, match="rascunho"):
        Propostas.editar_rascunho(
            EdicaoPropostaComercial(
                proposta=proposta,
                revisao_esperada=3,
                termos=_termos(),
                valida_ate=agora + timedelta(days=60),
            )
        )


def test_revisao_esperada_e_validade_sao_revalidadas_sob_transicao():
    organizacao = Organizacao.objects.create(nome="Revisao", slug="proposta-revisao")
    agora = timezone.now()
    proposta = _criar_rascunho(organizacao, agora=agora)

    with pytest.raises(ConflitoPropostaComercial, match="revisão"):
        Propostas.enviar(proposta, revisao_esperada=99, agora=agora)

    proposta.valida_ate = agora
    proposta.save(update_fields=["valida_ate", "last_modified_at"])
    with pytest.raises(ConflitoPropostaComercial, match="validade"):
        Propostas.enviar(proposta, revisao_esperada=1, agora=agora)


def test_somente_proprietario_aceita_e_modo_pagamento_apenas_prepara_checkout():
    organizacao = Organizacao.objects.create(nome="Pagamento", slug="proposta-pagamento")
    proprietario = criar_usuario(email="owner-proposta@example.com")
    administrador = criar_usuario(email="admin-proposta@example.com")
    Vinculo.objects.create(organizacao=organizacao, usuario=proprietario, papel=Papel.PROPRIETARIO)
    Vinculo.objects.create(organizacao=organizacao, usuario=administrador, papel=Papel.ADMINISTRADOR)
    proposta = _enviar(_criar_rascunho(organizacao))

    with pytest.raises(ConflitoPropostaComercial, match="proprietário"):
        Propostas.aceitar(proposta, ator=administrador, revisao_esperada=proposta.revisao)

    resultado = Propostas.aceitar(proposta, ator=proprietario, revisao_esperada=proposta.revisao)

    assert resultado.proposta.status == StatusPropostaComercial.ACEITA
    assert isinstance(resultado.preparacao_checkout, PreparacaoCheckoutProposta)
    assert resultado.preparacao_checkout.proposta_id == proposta.pk
    assert resultado.preparacao_checkout.revisao == resultado.proposta.revisao
    assert not hasattr(resultado.preparacao_checkout, "checkout")


def test_repetir_aceite_pago_e_retry_idempotente_enquanto_proposta_e_valida():
    organizacao = Organizacao.objects.create(nome="Retry", slug="proposta-retry")
    proprietario = criar_usuario(email="owner-retry-proposta@example.com")
    Vinculo.objects.create(organizacao=organizacao, usuario=proprietario, papel=Papel.PROPRIETARIO)
    proposta = _enviar(_criar_rascunho(organizacao))
    revisao_enviada = proposta.revisao

    primeiro = Propostas.aceitar(proposta, ator=proprietario, revisao_esperada=revisao_enviada)
    segundo = Propostas.aceitar(proposta, ator=proprietario, revisao_esperada=revisao_enviada)
    terceiro = Propostas.aceitar(proposta, ator=proprietario, revisao_esperada=primeiro.proposta.revisao)

    assert primeiro.proposta.pk == segundo.proposta.pk == terceiro.proposta.pk
    assert primeiro.proposta.revisao == segundo.proposta.revisao == terceiro.proposta.revisao
    assert primeiro.preparacao_checkout == segundo.preparacao_checkout == terceiro.preparacao_checkout


def test_recusar_cancelar_e_expirar_usam_transicoes_nominais():
    agora = timezone.now()
    proprietario = criar_usuario(email="owner-transicoes@example.com")
    operador = criar_usuario(email="operador-transicoes@example.com", is_staff=True, is_superuser=True)

    org_recusa = Organizacao.objects.create(nome="Recusa", slug="proposta-recusa")
    Vinculo.objects.create(organizacao=org_recusa, usuario=proprietario, papel=Papel.PROPRIETARIO)
    recusada = Propostas.recusar(
        _enviar(_criar_rascunho(org_recusa, agora=agora), agora=agora),
        ator=proprietario,
        revisao_esperada=2,
        agora=agora,
    )
    assert recusada.status == StatusPropostaComercial.RECUSADA
    assert recusada.recusada_por == proprietario

    org_cancela = Organizacao.objects.create(nome="Cancela", slug="proposta-cancela")
    cancelada = Propostas.cancelar(
        _criar_rascunho(org_cancela, agora=agora),
        ator=operador,
        revisao_esperada=1,
        agora=agora,
    )
    assert cancelada.status == StatusPropostaComercial.CANCELADA
    assert cancelada.cancelada_por == operador

    org_expira = Organizacao.objects.create(nome="Expira", slug="proposta-expira")
    proposta_expira = _enviar(_criar_rascunho(org_expira, agora=agora), agora=agora)
    with pytest.raises(ConflitoPropostaComercial, match="revisão"):
        Propostas.expirar(
            proposta_expira,
            revisao_esperada=99,
            agora=proposta_expira.valida_ate + timedelta(seconds=1),
        )
    expirada = Propostas.expirar(
        proposta_expira,
        revisao_esperada=proposta_expira.revisao,
        agora=proposta_expira.valida_ate + timedelta(seconds=1),
    )
    assert expirada.status == StatusPropostaComercial.EXPIRADA


def test_ativacao_contratual_exige_operador_autorizado_mfa_e_justificativa():
    agora = timezone.now()
    organizacao = Organizacao.objects.create(nome="Contrato", slug="proposta-contratual-auth")
    proprietario = criar_usuario(email="owner-contratual-auth@example.com")
    operador_sem_permissao = criar_usuario(email="staff-sem-permissao@example.com", is_staff=True)
    operador = criar_usuario(email="staff-com-permissao@example.com", is_staff=True, is_superuser=True)
    Vinculo.objects.create(organizacao=organizacao, usuario=proprietario, papel=Papel.PROPRIETARIO)
    proposta = _enviar(_criar_rascunho(organizacao, modo=ModoAtivacaoProposta.CONTRATUAL, agora=agora), agora=agora)
    proposta = Propostas.aceitar(proposta, ator=proprietario, revisao_esperada=2, agora=agora).proposta

    with pytest.raises(ConflitoPropostaComercial, match="operador autorizado"):
        Propostas.ativar_contratual(
            proposta,
            operador=operador_sem_permissao,
            revisao_esperada=3,
            codigo_mfa="000000",
            justificativa="Contrato assinado",
            agora=agora,
        )

    with pytest.raises(ConflitoPropostaComercial, match="MFA"):
        Propostas.ativar_contratual(
            proposta,
            operador=operador,
            revisao_esperada=3,
            codigo_mfa="000000",
            justificativa="Contrato assinado",
            agora=agora,
        )

    enrollment = start_enrollment(operador, MFAFactorType.TOTP)
    codigo = pyotp.TOTP(enrollment.plain_secret).now()
    confirm_enrollment(operador, MFAFactorType.TOTP, codigo)
    MFAFactor.objects.filter(pk=enrollment.factor.pk).update(totp_last_counter=None)
    with pytest.raises(ConflitoPropostaComercial, match="justificativa"):
        Propostas.ativar_contratual(
            proposta,
            operador=operador,
            revisao_esperada=3,
            codigo_mfa=pyotp.TOTP(enrollment.plain_secret).now(),
            justificativa="",
            agora=agora,
        )


def test_ativacao_contratual_substitui_contrato_em_uma_transacao_e_audita_operador():
    agora = timezone.now()
    organizacao = Organizacao.objects.create(nome="Ativacao", slug="proposta-ativacao")
    proprietario = criar_usuario(email="owner-ativacao@example.com")
    operador = criar_usuario(email="staff-ativacao@example.com", is_staff=True, is_superuser=True)
    Vinculo.objects.create(organizacao=organizacao, usuario=proprietario, papel=Papel.PROPRIETARIO)
    anterior = _contrato_atual(organizacao)
    proposta = _enviar(_criar_rascunho(organizacao, modo=ModoAtivacaoProposta.CONTRATUAL, agora=agora), agora=agora)
    proposta = Propostas.aceitar(proposta, ator=proprietario, revisao_esperada=2, agora=agora).proposta
    enrollment = start_enrollment(operador, MFAFactorType.TOTP)
    confirm_enrollment(operador, MFAFactorType.TOTP, pyotp.TOTP(enrollment.plain_secret).now())
    MFAFactor.objects.filter(pk=enrollment.factor.pk).update(totp_last_counter=None)

    nova = Propostas.ativar_contratual(
        proposta,
        operador=operador,
        revisao_esperada=3,
        codigo_mfa=pyotp.TOTP(enrollment.plain_secret).now(),
        justificativa="Contrato enterprise assinado",
        agora=agora,
    )

    with organizacao_atual_privilegiada(organizacao.pk):
        anterior.refresh_from_db()
        proposta.refresh_from_db()
    assert anterior.status == StatusAssinatura.ENCERRADA
    assert anterior.motivo_encerramento == "proposal_replaced"
    assert nova.status == StatusAssinatura.ATIVA
    assert nova.status_financeiro == StatusFinanceiro.ISENTO
    assert nova.versao_plano_id is None
    assert nova.proposta_comercial == proposta
    assert nova.valor_base_centavos == proposta.valor_base_centavos
    assert proposta.status == StatusPropostaComercial.ATIVADA
    assert proposta.ativada_por == operador
    assert proposta.justificativa_ativacao == "Contrato enterprise assinado"


def test_aceite_pago_nao_concede_acesso_e_confirmacao_substitui_contrato():
    agora = timezone.now()
    organizacao = Organizacao.objects.create(nome="Pagamento confirmado", slug="proposta-pagamento-confirmado")
    proprietario = criar_usuario(email="owner-pagamento-confirmado@example.com")
    Vinculo.objects.create(organizacao=organizacao, usuario=proprietario, papel=Papel.PROPRIETARIO)
    anterior = _contrato_atual(organizacao)
    proposta = _enviar(_criar_rascunho(organizacao, agora=agora), agora=agora)

    aceite = Propostas.aceitar(proposta, ator=proprietario, revisao_esperada=2, agora=agora)
    with organizacao_atual_privilegiada(organizacao.pk):
        assert AssinaturaOrganizacao.objects.get(status=StatusAssinatura.ATIVA).pk == anterior.pk

    nova = Propostas.ativar_pagamento_confirmado(aceite.proposta, revisao_esperada=3, agora=agora)

    assert nova.proposta_comercial_id == proposta.pk
    assert nova.status == StatusAssinatura.ATIVA
    assert nova.status_financeiro == StatusFinanceiro.REGULAR


def test_falha_ao_criar_novo_ciclo_reverte_encerramento_e_ativacao(monkeypatch):
    agora = timezone.now()
    organizacao = Organizacao.objects.create(nome="Rollback", slug="proposta-rollback")
    proprietario = criar_usuario(email="owner-proposta-rollback@example.com")
    Vinculo.objects.create(organizacao=organizacao, usuario=proprietario, papel=Papel.PROPRIETARIO)
    anterior = _contrato_atual(organizacao)
    proposta = _enviar(_criar_rascunho(organizacao, agora=agora), agora=agora)
    proposta = Propostas.aceitar(proposta, ator=proprietario, revisao_esperada=2, agora=agora).proposta

    def falhar(*args, **kwargs):
        raise RuntimeError("falha ao criar ciclo")

    monkeypatch.setattr(Assinaturas, "criar", falhar)
    with pytest.raises(RuntimeError, match="falha ao criar ciclo"):
        Propostas.ativar_pagamento_confirmado(proposta, revisao_esperada=3, agora=agora)

    with organizacao_atual_privilegiada(organizacao.pk):
        anterior.refresh_from_db()
        proposta.refresh_from_db()
    assert anterior.status == StatusAssinatura.ATIVA
    assert proposta.status == StatusPropostaComercial.ACEITA
