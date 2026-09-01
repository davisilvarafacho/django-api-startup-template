"""Testes da camada HTTP de organizacoes."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

from django.db import close_old_connections, connections
from django.utils import timezone

from rest_framework import status
from rest_framework.test import APIClient

import pytest
from knox.models import get_token_model

from apps.api.autenticacao.models import TokenMetaData, TokenType
from apps.assinaturas.catalogs import PLANOS_BOOTSTRAP, CatalogoPlanos, sincronizar_planos
from apps.assinaturas.models import Periodicidade, StatusAssinatura, StatusFinanceiro
from apps.assinaturas.subscriptions import Assinaturas, CriacaoAssinatura, OrigemVersaoPlano
from apps.organizacoes.constants import META_HEADER_ORGANIZACAO
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Convite, Organizacao, Papel, Time, Vinculo
from internal_frameworks.context import ContextVariable
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db

AuthToken = get_token_model()


@pytest.fixture(autouse=True)
def configurar_request_de_teste(settings):
    settings.ALLOWED_HOSTS = ["testserver"]
    ContextVariable.clear_context()
    yield
    ContextVariable.clear_context()


def _garantir_assinaturas_correntes():
    sincronizar_planos(PLANOS_BOOTSTRAP, aplicar=True)
    versao, preco = CatalogoPlanos.obter_versao_inicial(codigo="profissional", periodicidade=Periodicidade.MENSAL)
    for organizacao in Organizacao.objects.filter(is_active=True).order_by("pk"):
        with organizacao_atual_privilegiada(organizacao.pk):
            if Assinaturas.obter_corrente(organizacao) is None:
                Assinaturas.criar(
                    CriacaoAssinatura(
                        organizacao=organizacao,
                        origem=OrigemVersaoPlano(versao),
                        termos=Assinaturas._termos_catalogo(versao, preco, seats_contratados=100),
                        status=StatusAssinatura.ATIVA,
                        status_financeiro=StatusFinanceiro.REGULAR,
                        politica_trial=None,
                        trial_termina_em=None,
                        chave_idempotencia=f"fixture-http-profissional:{organizacao.pk}",
                    )
                )


def client_autenticado(usuario):
    _garantir_assinaturas_correntes()
    _, token = AuthToken.objects.create(user=usuario)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def client_com_api_key(usuario, scopes, organizacao):
    _garantir_assinaturas_correntes()
    instance, token = AuthToken.objects.create(
        responsavel=usuario,
        type=TokenType.API_KEY,
        created_by=usuario,
        organization=organizacao,
        name="Integração de teste",
        scopes=scopes,
    )
    TokenMetaData.objects.create(token=instance)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def vincular(usuario, organizacao, papel=Papel.MEMBRO, times=()):
    vinculo = Vinculo.objects.create(usuario=usuario, organizacao=organizacao, papel=papel)
    if times:
        vinculo.times.set(times)
    return vinculo


def contratar(organizacao, *, seats, papeis_isentos=()):
    sincronizar_planos(PLANOS_BOOTSTRAP, aplicar=True)
    versao, preco = CatalogoPlanos.obter_versao_inicial(codigo="profissional", periodicidade=Periodicidade.MENSAL)
    with organizacao_atual_privilegiada(organizacao.pk):
        assinatura = Assinaturas.criar(
            CriacaoAssinatura(
                organizacao=organizacao,
                origem=OrigemVersaoPlano(versao),
                termos=Assinaturas._termos_catalogo(versao, preco, seats_contratados=seats),
                status=StatusAssinatura.ATIVA,
                status_financeiro=StatusFinanceiro.REGULAR,
                politica_trial=None,
                trial_termina_em=None,
                chave_idempotencia=f"contrato-http-{organizacao.pk}",
            )
        )
    if papeis_isentos:
        assinatura.recursos = {
            **assinatura.recursos,
            "papeis_isentos_seat": [int(papel) for papel in papeis_isentos],
        }
        assinatura.save(update_fields=["recursos"])
    return assinatura


def test_lista_apenas_organizacoes_do_usuario_sem_exigir_header():
    usuario = criar_usuario()
    outra_pessoa = criar_usuario()
    org_a = Organizacao.objects.create(nome="Org A", slug="org-a")
    org_b = Organizacao.objects.create(nome="Org B", slug="org-b")
    org_de_outra_pessoa = Organizacao.objects.create(nome="Org C", slug="org-c")
    vincular(usuario, org_a, Papel.PROPRIETARIO)
    vincular(usuario, org_b, Papel.MEMBRO)
    vincular(outra_pessoa, org_de_outra_pessoa, Papel.PROPRIETARIO)

    response = client_autenticado(usuario).get("/organizacoes/")

    assert response.status_code == status.HTTP_200_OK, response.content
    resultados = response.data["resultados"]
    assert {item["slug"] for item in resultados} == {"org-a", "org-b"}
    assert {item["slug"]: item["papel"] for item in resultados} == {
        "org-a": Papel.PROPRIETARIO,
        "org-b": Papel.MEMBRO,
    }


def test_cria_organizacao_e_vincula_usuario_como_proprietario(monkeypatch):
    from apps.organizacoes import onboarding

    class Catalogo:
        @classmethod
        def obter_versao_inicial(cls, **kwargs):
            return object(), object()

    class Assinaturas:
        @classmethod
        def criar_gratuita(cls, **kwargs):
            return object()

    monkeypatch.setattr(onboarding, "_carregar_colaboradores_comerciais", lambda: (Catalogo, Assinaturas))
    usuario = criar_usuario(email_verificado_em=timezone.now())

    response = client_autenticado(usuario).post(
        "/organizacoes/",
        {"nome": "Minha Empresa", "slug": "minha-empresa"},
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    organizacao = Organizacao.objects.get(slug="minha-empresa")
    vinculo = Vinculo.objects.get(organizacao=organizacao, usuario=usuario)
    assert response.data["slug"] == "minha-empresa"
    assert vinculo.papel == Papel.PROPRIETARIO


def test_times_sao_filtrados_pela_organizacao_do_header():
    usuario = criar_usuario()
    org_a = Organizacao.objects.create(nome="Org A", slug="org-a")
    org_b = Organizacao.objects.create(nome="Org B", slug="org-b")
    vincular(usuario, org_a, Papel.MEMBRO)
    vincular(usuario, org_b, Papel.MEMBRO)
    Time.objects.create(organizacao=org_a, nome="Produto", created_by=usuario)
    Time.objects.create(organizacao=org_b, nome="Financeiro", created_by=usuario)

    response = client_autenticado(usuario).get(
        "/times/",
        **{META_HEADER_ORGANIZACAO: "org-a"},
    )

    assert response.status_code == status.HTTP_200_OK
    assert [item["nome"] for item in response.data["resultados"]] == ["Produto"]


def test_vinculos_sao_filtrados_pela_organizacao_do_header():
    usuario = criar_usuario()
    colega = criar_usuario(first_name="Colega")
    pessoa_de_fora = criar_usuario(first_name="Fora")
    org_a = Organizacao.objects.create(nome="Org A", slug="org-a")
    org_b = Organizacao.objects.create(nome="Org B", slug="org-b")
    vincular(usuario, org_a, Papel.ADMINISTRADOR)
    vincular(colega, org_a, Papel.MEMBRO)
    vincular(usuario, org_b, Papel.MEMBRO)
    vincular(pessoa_de_fora, org_b, Papel.MEMBRO)

    response = client_autenticado(usuario).get(
        "/vinculos/",
        **{META_HEADER_ORGANIZACAO: "org-a"},
    )

    assert response.status_code == status.HTTP_200_OK
    assert {item["usuario"]["email"] for item in response.data["resultados"]} == {
        usuario.email,
        colega.email,
    }


def test_gestor_cria_convite_na_organizacao_do_header():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Org A", slug="org-a")
    vincular(usuario, organizacao, Papel.GESTOR)

    response = client_autenticado(usuario).post(
        "/convites/",
        {"email": "nova@example.com", "papel": Papel.MEMBRO},
        format="json",
        **{META_HEADER_ORGANIZACAO: "org-a"},
    )

    assert response.status_code == status.HTTP_201_CREATED
    convite = Convite.objects.get(email="nova@example.com")
    assert convite.organizacao == organizacao
    assert convite.convidado_por == usuario
    assert response.data["token"] == convite.token


def test_header_de_organizacao_ausente_retorna_422():
    usuario = criar_usuario()
    org_a = Organizacao.objects.create(nome="Org A", slug="org-a")
    vincular(usuario, org_a, Papel.MEMBRO)

    response = client_autenticado(usuario).get("/times/")

    assert response.status_code == 422
    assert response.json()["errors"][0]["code"] == "organizations.header_required"
    assert response.json()["errors"][0]["field"] == "X-Organization"


def test_usuario_sem_vinculo_ativo_na_organizacao_retorna_403():
    usuario = criar_usuario()
    Organizacao.objects.create(nome="Org A", slug="org-a")

    response = client_autenticado(usuario).get(
        "/times/",
        **{META_HEADER_ORGANIZACAO: "org-a"},
    )

    assert response.status_code == 403
    assert response.json()["errors"][0]["code"] == "organizations.membership_required"


def test_papel_insuficiente_para_criar_time_retorna_403():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Org A", slug="org-a")
    vincular(usuario, organizacao, Papel.MEMBRO)

    response = client_autenticado(usuario).post(
        "/times/",
        {"nome": "Produto"},
        format="json",
        **{META_HEADER_ORGANIZACAO: "org-a"},
    )

    assert response.status_code == 403
    assert response.data["errors"][0]["code"] == "organizations.role_insufficient"


def test_convite_com_papel_acima_do_proprio_e_recusado():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Org A", slug="org-a")
    vincular(usuario, organizacao, Papel.GESTOR)

    response = client_autenticado(usuario).post(
        "/convites/",
        {"email": "nova@example.com", "papel": Papel.PROPRIETARIO},
        format="json",
        **{META_HEADER_ORGANIZACAO: "org-a"},
    )

    assert response.status_code == 422
    assert response.data["errors"][0]["code"] == "organizations.role_insufficient"
    assert response.data["errors"][0]["field"] == "papel"


def test_patch_nao_pode_reativar_reserva_de_convite_sem_validar_capacidade():
    administrador = criar_usuario(email="admin-patch-expiracao@example.com")
    organizacao = Organizacao.objects.create(nome="Patch expiração", slug="patch-expiracao")
    vincular(administrador, organizacao, Papel.ADMINISTRADOR)
    contratar(organizacao, seats=1)
    expirou_em = timezone.now() - timedelta(days=1)
    convite = Convite.objects.create(
        organizacao=organizacao,
        email="reativar@example.com",
        papel=Papel.MEMBRO,
        expira_em=expirou_em,
    )

    response = client_autenticado(administrador).patch(
        f"/convites/{convite.pk}/",
        {"expira_em": (timezone.now() + timedelta(days=1)).isoformat()},
        format="json",
        **{META_HEADER_ORGANIZACAO: organizacao.slug},
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.data["errors"][0]["code"] == "billing.seat_limit_reached"
    convite.refresh_from_db()
    assert convite.expira_em == expirou_em


def test_patch_nao_pode_tornar_convite_ou_vinculo_cobravel_sem_validar_capacidade():
    administrador = criar_usuario(email="admin-patch-papel@example.com")
    membro = criar_usuario(email="membro-patch-papel@example.com")
    organizacao = Organizacao.objects.create(nome="Patch papel", slug="patch-papel")
    vincular(administrador, organizacao, Papel.ADMINISTRADOR)
    vinculo = vincular(membro, organizacao, Papel.MEMBRO)
    contratar(organizacao, seats=1, papeis_isentos=(Papel.MEMBRO,))
    convite = Convite.objects.create(
        organizacao=organizacao,
        email="papel-convite@example.com",
        papel=Papel.MEMBRO,
        expira_em=timezone.now() + timedelta(days=1),
    )
    client = client_autenticado(administrador)

    resposta_convite = client.patch(
        f"/convites/{convite.pk}/",
        {"papel": Papel.GESTOR},
        format="json",
        **{META_HEADER_ORGANIZACAO: organizacao.slug},
    )
    resposta_vinculo = client.patch(
        f"/vinculos/{vinculo.pk}/",
        {"papel": Papel.GESTOR},
        format="json",
        **{META_HEADER_ORGANIZACAO: organizacao.slug},
    )

    assert resposta_convite.status_code == status.HTTP_409_CONFLICT
    assert resposta_vinculo.status_code == status.HTTP_409_CONFLICT
    convite.refresh_from_db()
    vinculo.refresh_from_db()
    assert convite.papel == Papel.MEMBRO
    assert vinculo.papel == Papel.MEMBRO


@pytest.mark.django_db(transaction=True)
def test_patchs_concorrentes_de_convites_disputam_o_ultimo_seat_uma_unica_vez():
    administrador = criar_usuario(email="admin-patch-concorrente@example.com")
    organizacao = Organizacao.objects.create(nome="Patch concorrente", slug="patch-concorrente")
    vincular(administrador, organizacao, Papel.ADMINISTRADOR)
    contratar(organizacao, seats=2)
    expirou_em = timezone.now() - timedelta(days=1)
    convites = [
        Convite.objects.create(
            organizacao=organizacao,
            email=f"patch-concorrente-{numero}@example.com",
            papel=Papel.MEMBRO,
            expira_em=expirou_em,
        )
        for numero in (1, 2)
    ]
    clients = [client_autenticado(administrador), client_autenticado(administrador)]
    barreira = Barrier(2)
    nova_expiracao = (timezone.now() + timedelta(days=1)).isoformat()

    def reativar(indice):
        close_old_connections()
        try:
            barreira.wait(timeout=5)
            return (
                clients[indice]
                .patch(
                    f"/convites/{convites[indice].pk}/",
                    {"expira_em": nova_expiracao},
                    format="json",
                    **{META_HEADER_ORGANIZACAO: organizacao.slug},
                )
                .status_code
            )
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        resultados = list(executor.map(reativar, (0, 1)))

    assert sorted(resultados) == [status.HTTP_200_OK, status.HTTP_409_CONFLICT]
    assert Convite.objects.filter(organizacao=organizacao, expira_em__gt=timezone.now()).count() == 1


def test_aceitar_convite_com_token_invalido_retorna_422():
    usuario = criar_usuario()

    response = client_autenticado(usuario).post(
        "/convites/aceitar/",
        {"token": "token-que-nao-existe"},
        format="json",
    )

    assert response.status_code == 422
    assert response.data["errors"][0]["code"] == "organizations.invitation_invalid"
    assert response.data["errors"][0]["field"] == "token"


def test_aceitar_convite_ja_utilizado_retorna_422():
    usuario = criar_usuario(email="nova@example.com")
    organizacao = Organizacao.objects.create(nome="Org A", slug="org-a")
    convite = Convite.objects.create(
        organizacao=organizacao,
        email=usuario.email,
        papel=Papel.MEMBRO,
        expira_em=timezone.now() + timedelta(days=1),
        aceito_em=timezone.now(),
    )

    response = client_autenticado(usuario).post(
        "/convites/aceitar/",
        {"token": convite.token},
        format="json",
    )

    assert response.status_code == 422
    assert response.data["errors"][0]["code"] == "organizations.invitation_expired"


def test_aceitar_convite_de_outro_email_retorna_422():
    usuario = criar_usuario(email="usuario@example.com")
    organizacao = Organizacao.objects.create(nome="Org A", slug="org-a")
    convite = Convite.objects.create(
        organizacao=organizacao,
        email="outro@example.com",
        papel=Papel.MEMBRO,
        expira_em=timezone.now() + timedelta(days=1),
    )

    response = client_autenticado(usuario).post(
        "/convites/aceitar/",
        {"token": convite.token},
        format="json",
    )

    assert response.status_code == 422
    assert response.data["errors"][0]["code"] == "organizations.invitation_email_mismatch"


def test_usuario_convidado_aceita_convite_sem_header_de_organizacao():
    usuario = criar_usuario(email="nova@example.com")
    organizacao = Organizacao.objects.create(nome="Org A", slug="org-a")
    convite = Convite.objects.create(
        organizacao=organizacao,
        email=usuario.email,
        papel=Papel.MEMBRO,
        expira_em=timezone.now() + timedelta(days=1),
    )

    response = client_autenticado(usuario).post(
        "/convites/aceitar/",
        {"token": convite.token},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert Vinculo.objects.filter(usuario=usuario, organizacao=organizacao, papel=Papel.MEMBRO).exists()
    convite.refresh_from_db()
    assert convite.aceito_em is not None


# ---- scopes públicos (resource:action) derivados dos models ----


def test_api_key_com_scope_teams_read_pode_listar_times():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Org A", slug="org-a")
    vincular(usuario, organizacao, Papel.MEMBRO)

    response = client_com_api_key(usuario, ["teams:read"], organizacao).get(
        "/times/",
        **{META_HEADER_ORGANIZACAO: "org-a"},
    )

    assert response.status_code == status.HTTP_200_OK


def test_api_key_sem_scope_teams_read_e_recusada():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Org A", slug="org-a")
    vincular(usuario, organizacao, Papel.MEMBRO)

    response = client_com_api_key(usuario, ["organizations:read"], organizacao).get(
        "/times/",
        **{META_HEADER_ORGANIZACAO: "org-a"},
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.data["errors"][0]["code"] == "auth.insufficient_scope"


def test_api_key_precisa_do_scope_invitations_accept_para_aceitar_convite():
    usuario = criar_usuario(email="precisa-scope@example.com")
    organizacao = Organizacao.objects.create(nome="Org A", slug="org-a")
    vincular(usuario, organizacao)
    convite = Convite.objects.create(
        organizacao=organizacao,
        email=usuario.email,
        papel=Papel.MEMBRO,
        expira_em=timezone.now() + timedelta(days=1),
    )

    response = client_com_api_key(usuario, ["teams:read"], organizacao).post(
        "/convites/aceitar/",
        {"token": convite.token},
        format="json",
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.data["errors"][0]["code"] == "auth.insufficient_scope"


def test_api_key_com_scope_invitations_accept_aceita_convite():
    usuario = criar_usuario(email="com-scope@example.com")
    organizacao = Organizacao.objects.create(nome="Org A", slug="org-a")
    vincular(usuario, organizacao)
    convite = Convite.objects.create(
        organizacao=organizacao,
        email=usuario.email,
        papel=Papel.MEMBRO,
        expira_em=timezone.now() + timedelta(days=1),
    )

    response = client_com_api_key(usuario, ["invitations:accept"], organizacao).post(
        "/convites/aceitar/",
        {"token": convite.token},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK


def test_delete_de_time_oculta_registro_sem_remover_linha():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Org A", slug="org-a")
    vincular(usuario, organizacao, Papel.GESTOR)
    time = Time.objects.create(organizacao=organizacao, nome="Produto")

    response = client_autenticado(usuario).delete(f"/times/{time.pk}/", **{META_HEADER_ORGANIZACAO: "org-a"})

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert not Time.objects.filter(pk=time.pk).exists()
    assert Time.all_objects.get(pk=time.pk).is_deleted is True


def test_delete_de_vinculo_oculta_registro_sem_remover_linha():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Org A", slug="org-a")
    vincular(usuario, organizacao, Papel.ADMINISTRADOR)
    alvo = vincular(criar_usuario(), organizacao)

    response = client_autenticado(usuario).delete(f"/vinculos/{alvo.pk}/", **{META_HEADER_ORGANIZACAO: "org-a"})

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert Vinculo.all_objects.get(pk=alvo.pk).is_deleted is True


def test_delete_de_convite_oculta_registro_sem_remover_linha():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Org A", slug="org-a")
    vincular(usuario, organizacao, Papel.GESTOR)
    convite = Convite.objects.create(organizacao=organizacao, email="nova@example.com", convidado_por=usuario)

    response = client_autenticado(usuario).delete(f"/convites/{convite.pk}/", **{META_HEADER_ORGANIZACAO: "org-a"})

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert Convite.all_objects.get(pk=convite.pk).is_deleted is True
