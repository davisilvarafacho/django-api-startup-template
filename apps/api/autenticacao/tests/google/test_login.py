"""Cadastro e login pelo identificador imutável do Google."""

from django.utils import timezone

from rest_framework.test import APIClient

import pytest

from apps.api.autenticacao.models import AuthToken, IdentidadeExterna, ProvedorIdentidade, TokenType
from apps.usuarios.models import Usuario
from tests.support.usuarios import criar_usuario

from .helpers import CLIENT_ID, claims_google, substituir_verificador

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _configurar_client_google(settings):
    settings.GOOGLE_OAUTH_CLIENT_IDS = [CLIENT_ID]


def test_login_google_e_publico_e_valida_o_payload():
    response = APIClient().post("/auth/google/", {}, format="json")

    assert response.status_code == 422
    assert response.data["errors"][0]["code"] == "validation.required"


def test_cadastro_google_cria_conta_vinculo_e_sessao_sem_persistir_payload():
    claims = claims_google(
        sub="novo-sub",
        email="nova@gmail.com",
        access_token="access-token-que-nao-pode-ser-salvo",
        refresh_token="refresh-token-que-nao-pode-ser-salvo",
        raw="payload-que-nao-pode-ser-salvo",
    )

    with substituir_verificador(claims=claims):
        response = APIClient().post(
            "/auth/google/",
            {"id_token": "id-token-puro", "device_name": "Notebook"},
            format="json",
        )

    assert response.status_code == 200
    assert set(response.data) == {"token", "expiry", "session"}
    usuario = Usuario.objects.get(email__iexact="nova@gmail.com")
    assert usuario.first_name == "Ada"
    assert usuario.last_name == "Lovelace"
    assert usuario.has_usable_password() is False
    assert usuario.email_verificado_em is not None

    identidade = IdentidadeExterna.objects.get(usuario=usuario)
    assert identidade.provedor == ProvedorIdentidade.GOOGLE
    assert identidade.identificador == "novo-sub"
    valores_persistidos = repr(identidade.__dict__)
    for segredo in ("id-token-puro", claims["email"], claims["picture"], claims["access_token"], claims["refresh_token"], claims["raw"]):
        assert segredo not in valores_persistidos

    token = AuthToken.objects.get(responsavel=usuario)
    assert token.type == TokenType.TOKEN
    assert token.metadata.device_name == "Notebook"
    assert response.data["session"]["uuid"] == str(token.uuid)


def test_sub_conhecido_entra_na_conta_vinculada_sem_usar_email_do_token():
    usuario = criar_usuario(email="local@example.com")
    IdentidadeExterna.objects.create(usuario=usuario, provedor=ProvedorIdentidade.GOOGLE, identificador="sub-conhecido")
    claims = claims_google(sub="sub-conhecido", email="email-alterado-no-google@example.net")

    with substituir_verificador(claims=claims):
        response = APIClient().post("/auth/google/", {"id_token": "token-conhecido"}, format="json")

    assert response.status_code == 200
    usuario.refresh_from_db()
    assert usuario.email == "local@example.com"
    assert AuthToken.objects.filter(responsavel=usuario, type=TokenType.TOKEN).count() == 1
    token = AuthToken.objects.get(responsavel=usuario)
    assert token.metadata.token_id == token.pk


def test_email_local_existente_nunca_e_vinculado_implicitamente():
    usuario = criar_usuario(email="Mesmo@Example.com")
    claims = claims_google(sub="sub-novo", email="mesmo@example.com")

    with substituir_verificador(claims=claims):
        response = APIClient().post("/auth/google/", {"id_token": "token-conflitante"}, format="json")

    assert response.status_code == 409
    assert response.data["errors"][0]["code"] == "account.external_identity_conflict"
    assert IdentidadeExterna.objects.filter(usuario=usuario).exists() is False
    assert AuthToken.objects.filter(responsavel=usuario).exists() is False


def test_email_soft_deleted_nao_anonimizado_impede_novo_cadastro():
    usuario = criar_usuario(email="excluida@example.com")
    usuario.delete()
    claims = claims_google(sub="sub-posterior", email="excluida@example.com")

    with substituir_verificador(claims=claims):
        response = APIClient().post("/auth/google/", {"id_token": "token-posterior"}, format="json")

    assert response.status_code == 409
    assert response.data["errors"][0]["code"] == "account.external_identity_conflict"
    assert Usuario.all_objects.filter(email__iexact="excluida@example.com").count() == 1
    assert IdentidadeExterna.objects.filter(provedor=ProvedorIdentidade.GOOGLE, identificador="sub-posterior").exists() is False
    assert AuthToken.objects.exists() is False


