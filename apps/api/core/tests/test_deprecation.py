from django.http import HttpResponse

import pytest

from apps.api.core.deprecation import api_deprecated


def decorar(**overrides):
    parametros = {
        "since": "2026-08-01",
        "sunset": "2026-11-01",
        "documentation": "/docs/deprecations/usuarios/",
        "replacement": "/api/v2/usuarios/",
    }
    parametros.update(overrides)
    return api_deprecated(**parametros)


def test_decorator_adiciona_headers_e_preserva_link_existente():
    @decorar()
    def handler():
        response = HttpResponse(status=200)
        response["Link"] = '</api/schema/>; rel="service-desc"'
        return response

    response = handler()

    assert response["Deprecation"] == "@1785542400"
    assert response["Sunset"] == "Sun, 01 Nov 2026 00:00:00 GMT"
    assert response["Link"] == (
        '</api/schema/>; rel="service-desc", '
        '</docs/deprecations/usuarios/>; rel="deprecation"; type="text/html"'
    )


def test_decorator_nao_duplica_link_de_deprecacao():
    link = '</docs/deprecations/usuarios/>; rel="deprecation"; type="text/html"'

    @decorar()
    def handler():
        response = HttpResponse(status=200)
        response["Link"] = link
        return response

    assert handler()["Link"] == link


@pytest.mark.parametrize(
    ("campo", "valor", "mensagem"),
    [
        ("since", "01/08/2026", "since"),
        ("sunset", "2026-10-29", "90 dias"),
        ("documentation", "docs/deprecations/usuarios", "documentation"),
        ("documentation", "ftp://example.com/guia", "documentation"),
        ("replacement", "//example.com/api/v2", "replacement"),
    ],
)
def test_decorator_rejeita_configuracao_invalida(campo, valor, mensagem):
    with pytest.raises(ValueError, match=mensagem):
        decorar(**{campo: valor})


def test_decorator_rejeita_aplicacao_duplicada():
    primeiro = decorar()

    with pytest.raises(ValueError, match="mais de uma vez"):
        decorar()(primeiro(lambda: HttpResponse()))


def test_retorno_invalido_fica_para_validacao_do_drf():
    @decorar()
    def handler():
        return {"invalido": True}

    assert handler() == {"invalido": True}
