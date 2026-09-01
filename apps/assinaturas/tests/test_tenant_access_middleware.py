"""Integração real da política comercial no contexto tenant da request."""

import json
from dataclasses import FrozenInstanceError
from datetime import timedelta

from django.db import connection
from django.http import HttpResponse
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from rest_framework.test import APIRequestFactory

import pytest

from apps.assinaturas.access_policies import MotivoRestricao, StatusAcesso
from apps.assinaturas.catalogs import PLANOS_BOOTSTRAP, CatalogoPlanos, sincronizar_planos
from apps.assinaturas.features import CATALOGO_RECURSOS, ValoresRecursos
from apps.assinaturas.models import Periodicidade, StatusAssinatura, StatusFinanceiro
from apps.assinaturas.subscriptions import Assinaturas, CriacaoAssinatura, OrigemVersaoPlano, TermosAssinatura
from apps.organizacoes.constants import META_HEADER_ORGANIZACAO
from apps.organizacoes.context import ContextoOrganizacao, organizacao_atual_privilegiada
from apps.organizacoes.memberships import OcupacaoSeats
from apps.organizacoes.middleware import OrganizacaoMiddleware
from apps.organizacoes.models import Organizacao, Papel, Vinculo
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db


def _payload(response):
    return json.loads(response.content)