@pytest.mark.parametrize(
    ("email", "hd", "espera_verificado"),
    [
        ("pessoa@gmail.com", None, True),
        ("pessoa@empresa.example", "empresa.example", True),
        ("pessoa@provedor.example", None, False),
    ],
)
def test_confianca_do_google_define_verificacao_local(email, hd, espera_verificado):
    sufixo = email.split("@", 1)[0] + email.split("@", 1)[1].split(".", 1)[0]
    kwargs = {"sub": f"sub-{sufixo}", "email": email}
    if hd is not None:
        kwargs["hd"] = hd
    claims = claims_google(**kwargs)

    with substituir_verificador(claims=claims):
        response = APIClient().post("/auth/google/", {"id_token": f"token-{sufixo}"}, format="json")

    assert response.status_code == 200
    usuario = Usuario.objects.get(email__iexact=email)
    assert (usuario.email_verificado_em is not None) is espera_verificado


@pytest.mark.parametrize("estado", ["inativo", "excluido", "exclusao_agendada"])
def test_sub_conhecido_nao_emite_sessao_para_conta_indisponivel(estado):
    usuario = criar_usuario(email=f"{estado}@example.com")
    IdentidadeExterna.objects.create(usuario=usuario, provedor=ProvedorIdentidade.GOOGLE, identificador=f"sub-{estado}")
    if estado == "inativo":
        usuario.is_active = False
        usuario.save(update_fields=["is_active"])
    elif estado == "excluido":
        usuario.delete()
    else:
        usuario.exclusao_agendada_para = timezone.now()
        usuario.save(update_fields=["exclusao_agendada_para"])

    with substituir_verificador(claims=claims_google(sub=f"sub-{estado}", email=usuario.email)):
        response = APIClient().post("/auth/google/", {"id_token": f"token-{estado}"}, format="json")

    assert response.status_code == 401
    assert response.data["errors"][0]["code"] == "auth.user_inactive"
    assert AuthToken.objects.filter(responsavel_id=usuario.pk).exists() is False


@pytest.mark.parametrize("mutacao", ["sem_sub", "sem_email", "email_nao_verificado"])
def test_claims_obrigatorias_invalidas_tem_erro_uniforme(mutacao):
    claims = claims_google(sub=f"sub-{mutacao}", email=f"{mutacao}@example.com")
    if mutacao == "sem_sub":
        claims.pop("sub")
    elif mutacao == "sem_email":
        claims.pop("email")
    else:
        claims["email_verified"] = False

    with substituir_verificador(claims=claims):
        response = APIClient().post("/auth/google/", {"id_token": "token-malformado"}, format="json")

    assert response.status_code == 401
    assert response.data["errors"][0]["code"] == "auth.google_token_invalid"
    assert Usuario.objects.count() == 0


def test_falha_do_verificador_tem_o_mesmo_erro_publico():
    with substituir_verificador(side_effect=ValueError("assinatura, issuer, expiração ou audience inválidos")):
        response = APIClient().post("/auth/google/", {"id_token": "token-invalido"}, format="json")

    assert response.status_code == 401
    assert response.data["errors"][0]["code"] == "auth.google_token_invalid"


def test_cada_client_id_configurado_pode_validar_a_audience(settings):
    primeiro = "android-client.apps.googleusercontent.com"
    segundo = "ios-client.apps.googleusercontent.com"
    settings.GOOGLE_OAUTH_CLIENT_IDS = [primeiro, segundo]
    claims = claims_google(sub="sub-ios", email="ios@gmail.com", audience=segundo)

    def verificar(token, request, audience):
        del token, request
        if audience != segundo:
            raise ValueError("audience diferente")
        return claims

    with substituir_verificador(side_effect=verificar):
        response = APIClient().post("/auth/google/", {"id_token": "token-ios"}, format="json")

    assert response.status_code == 200
    assert Usuario.objects.filter(email="ios@gmail.com").exists()
