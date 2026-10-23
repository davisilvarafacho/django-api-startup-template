import pytest

pytestmark = pytest.mark.django_db


def test_verificacao_de_email_publica_rejeita_token_invalido_sem_autenticacao(api_client):
    """Evita que a rota pública fique protegida pelo fallback de autenticação."""
    response = api_client.post("/auth/email/verify/", {"token": "invalido"}, format="json")

    assert response.status_code == 400
    assert response.json()["errors"][0]["code"] == "account.email_verification_invalid"