def _criar_contrato(*, organizacao: Organizacao, seats: int = 3):
    sincronizar_planos(PLANOS_BOOTSTRAP, aplicar=True)
    versao, preco = CatalogoPlanos.obter_versao_inicial(codigo="profissional", periodicidade=Periodicidade.MENSAL)
    termos = TermosAssinatura(
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
    with organizacao_atual_privilegiada(organizacao.pk):
        return Assinaturas.criar(
            CriacaoAssinatura(
                organizacao=organizacao,
                origem=OrigemVersaoPlano(versao),
                termos=termos,
                status=StatusAssinatura.ATIVA,
                status_financeiro=StatusFinanceiro.REGULAR,
                politica_trial=None,
                trial_termina_em=None,
                chave_idempotencia=f"middleware:{organizacao.pk}",
            )
        )


def _request(*, organizacao: Organizacao, usuario, path: str = "/times/", method: str = "get"):
    request = getattr(APIRequestFactory(), method)(path, **{META_HEADER_ORGANIZACAO: organizacao.slug})
    request.user = usuario
    return request


def test_middleware_anexa_contexto_comercial_real_e_imutavel_uma_vez():
    usuario = criar_usuario(email="middleware-contexto@example.com")
    organizacao = Organizacao.objects.create(nome="Contexto comercial", slug="contexto-comercial")
    vinculo = Vinculo.objects.create(usuario=usuario, organizacao=organizacao, papel=Papel.PROPRIETARIO)
    assinatura = _criar_contrato(organizacao=organizacao, seats=3)
    observado = {}

    def responder(request):
        observado["tenant"] = request.tenant
        return HttpResponse()

    with CaptureQueriesContext(connection) as queries:
        response = OrganizacaoMiddleware(responder)(_request(organizacao=organizacao, usuario=usuario))

    assert response.status_code == 200
    tenant = observado["tenant"]
    assert tenant == ContextoOrganizacao(
        organizacao=organizacao,
        vinculo=vinculo,
        assinatura=assinatura,
        utilizacao_seats=Assinaturas.calcular_utilizacao(assinatura, OcupacaoSeats(consumidos=1, reservados=0)),
        situacao_acesso=tenant.situacao_acesso,
    )
    assert tenant.situacao_acesso.status == StatusAcesso.LIBERADO
    assert tenant.situacao_acesso.motivos == ()
    with pytest.raises(FrozenInstanceError):
        tenant.assinatura = None

    selects_assinatura = [query for query in queries if 'FROM "assinatura_organizacao"' in query["sql"]]
    selects_ocupacao = [query for query in queries if 'FROM "organizacao"' in query["sql"] and "COUNT(" in query["sql"]]
    assert len(selects_assinatura) == 1
    assert len(selects_ocupacao) == 1


def test_middleware_falha_fechado_para_organizacao_ativa_sem_assinatura():
    usuario = criar_usuario(email="middleware-sem-assinatura@example.com")
    organizacao = Organizacao.objects.create(nome="Sem assinatura", slug="sem-assinatura")
    Vinculo.objects.create(usuario=usuario, organizacao=organizacao, papel=Papel.PROPRIETARIO)
    chamada = False

    def responder(request):
        nonlocal chamada
        chamada = True
        return HttpResponse()

    response = OrganizacaoMiddleware(responder)(_request(organizacao=organizacao, usuario=usuario))

    assert response.status_code == 503
    assert _payload(response)["errors"][0]["code"] == "billing.subscription_required"
    assert chamada is False


def test_middleware_restringe_rota_comum_apos_carencia_de_seats():
    usuario = criar_usuario(email="middleware-restrito@example.com")
    outro = criar_usuario(email="middleware-restrito-outro@example.com")
    organizacao = Organizacao.objects.create(nome="Restrita", slug="restrita")
    Vinculo.objects.create(usuario=usuario, organizacao=organizacao, papel=Papel.PROPRIETARIO)
    Vinculo.objects.create(usuario=outro, organizacao=organizacao, papel=Papel.MEMBRO)
    assinatura = _criar_contrato(organizacao=organizacao, seats=1)
    inicio = timezone.now() - timedelta(days=8)
    with organizacao_atual_privilegiada(organizacao.pk):
        Assinaturas.reconciliar_carencia_seats(
            assinatura,
            OcupacaoSeats(consumidos=2, reservados=0),
            agora=inicio,
        )

    response = OrganizacaoMiddleware(lambda request: HttpResponse())(_request(organizacao=organizacao, usuario=usuario))

    assert response.status_code == 403
    erro = _payload(response)["errors"][0]
    assert erro["code"] == "billing.organization_restricted"
    assert erro["context"] == {
        "reasons": [MotivoRestricao.SEAT_OVERAGE_GRACE_PERIOD_EXPIRED],
        "regularize_by": (inicio + timedelta(days=7)).isoformat(),
    }


@override_settings(ROOT_URLCONF="apps.api.core.tests.test_route_markers")
@pytest.mark.parametrize(
    ("papel", "status_esperado"),
    [
        (Papel.MEMBRO, 403),
        (Papel.ADMINISTRADOR, 200),
        (Papel.PROPRIETARIO, 200),
    ],
)
def test_rota_marcada_regulariza_sem_ampliar_papel(papel, status_esperado):
    usuario = criar_usuario(email=f"middleware-regularizacao-{papel}@example.com")
    outro = criar_usuario(email=f"middleware-regularizacao-outro-{papel}@example.com")
    organizacao = Organizacao.objects.create(nome=f"Regularização {papel}", slug=f"regularizacao-{papel}")
    Vinculo.objects.create(usuario=usuario, organizacao=organizacao, papel=papel)
    Vinculo.objects.create(usuario=outro, organizacao=organizacao, papel=Papel.MEMBRO)
    assinatura = _criar_contrato(organizacao=organizacao, seats=1)
    with organizacao_atual_privilegiada(organizacao.pk):
        Assinaturas.reconciliar_carencia_seats(
            assinatura,
            OcupacaoSeats(consumidos=2, reservados=0),
            agora=timezone.now() - timedelta(days=8),
        )
    observado = {}

    def responder(request):
        observado["situacao"] = request.tenant.situacao_acesso
        return HttpResponse()

    response = OrganizacaoMiddleware(responder)(
        _request(
            organizacao=organizacao,
            usuario=usuario,
            path="/_test/regularizar/",
            method="post",
        )
    )

    assert response.status_code == status_esperado
    if status_esperado == 200:
        assert observado["situacao"].status == StatusAcesso.RESTRITO
    else:
        assert _payload(response)["errors"][0]["code"] == "billing.organization_restricted"
