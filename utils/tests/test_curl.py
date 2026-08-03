import pytest
import requests

from utils.curl import as_curl


def make_response(method="GET", url="https://example.test/items", headers=None, data=None):
    request = requests.Request(method, url, headers=headers, data=data).prepare()
    response = requests.Response()
    response.request = request
    return response


def test_as_curl_reproduz_metodo_url_e_headers():
    response = make_response(headers={"Authorization": "Bearer secret", "X-Name": "O'Reilly"})

    assert as_curl(response) == "curl -X GET -H 'Authorization: Bearer secret' -H 'X-Name: O'\"'\"'Reilly' https://example.test/items"


def test_as_curl_inclui_corpo_da_requisicao():
    response = make_response(method="POST", data="nome=Ana Maria")

    assert as_curl(response) == "curl -X POST -H 'Content-Length: 14' --data-raw 'nome=Ana Maria' https://example.test/items"


@pytest.mark.parametrize("body", ["", b""])
def test_as_curl_inclui_argumento_vazio_quando_corpo_esta_presente(body):
    response = make_response(method="POST")
    response.request.body = body

    assert as_curl(response) == "curl -X POST -H 'Content-Length: 0' --data-raw '' https://example.test/items"
