"""Testes do cliente e do validator HaveIBeenPwned.

Nenhum teste toca a rede nem o banco: `httpx.get` é sempre substituído.
"""

import hashlib

from django.core.exceptions import ValidationError

import httpx
import pytest

from apps.usuarios.password_validation import PwnedPasswordsClient, PwnedPasswordValidator

SENHA = "senha-de-teste"
DIGEST = hashlib.sha1(SENHA.encode("utf-8"), usedforsecurity=False).hexdigest().upper()
PREFIXO, SUFIXO = DIGEST[:5], DIGEST[5:]


def responder(monkeypatch, *, text="", status_code=200, capturar=None, erro=None):
    """Substitui `httpx.get` por uma resposta fixa ou por uma exceção."""

    def fake_get(url, headers, timeout):
        if capturar is not None:
            capturar.update(url=url, headers=headers, timeout=timeout)
        if erro is not None:
            raise erro
        return httpx.Response(status_code, request=httpx.Request("GET", url), text=text)

    monkeypatch.setattr(httpx, "get", fake_get)


def cliente():
    return PwnedPasswordsClient(base_url="https://example.test/range", timeout=0.1)


def test_cliente_envia_apenas_prefixo_e_padding(monkeypatch):
    capturado = {}
    responder(monkeypatch, text=f"{SUFIXO}:4\r\n", capturar=capturado)

    cliente().is_pwned(SENHA)

    assert capturado["headers"]["Add-Padding"] == "true"
    assert capturado["url"].rsplit("/", 1)[-1] == PREFIXO
    assert len(capturado["url"].rsplit("/", 1)[-1]) == 5
    assert SENHA not in capturado["url"]
    assert SUFIXO not in capturado["url"]
    assert capturado["timeout"] == 0.1


def test_cliente_detecta_senha_vazada(monkeypatch):
    responder(monkeypatch, text=f"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA:2\r\n{SUFIXO}:117\r\n")

    assert cliente().is_pwned(SENHA) is True


def test_cliente_ignora_sufixo_ausente(monkeypatch):
    responder(monkeypatch, text="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA:2\r\n")

    assert cliente().is_pwned(SENHA) is False


def test_cliente_descarta_contagem_zero_do_padding(monkeypatch):
    responder(monkeypatch, text=f"{SUFIXO}:0\r\n")

    assert cliente().is_pwned(SENHA) is False


@pytest.mark.parametrize(
    "erro",
    [
        httpx.TimeoutException("timeout"),
        httpx.ConnectError("sem rede"),
        httpx.HTTPError("falha generica"),
    ],
)
def test_cliente_e_fail_open_em_falha_de_rede(monkeypatch, erro):
    responder(monkeypatch, erro=erro)

    assert cliente().is_pwned(SENHA) is False


def test_cliente_e_fail_open_em_erro_http(monkeypatch):
    responder(monkeypatch, status_code=500, text="erro")

    assert cliente().is_pwned(SENHA) is False


def test_cliente_e_fail_open_em_payload_malformado(monkeypatch):
    responder(monkeypatch, text="lixo sem dois pontos\r\noutra:linha:ruim\r\n")

    assert cliente().is_pwned(SENHA) is False


def test_validator_recusa_qualquer_ocorrencia(monkeypatch):
    monkeypatch.setattr(PwnedPasswordsClient, "is_pwned", lambda self, password: True)

    with pytest.raises(ValidationError, match="vazamentos") as excinfo:
        PwnedPasswordValidator(enabled=True).validate(SENHA)

    assert excinfo.value.code == "auth.pwned_password"


def test_validator_aceita_senha_nao_vazada(monkeypatch):
    monkeypatch.setattr(PwnedPasswordsClient, "is_pwned", lambda self, password: False)

    assert PwnedPasswordValidator(enabled=True).validate(SENHA) is None


def test_validator_desligado_nao_consulta_a_api(monkeypatch):
    def falhar(self, password):
        raise AssertionError("não deve consultar a API quando desligado")

    monkeypatch.setattr(PwnedPasswordsClient, "is_pwned", falhar)

    assert PwnedPasswordValidator(enabled=False).validate(SENHA) is None


def test_validator_segue_o_settings_quando_nao_recebe_flag(monkeypatch, settings):
    """Sem `enabled` explícito, quem manda é `HIBP_PASSWORD_CHECK_ENABLED`."""
    monkeypatch.setattr(PwnedPasswordsClient, "is_pwned", lambda self, password: True)

    settings.HIBP_PASSWORD_CHECK_ENABLED = False
    assert PwnedPasswordValidator().validate(SENHA) is None

    settings.HIBP_PASSWORD_CHECK_ENABLED = True
    with pytest.raises(ValidationError):
        PwnedPasswordValidator().validate(SENHA)


def test_checagem_desligada_por_padrao_em_teste(settings):
    """A suíte nunca pode depender de rede."""
    assert settings.HIBP_PASSWORD_CHECK_ENABLED is False


def test_validator_tem_help_text():
    assert PwnedPasswordValidator().get_help_text()
