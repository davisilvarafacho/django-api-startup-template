"""Concorrência real do cadastro automático pelo Google."""

import threading

from django.db import connections

from rest_framework.test import APIClient

import pytest

from apps.api.autenticacao.models import AuthToken, IdentidadeExterna
from apps.usuarios.models import Usuario

from .helpers import CLIENT_ID, claims_google, substituir_verificador


@pytest.mark.django_db(transaction=True)
def test_cadastros_concorrentes_do_mesmo_sub_convergem_para_uma_conta(settings):
    settings.GOOGLE_OAUTH_CLIENT_IDS = [CLIENT_ID]
    claims = claims_google(sub="sub-corrida", email="corrida@gmail.com")
    verificadores_prontos = threading.Barrier(2)
    resultados = []
    erros = []
    lock = threading.Lock()

    def verificar(token, request, audience):
        del token, request, audience
        verificadores_prontos.wait(timeout=10)
        return claims

    def cadastrar():
        try:
            response = APIClient().post("/auth/google/", {"id_token": "token-corrida"}, format="json")
            with lock:
                resultados.append(response.status_code)
        except Exception as exc:  # pragma: no cover - falha reapresentada na thread principal
            with lock:
                erros.append(exc)
        finally:
            connections.close_all()

    connections.close_all()
    with substituir_verificador(side_effect=verificar):
        threads = [threading.Thread(target=cadastrar) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

    assert all(not thread.is_alive() for thread in threads)
    assert erros == []
    assert sorted(resultados) == [200, 200]
    usuario = Usuario.objects.get(email="corrida@gmail.com")
    assert Usuario.objects.filter(email="corrida@gmail.com").count() == 1
    assert IdentidadeExterna.objects.filter(usuario=usuario, identificador="sub-corrida").count() == 1
    assert AuthToken.objects.filter(responsavel=usuario).count() == 2
