"""Fatos de domínio de vínculos e ocupação de seats."""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.utils import timezone

import pytest

from apps.api.core.errors import APIError
from apps.organizacoes.memberships import OcupacaoSeats, Vinculos
from apps.organizacoes.models import Convite, Organizacao, Papel, Vinculo
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db


def test_organizacao_persiste_email_faturamento_e_agendamento_de_encerramento():
    solicitada_em = timezone.now()
    agendada_para = solicitada_em + timedelta(days=30)

    organizacao = Organizacao.objects.create(
        nome="Acme",
        slug="acme-ciclo",
        email_faturamento="financeiro@example.com",
        encerramento_solicitado_em=solicitada_em,
        encerramento_agendado_para=agendada_para,
    )

    organizacao.refresh_from_db()
    assert organizacao.email_faturamento == "financeiro@example.com"
    assert organizacao.encerramento_solicitado_em == solicitada_em
    assert organizacao.encerramento_agendado_para == agendada_para


def test_agendamento_de_encerramento_exige_solicitacao():
    with pytest.raises(IntegrityError):
        Organizacao.objects.create(
            nome="Acme",
            slug="acme-agendamento-invalido",
            encerramento_agendado_para=timezone.now() + timedelta(days=30),
        )


def test_calcular_ocupacao_conta_vinculos_ativos_e_suspensos_e_reserva_somente_convite_vivo(
    django_assert_num_queries,
):
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme-ocupacao")
    Vinculo.objects.create(organizacao=organizacao, usuario=criar_usuario(email="ativo@example.com"), papel=Papel.MEMBRO)
    Vinculo.objects.create(
        organizacao=organizacao,
        usuario=criar_usuario(email="suspenso@example.com"),
        papel=Papel.MEMBRO,
        is_active=False,
    )
    Vinculo.objects.create(organizacao=organizacao, usuario=criar_usuario(email="isento@example.com"), papel=Papel.PROPRIETARIO)
    removido = Vinculo.objects.create(organizacao=organizacao, usuario=criar_usuario(email="removido@example.com"), papel=Papel.MEMBRO)
    removido.delete()

    Convite.objects.create(organizacao=organizacao, email="pendente@example.com", expira_em=timezone.now() + timedelta(days=1))
    Convite.objects.create(
        organizacao=organizacao,
        email="inativo@example.com",
        expira_em=timezone.now() + timedelta(days=1),
        is_active=False,
    )
    Convite.objects.create(organizacao=organizacao, email="expirado@example.com", expira_em=timezone.now() - timedelta(seconds=1))
    Convite.objects.create(
        organizacao=organizacao,
        email="aceito@example.com",
        expira_em=timezone.now() + timedelta(days=1),
        aceito_em=timezone.now(),
    )
    cancelado = Convite.objects.create(organizacao=organizacao, email="cancelado@example.com", expira_em=timezone.now() + timedelta(days=1))
    cancelado.delete()

    with django_assert_num_queries(1):
        ocupacao = Vinculos.calcular_ocupacao(organizacao, frozenset({Papel.PROPRIETARIO}))

    assert ocupacao == OcupacaoSeats(consumidos=2, reservados=1)


def test_criar_proprietario_cria_vinculo_pelo_servico():
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme-proprietario")
    usuario = criar_usuario()

    vinculo = Vinculos.criar_proprietario(organizacao, usuario)

    assert vinculo.organizacao == organizacao
    assert vinculo.usuario == usuario
    assert vinculo.papel == Papel.PROPRIETARIO


def test_aceitar_convite_cria_vinculo_e_consumo_sem_manter_reserva():
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme-aceite")
    usuario = criar_usuario(email="aceite@example.com", email_verificado_em=timezone.now())
    convite = Convite.objects.create(organizacao=organizacao, email=usuario.email, papel=Papel.GESTOR)

    vinculo = Vinculos.aceitar_convite(convite, usuario)

    convite.refresh_from_db()
    assert vinculo.papel == Papel.GESTOR
    assert convite.aceito_em is not None
    assert Vinculos.calcular_ocupacao(organizacao, frozenset()) == OcupacaoSeats(consumidos=1, reservados=0)


def test_aceitar_convite_revalida_email_sob_lock():
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme-aceite-email-lock")
    usuario = criar_usuario(email="destinatario-original@example.com", email_verificado_em=timezone.now())
    convite = Convite.objects.create(
        organizacao=organizacao,
        email="destinatario-atual@example.com",
        papel=Papel.MEMBRO,
    )

    with pytest.raises(APIError) as erro:
        Vinculos.aceitar_convite(convite, usuario)

    assert erro.value.code == "organizations.invitation_email_mismatch"
    assert Vinculo.objects.filter(organizacao=organizacao, usuario=usuario).exists() is False


@pytest.mark.parametrize(
    "estado",
    [
        {"is_active": False},
        {"is_deleted": True},
        {"exclusao_agendada_para": timezone.now() + timedelta(days=1)},
    ],
)
def test_aceitar_convite_revalida_conta_elegivel_sob_lock(estado):
    organizacao = Organizacao.objects.create(nome="Acme", slug=f"acme-aceite-conta-{next(iter(estado))}")
    usuario = criar_usuario(
        email=f"{next(iter(estado))}@example.com",
        email_verificado_em=timezone.now(),
        **estado,
    )
    convite = Convite.objects.create(organizacao=organizacao, email=usuario.email, papel=Papel.MEMBRO)

    with pytest.raises(APIError) as erro:
        Vinculos.aceitar_convite(convite, usuario)

    assert erro.value.code == "auth.user_inactive"
    assert Vinculo.objects.filter(organizacao=organizacao, usuario=usuario).exists() is False


def test_aceitar_convite_pelo_model_preserva_fachada_publica():
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme-fachada-aceite")
    usuario = criar_usuario(email="fachada@example.com", email_verificado_em=timezone.now())
    convite = Convite.objects.create(organizacao=organizacao, email=usuario.email, papel=Papel.GESTOR)

    vinculo = convite.aceitar(usuario)

    assert vinculo.organizacao == organizacao
    assert vinculo.usuario == usuario
    assert vinculo.papel == Papel.GESTOR
    assert convite.aceito_em is not None


def test_aceitar_convite_eleva_papel_de_vinculo_existente():
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme-eleva-papel")
    usuario = criar_usuario(email="eleva@example.com", email_verificado_em=timezone.now())
    Vinculo.objects.create(organizacao=organizacao, usuario=usuario, papel=Papel.MEMBRO)
    convite = Convite.objects.create(organizacao=organizacao, email=usuario.email, papel=Papel.GESTOR)

    vinculo = Vinculos.aceitar_convite(convite, usuario)

    convite.refresh_from_db()
    assert vinculo.papel == Papel.GESTOR
    assert convite.aceito_em is not None


def test_aceitar_convite_nao_rebaixa_papel_de_vinculo_existente():
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme-nao-rebaixa-papel")
    usuario = criar_usuario(email="nao-rebaixa@example.com", email_verificado_em=timezone.now())
    Vinculo.objects.create(organizacao=organizacao, usuario=usuario, papel=Papel.ADMINISTRADOR)
    convite = Convite.objects.create(organizacao=organizacao, email=usuario.email, papel=Papel.MEMBRO)

    vinculo = Vinculos.aceitar_convite(convite, usuario)

    convite.refresh_from_db()
    assert vinculo.papel == Papel.ADMINISTRADOR
    assert convite.aceito_em is not None


def test_aceitar_convite_expirado_e_recusado_pelo_servico():
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme-convite-expirado")
    convite = Convite.objects.create(organizacao=organizacao, email="expirado@example.com", expira_em=timezone.now() - timedelta(seconds=1))

    with pytest.raises(ValidationError):
        Vinculos.aceitar_convite(
            convite,
            criar_usuario(email="expirado@example.com", email_verificado_em=timezone.now()),
        )
